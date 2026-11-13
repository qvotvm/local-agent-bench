"""Offline integration tests: python3 -m unittest discover -s scorer -v."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    from . import run, score
except ImportError:
    import run
    import score

ROOT = Path(__file__).resolve().parent.parent
ANSWER = {'values': {'launches': 5934}, 'status': 'ok',
          'citations': ['2026-10-02/pons/window_counts/launches'], 'text': 'observed launches.'}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.out = self.directory / 'answers.jsonl'
        self.requests = []
        self.responses = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append({'path': self.path, 'headers': dict(self.headers), 'payload': payload})
                if getattr(owner, 'trickle', None):
                    try:
                        if owner.trickle == 'headers':
                            content = b'HTTP/1.1 200 OK\r\nX-Slow: ' + b'x' * 100
                        else:
                            self.send_response(200)
                            if owner.trickle == 'chunked':
                                self.send_header('Transfer-Encoding', 'chunked')
                            self.end_headers()
                            content = b'F' * 100 if owner.trickle == 'chunked' else b' ' * 100
                        for byte in content:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.02)
                    except OSError:
                        pass
                    return
                item = owner.responses.pop(0) if owner.responses else json.dumps(ANSWER)
                if isinstance(item, tuple):
                    code, content = item
                else:
                    code, content = 200, json.dumps({'choices': [{'message': {'content': item}, 'finish_reason': 'stop'}],
                                                     'usage': {'prompt_tokens': 12, 'completion_tokens': 9}})
                body = content.encode()
                self.send_response(code)
                if 300 <= code < 400:
                    self.send_header('Location', owner.base_url + '/redirected')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.base_url = f'http://127.0.0.1:{self.server.server_port}/v1'

    def invoke(self, *options):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ['--base-url', self.base_url, '--model', 'fake/model:4B', '--out', str(self.out), '--limit', '1', *options]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = run.main(argv)
        self.assertEqual(code, 0, stderr.getvalue())
        return score.rows(self.out), stdout.getvalue() + stderr.getvalue()

    def test_normal_json_and_defaults(self):
        rows, _ = self.invoke()
        self.assertEqual(rows[0]['answer'], ANSWER)
        self.assertEqual(rows[0]['finish_reason'], 'stop')
        self.assertEqual(rows[0]['prompt_tokens'], 12)
        self.assertEqual(rows[0]['completion_tokens'], 9)
        self.assertGreaterEqual(rows[0]['latency_s'], 0)
        request = self.requests[0]
        self.assertEqual(request['path'], '/v1/chat/completions')
        self.assertEqual(request['payload'], {'model': 'fake/model:4B', 'messages': [{'role': 'user', 'content': score.rows(ROOT / 'data/prompts.jsonl')[0]['prompt']}], 'temperature': 0, 'max_tokens': 800})

    def test_think_wrapper(self):
        self.responses = ['<think>private reasoning\nsecond line</think>' + json.dumps(ANSWER)]
        self.assertEqual(self.invoke()[0][0]['answer'], ANSWER)

    def test_code_fences(self):
        self.responses = ['```json\n' + json.dumps(ANSWER) + '\n```']
        self.assertEqual(self.invoke()[0][0]['answer'], ANSWER)

    def test_think_then_unlabelled_fence(self):
        self.responses = ['<think>reason</think>\n```\n' + json.dumps(ANSWER) + '\n```']
        self.assertEqual(self.invoke()[0][0]['answer'], ANSWER)

    def test_invalid_json_error_row(self):
        self.responses = ['not JSON']
        row = self.invoke()[0][0]
        self.assertEqual(row['error'], 'invalid_json')
        self.assertNotIn('answer', row)
        self.assertEqual(row['finish_reason'], 'stop')
        self.assertEqual(len(self.requests), 1)

    def test_invalid_response_envelope(self):
        self.responses = [(200, '{broken envelope')]
        self.assertEqual(self.invoke()[0][0]['error'], 'invalid_json')

    def test_deep_json_does_not_abort_later_prompts(self):
        nested = '[' * 20000 + '0' + ']' * 20000
        for content in (nested, json.dumps({'choices': [{'message': {'content': nested}}]})):
            with self.subTest(envelope=content == nested):
                self.out.unlink(missing_ok=True)
                self.responses = [(200, content)]
                rows, _ = self.invoke('--limit', '2')
                self.assertEqual(rows[0]['error'], 'invalid_json')
                self.assertEqual(rows[1]['answer'], ANSWER)

    def test_surrogate_json_is_an_error_row(self):
        self.responses = [json.dumps(dict(ANSWER, text='\ud800'))]
        rows, _ = self.invoke('--limit', '2')
        self.assertEqual(rows[0]['error'], 'invalid_json')
        self.assertEqual(rows[1]['answer'], ANSWER)

    def test_response_size_limit(self):
        self.responses = [(200, ' ' * (run.MAX_RESPONSE_BYTES + 1))]
        self.assertEqual(self.invoke()[0][0]['error'], 'response_too_large')

    def test_trickled_headers_body_and_chunk_framing_obey_deadline(self):
        for kind in ('headers', 'body', 'chunked'):
            with self.subTest(kind=kind):
                self.out.unlink(missing_ok=True)
                self.trickle = kind
                started = time.monotonic()
                rows, _ = self.invoke('--timeout', '0.15', '--retries', '0')
                self.assertEqual(rows[0]['error'], 'request_failed')
                self.assertLess(time.monotonic() - started, 1.0)

    def test_malformed_envelope_structure(self):
        self.responses = [(200, json.dumps({'choices': ['invalid choice']}))]
        self.assertEqual(self.invoke()[0][0]['error'], 'invalid_json')

    def test_default_runs_all_200_prompts(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(['--base-url', self.base_url, '--model', 'test-all', '--out', str(self.out)]), 0)
        self.assertEqual(len(self.requests), 200)
        self.assertEqual([row['id'] for row in score.rows(self.out)], [row['id'] for row in score.rows(ROOT / 'data/prompts.jsonl')])

    def test_non_object_json(self):
        self.responses = ['[1, 2]']
        self.assertEqual(self.invoke()[0][0]['error'], 'invalid_answer')

    def test_http_500_retry_then_success(self):
        self.responses = [(500, 'private provider error'), json.dumps(ANSWER)]
        with patch.object(run.time, 'sleep'):
            self.assertEqual(self.invoke()[0][0]['answer'], ANSWER)
        self.assertEqual(len(self.requests), 2)

    def test_http_500_bounded_retries_and_task_limit(self):
        self.responses = [(500, 'private error')] * 8
        with patch.object(run.time, 'sleep'):
            row = self.invoke('--retries', '2')[0][0]
        self.assertEqual(row['error'], 'http_500')
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(len(score.rows(self.out)), 1)
        self.assertNotIn('private error', self.out.read_text())

    def test_http_400_not_retried(self):
        self.responses = [(400, 'private error')]
        self.assertEqual(self.invoke()[0][0]['error'], 'http_400')
        self.assertEqual(len(self.requests), 1)

    def test_transport_failure_is_bounded(self):
        with patch.object(run.urllib.request.OpenerDirector, 'open', side_effect=TimeoutError('private timeout')), patch.object(run.time, 'sleep'):
            self.assertEqual(self.invoke('--retries', '1')[0][0]['error'], 'request_failed')
        self.assertEqual(len(self.requests), 0)
        self.assertNotIn('private timeout', self.out.read_text())

    def test_resume_skips_existing_ids_including_errors(self):
        self.responses = ['invalid']
        first, _ = self.invoke()
        rows, _ = self.invoke('--resume')
        self.assertEqual([r['id'] for r in rows], ['t001', 't002'])
        self.assertEqual(rows[0], first[0])
        self.assertEqual(len(self.requests), 2)

    def test_resume_imported_file_without_metadata(self):
        self.out.write_text(json.dumps({'id': 't001', 'answer': ANSWER}) + '\n')
        self.assertEqual([r['id'] for r in self.invoke('--resume')[0]], ['t001', 't002'])

    def test_existing_output_protected_and_resume_settings_checked(self):
        self.invoke()
        saved = self.out.read_bytes()
        for extra in ([], ['--resume', '--temperature', '0.7']):
            with redirect_stderr(io.StringIO()):
                status = run.main(['--base-url', self.base_url, '--model', 'fake/model:4B', '--out', str(self.out), *extra])
            self.assertEqual(status, 1)
            self.assertEqual(saved, self.out.read_bytes())

    def test_limit_zero(self):
        self.assertEqual(self.invoke('--limit', '0')[0], [])
        self.assertEqual(self.requests, [])

    def test_key_never_written_even_when_echoed(self):
        key = 'secret-test-credential-123'
        self.responses = [json.dumps(dict(ANSWER, text=key))]
        with patch.dict(run.os.environ, {'BENCH_TEST_KEY': key}):
            rows, logs = self.invoke('--api-key-env', 'BENCH_TEST_KEY')
        self.assertEqual(self.requests[0]['headers']['Authorization'], 'Bearer ' + key)
        self.assertEqual(rows[0]['error'], 'secret_in_response')
        for text in (logs, self.out.read_text(), Path(str(self.out) + '.meta.json').read_text()):
            self.assertNotIn(key, text)
            self.assertNotIn('Authorization', text)

    def test_escaped_key_never_written(self):
        key = 'secret-test-credential-123'
        escaped = ''.join('\\u%04x' % ord(c) for c in key)
        self.responses = [json.dumps(dict(ANSWER, text=key)).replace(key, escaped)]
        with patch.dict(run.os.environ, {'BENCH_TEST_KEY': key}):
            rows, _ = self.invoke('--api-key-env', 'BENCH_TEST_KEY')
        self.assertEqual(rows[0]['error'], 'secret_in_response')
        self.assertNotIn(key, self.out.read_text())

    def test_query_never_written(self):
        self.base_url += '?credential=query-private-value'
        self.responses = [json.dumps(dict(ANSWER, text='credential=query-private-value'))]
        rows, logs = self.invoke()
        self.assertEqual(rows[0]['error'], 'secret_in_response')
        self.assertIn('?credential=query-private-value', self.requests[0]['path'])
        for text in (self.out.read_text(), Path(str(self.out) + '.meta.json').read_text(), logs):
            self.assertNotIn('query-private-value', text)
            self.assertNotIn(self.base_url, text)

    def test_redirect_not_followed(self):
        # Exercise the redirect handler directly, without a second destination.
        self.assertIsNone(run.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.invalid'))

    def test_http_redirects_do_not_send_a_second_request(self):
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                self.out.unlink(missing_ok=True)
                self.responses = [(code, 'redirect body')]
                count = len(self.requests)
                rows, _ = self.invoke()
                self.assertEqual(rows[0]['error'], f'http_{code}')
                self.assertEqual(len(self.requests), count + 1)

    def test_summary_confines_crafted_model_names(self):
        self.invoke()
        Path(str(self.out) + '.meta.json').unlink()
        fake_root = self.directory / 'benchmark'
        shutil.copytree(ROOT / 'data', fake_root / 'data')
        (fake_root / 'scorer').mkdir()
        shutil.copyfile(ROOT / 'scorer/score.py', fake_root / 'scorer/score.py')
        for model in ('../../escaped', '/absolute/name', '..', 'x\\..\\y', 'a/b:9b', '%2fescape'):
            with self.subTest(model=model), patch.object(run, 'ROOT', fake_root), redirect_stdout(io.StringIO()):
                self.assertEqual(run.main(['--summarize', str(self.out), '--model', model,
                                           '--hardware', 'test', '--quant', 'test', '--runtime', 'test']), 0)
                destination = fake_root / 'results/community' / (run.slug(model) + '.json')
                self.assertEqual(destination.resolve().parent, (fake_root / 'results/community').resolve())
                self.assertEqual(json.loads(destination.read_text())['model'], model)

    def test_summary_schema_hashes_slug_and_settings(self):
        self.invoke('--temperature', '0.3', '--max-tokens', '37')
        fake_root = self.directory / 'benchmark'
        shutil.copytree(ROOT / 'data', fake_root / 'data')
        (fake_root / 'scorer').mkdir()
        shutil.copyfile(ROOT / 'scorer/score.py', fake_root / 'scorer/score.py')
        with patch.object(run, 'ROOT', fake_root), redirect_stdout(io.StringIO()):
            status = run.main(['--summarize', str(self.out), '--model', 'fake/model:4B', '--hardware', 'test machine', '--quant', '4-bit', '--runtime', 'fake server'])
        self.assertEqual(status, 0)
        destination = fake_root / 'results/community/fake-model-4b.json'
        result = json.loads(destination.read_text())
        self.assertEqual(set(result), {'benchmark', 'model', 'quant', 'runtime', 'hardware', 'temperature', 'max_tokens', 'date', 'scorer_sha256', 'structured', 'answers_sha256'})
        self.assertEqual(result['benchmark'], 'local-agent-bench v0.2')
        self.assertEqual((result['temperature'], result['max_tokens']), (0.3, 37))
        self.assertEqual(result['scorer_sha256'], hashlib.sha256((ROOT / 'scorer/score.py').read_bytes()).hexdigest())
        self.assertEqual(result['answers_sha256'], hashlib.sha256(self.out.read_bytes()).hexdigest())
        self.assertEqual(result['structured']['all']['n'], 200)
        self.assertEqual(result['structured']['all']['accepted'], 1)
        self.assertEqual(result['structured']['all']['p50_s'], score.rows(self.out)[0]['latency_s'])
        self.assertEqual(result['structured']['all']['p95_s'], score.rows(self.out)[0]['latency_s'])
        self.assertEqual(set(result['structured']), {'factual', 'robustness', 'explanations', 'all'})
        self.assertTrue(result['date'].endswith('+00:00'))
        for name in ('../A/B:9b', '..', '', 'X Y/Z', '模型'):
            self.assertRegex(run.slug(name), r'^[a-z0-9._-]+$')
            self.assertNotIn('..', run.slug(name))

    def test_summary_import_without_metadata(self):
        self.invoke()
        Path(str(self.out) + '.meta.json').unlink()
        fake_root = self.directory / 'benchmark'
        shutil.copytree(ROOT / 'data', fake_root / 'data')
        (fake_root / 'scorer').mkdir()
        shutil.copyfile(ROOT / 'scorer/score.py', fake_root / 'scorer/score.py')
        with patch.object(run, 'ROOT', fake_root), redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(['--summarize', str(self.out), '--model', 'imported', '--temperature', '0.4', '--hardware', 'unknown', '--quant', 'unknown', '--runtime', 'imported']), 0)
        result = json.loads((fake_root / 'results/community/imported.json').read_text())
        self.assertEqual(result['temperature'], 0.4)

    def test_cli_subprocess(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scorer/run.py'), '--base-url', self.base_url, '--model', 'test-cli', '--out', str(self.out), '--limit', '2', '--timeout', '5'], text=True, capture_output=True, check=True)
        self.assertEqual([r['id'] for r in score.rows(self.out)], ['t001', 't002'])
        self.assertIn('completed 2 prompts', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_optional_usage(self):
        self.responses = [(200, json.dumps({'choices': [{'message': {'content': json.dumps(ANSWER)}, 'finish_reason': 'length'}]}))]
        row = self.invoke()[0][0]
        self.assertEqual(row['finish_reason'], 'length')
        self.assertNotIn('prompt_tokens', row)
        self.assertNotIn('completion_tokens', row)


class SavedResultsTests(unittest.TestCase):
    def test_saved_answers_reproduce_published_summary(self):
        tasks = score.rows(ROOT / 'data/tasks.jsonl')
        gold = {r['id']: r for r in score.rows(ROOT / 'data/gold.jsonl')}
        published = json.loads((ROOT / 'results/summary.json').read_text())
        for model in ('qwen3-8b-mlx-4bit', 'claude-opus-5-5', 'sql-formatter-baseline'):
            with self.subTest(model=model):
                answers = {r['id']: r for r in score.rows(ROOT / f'data/answers/{model}.jsonl')}
                details = [score.grade(t, gold[t['id']], answers[t['id']]) for t in tasks]
                self.assertEqual(details, score.rows(ROOT / f'data/grading/{model}.jsonl'))
                structured = score.summary(details)
                for stats in structured.values():
                    for field in ('p50_s', 'p95_s'):
                        if stats[field] is not None:
                            stats[field] = round(stats[field], 3)
                self.assertEqual(structured, published['models'][model]['structured'])


if __name__ == '__main__':
    unittest.main()
