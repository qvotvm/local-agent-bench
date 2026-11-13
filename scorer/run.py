"""Run frozen benchmark prompts against an OpenAI-compatible endpoint.

Examples (from the repository root):
    python3 scorer/run.py --base-url http://localhost:11434/v1 --model qwen3.5:9b --out answers.jsonl
    python3 scorer/run.py --summarize answers.jsonl --model qwen3.5:9b --hardware 'local machine' --quant '4-bit' --runtime ollama

Only standard-library modules are used. API keys are read from an environment
variable. Error rows contain fixed categories, never server bodies or URLs.
Summaries score all 200 tasks, so missing answers count as failures. On resume,
all existing ids, including errors, are skipped. Use a new file to retry them.
An adjacent .meta.json records generation settings without credentials or URLs.
For imported files without metadata, resume and summary use the supplied
temperature/max-tokens flags (defaults 0/800); supply the original settings.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from . import score
except ImportError:
    import score

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK = 'local-agent-bench v0.2'
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 800
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def parse(text):
    """Match the historical eval.py parser, including its fence handling."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    return json.loads(text)


def slug(model):
    value = re.sub(r'[^a-z0-9._-]+', '-', model.lower()).strip('.-_')
    return value or 'model'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def endpoint(base_url):
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('base URL must be an http or https endpoint')
    if parts.username is not None or parts.password is not None:
        raise ValueError('use --api-key-env for credentials')
    if parts.fragment:
        raise ValueError('base URL must not contain a fragment')
    path = parts.path.rstrip('/')
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ''))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward credentials or prompts to a redirected destination."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ResponseDeadline:
    """Interrupt header and body reads even while a peer keeps sending bytes."""
    def __init__(self, timeout):
        self.expires = time.monotonic() + timeout
        self.timer = None
        self.expired = threading.Event()

    def connection(self, base):
        deadline = self

        class Connection(base):
            def getresponse(self):
                sock = self.sock

                def interrupt():
                    deadline.expired.set()
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

                deadline.timer = threading.Timer(max(0, deadline.expires - time.monotonic()), interrupt)
                deadline.timer.daemon = True
                deadline.timer.start()
                return super().getresponse()

        return Connection

    def close(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer.join()


class DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, req):
        return self.do_open(self.deadline.connection(http.client.HTTPConnection), req)


class DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def https_open(self, req):
        return self.do_open(self.deadline.connection(http.client.HTTPSConnection), req, context=self._context)


class SecretInResponse(ValueError):
    pass


def contains_secret(text, secrets):
    return any(secret in text for secret in secrets)


def request_answer(url, model, prompt, key, temperature, max_tokens, timeout, retries):
    payload = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                          'temperature': temperature, 'max_tokens': max_tokens}).encode()
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    query = urllib.parse.urlsplit(url).query
    secrets = [value for value in (key, query, urllib.parse.unquote(query)) if value]
    secrets.extend(value for _, value in urllib.parse.parse_qsl(query) if value)
    started = time.perf_counter()
    finish_reason = None
    usage = {}
    error = None
    answer = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=payload, headers=headers, method='POST')
        deadline = ResponseDeadline(timeout)
        try:
            opener = urllib.request.build_opener(NoRedirect(), DeadlineHTTPHandler(deadline), DeadlineHTTPSHandler(deadline))
            with opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if deadline.expired.is_set():
                raise TimeoutError()
            if len(body) > MAX_RESPONSE_BYTES:
                error = 'response_too_large'
                break
            text = body.decode('utf-8')
            # Reject reflected secrets before parsing or extracting any fields.
            if contains_secret(text, secrets) or contains_secret(json.dumps(json.loads(text), ensure_ascii=False), secrets):
                raise SecretInResponse()
            envelope = json.loads(text)
            if not isinstance(envelope, dict) or not isinstance(envelope.get('choices'), list) or not envelope['choices']:
                raise ValueError('invalid response envelope')
            choice = envelope['choices'][0]
            if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict) or not isinstance(choice['message'].get('content'), str):
                raise ValueError('invalid response message')
            candidate_finish = choice.get('finish_reason')
            if isinstance(candidate_finish, str):
                candidate_finish.encode('utf-8')
            finish_reason = candidate_finish if isinstance(candidate_finish, str) else None
            supplied_usage = envelope.get('usage')
            if isinstance(supplied_usage, dict):
                for name in ('prompt_tokens', 'completion_tokens'):
                    value = supplied_usage.get(name)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        usage[name] = value
            answer = parse(choice['message']['content'])
            # JSON escapes may decode to lone surrogates that cannot be saved.
            json.dumps({'answer': answer, 'finish_reason': finish_reason}, ensure_ascii=False).encode('utf-8')
            if not isinstance(answer, dict):
                error = 'invalid_answer'
            else:
                error = None
            break
        except SecretInResponse:
            error = 'secret_in_response'
            break
        except urllib.error.HTTPError as exc:
            error = f'http_{exc.code}'
            exc.close()
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                break
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
            error = 'request_failed'
            if attempt == retries:
                break
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError):
            answer = None
            error = 'invalid_json'
            break
        finally:
            deadline.close()
        if attempt < retries:
            time.sleep(min(0.25 * 2 ** attempt, 2.0))
    result = {'latency_s': time.perf_counter() - started, 'finish_reason': finish_reason, **usage}
    if contains_secret(json.dumps(result, ensure_ascii=False), secrets) or contains_secret(json.dumps(answer, ensure_ascii=False), secrets):
        result = {'latency_s': result['latency_s'], 'finish_reason': None}
        error = 'secret_in_response'
    if error:
        result['error'] = error
    else:
        result['answer'] = answer
    return result


def load_metadata(path):
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('invalid run metadata')
    return value


def run(args):
    url = endpoint(args.base_url)
    key = os.environ.get(args.api_key_env) if args.api_key_env else None
    if args.api_key_env and not key:
        raise ValueError('API key environment variable is empty or missing')
    prompts = score.rows(ROOT / 'data/prompts.jsonl')
    out = Path(args.out)
    meta_path = Path(str(out) + '.meta.json')
    metadata = {'benchmark': BENCHMARK, 'model': args.model, 'temperature': args.temperature,
                'max_tokens': args.max_tokens, 'prompts_sha256': sha256(ROOT / 'data/prompts.jsonl')}
    # Do not create mixed-setting answer files when resuming.
    if args.resume and out.exists():
        existing_metadata = load_metadata(meta_path)
        if existing_metadata is not None and existing_metadata != metadata:
            raise ValueError('resume settings differ from the saved run')
    seen = set()
    if out.exists():
        if not args.resume:
            raise ValueError('output already exists; use --resume or a new file')
        seen = {row['id'] for row in score.rows(out)}
        if out.stat().st_size and not out.read_bytes().endswith(b'\n'):
            raise ValueError('resume output must end with a newline')
    pending = [row for row in prompts if row['id'] not in seen]
    if args.limit is not None:
        pending = pending[:args.limit]
    out.parent.mkdir(parents=True, exist_ok=True)
    if key and key in json.dumps(metadata, ensure_ascii=False):
        raise ValueError('API key must not appear in run metadata')
    meta_path.write_text(json.dumps(metadata, indent=2) + '\n')
    with out.open('a', encoding='utf-8') as stream:
        for prompt in pending:
            result = request_answer(url, args.model, prompt['prompt'], key,
                                    args.temperature, args.max_tokens, args.timeout, args.retries)
            row = {'id': prompt['id'], **result}
            # A final serialization guard also catches escaped secrets.
            serialized = json.dumps(row, ensure_ascii=False)
            if key and key in serialized:
                row = {'id': prompt['id'], 'latency_s': result['latency_s'],
                       'finish_reason': None, 'error': 'secret_in_response'}
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
    print(f'completed {len(pending)} prompts')


def summarize(args):
    answers_path = Path(args.summarize)
    metadata = load_metadata(Path(str(answers_path) + '.meta.json'))
    if metadata is None:
        metadata = {'benchmark': BENCHMARK, 'model': args.model, 'temperature': args.temperature,
                    'max_tokens': args.max_tokens, 'prompts_sha256': sha256(ROOT / 'data/prompts.jsonl')}
    if metadata.get('model') != args.model or metadata.get('benchmark') != BENCHMARK:
        raise ValueError('summary model or benchmark differs from the saved run')
    if metadata.get('prompts_sha256') != sha256(ROOT / 'data/prompts.jsonl'):
        raise ValueError('saved run used different prompts')
    tasks = score.rows(ROOT / 'data/tasks.jsonl')
    gold = {row['id']: row for row in score.rows(ROOT / 'data/gold.jsonl')}
    answers = {row['id']: row for row in score.rows(answers_path)}
    details = [score.grade(task, gold[task['id']], answers.get(task['id'], {})) for task in tasks]
    result = {'benchmark': BENCHMARK, 'model': args.model, 'quant': args.quant,
              'runtime': args.runtime, 'hardware': args.hardware,
              'temperature': metadata['temperature'], 'max_tokens': metadata['max_tokens'],
              'date': datetime.now(timezone.utc).isoformat(), 'scorer_sha256': sha256(ROOT / 'scorer/score.py'),
              'structured': score.summary(details), 'answers_sha256': sha256(answers_path)}
    directory = ROOT / 'results/community'
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (slug(args.model) + '.json')
    if destination.exists():
        raise ValueError('community summary already exists; move it before replacing it')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'wrote results/community/{destination.name}')
    return destination


def finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError('must be a finite number')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', help='OpenAI-compatible API base, such as http://localhost:11434/v1')
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', help='JSONL answer destination; existing files require --resume')
    parser.add_argument('--api-key-env', metavar='NAME')
    parser.add_argument('--temperature', type=finite_float, default=DEFAULT_TEMPERATURE)
    parser.add_argument('--max-tokens', type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument('--limit', type=int, help='maximum new prompts to run, after skipping resumed ids')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--timeout', type=finite_float, default=120.0, help='seconds per HTTP attempt')
    parser.add_argument('--retries', type=int, default=2, help='extra attempts for transport errors and HTTP 429/500/502/503/504')
    parser.add_argument('--summarize', metavar='ANSWERS_JSONL')
    parser.add_argument('--hardware')
    parser.add_argument('--quant')
    parser.add_argument('--runtime')
    args = parser.parse_args(argv)
    if args.temperature < 0 or args.max_tokens < 1 or args.timeout <= 0 or args.retries < 0 or args.retries > 10 or (args.limit is not None and args.limit < 0):
        parser.error('invalid generation limit, temperature, timeout or retry count (0..10)')
    if args.summarize:
        if args.base_url or args.out or args.resume or args.api_key_env or args.limit is not None:
            parser.error('--summarize cannot be combined with run options')
        if any(value is None for value in (args.hardware, args.quant, args.runtime)):
            parser.error('--summarize requires --hardware, --quant and --runtime')
    elif not args.base_url or not args.out:
        parser.error('a run requires --base-url and --out')
    try:
        if args.summarize:
            summarize(args)
        else:
            run(args)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Exception messages can contain credentials, URLs or local file contents.
        print(f'runner failed ({type(exc).__name__}); check arguments and local files', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
