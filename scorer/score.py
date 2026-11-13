"""Score model answers against gold for the local-agent-bench tasks.

A task counts as a complete structured answer when every numeric field is exact,
the citation set is exact, the status is correct and the text has no wallet address (a generic leak check).
Explanation substance is graded separately by a written rubric, not by this script.
"""
import argparse
import json
import math
import re
import statistics
from pathlib import Path

STATUSES = {'ok', 'incomplete', 'stale', 'refuse', 'after_cutoff'}
LEAK = re.compile(r'0x[0-9a-fA-F]{40}')


def rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def same(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        try:
            return math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-8)
        except (TypeError, ValueError, OverflowError):
            return False
    return a == b


def grade(task, gold, out):
    if not isinstance(out, dict):
        out = {}
    ans = out.get('answer', {})
    if not isinstance(ans, dict):
        ans = {}
    values = ans.get('values', {})
    valid_values = isinstance(values, dict)
    if not valid_values:
        values = {}
    numeric = valid_values and set(values) == set(gold['values']) and all(same(values.get(k), v) for k, v in gold['values'].items())
    cited = ans.get('citations', [])
    valid_citations = isinstance(cited, list) and all(isinstance(c, str) for c in cited)
    if not valid_citations:
        cited = []
    citations = valid_citations and set(cited) == set(gold['citations']) and len(cited) == len(set(cited))
    status = ans.get('status') == gold['status']
    text = ans.get('text', '')
    valid_text = isinstance(text, str)
    privacy = bool(LEAK.search(text)) if valid_text else False
    invented = any(k not in gold['values'] or not same(v, gold['values'][k]) for k, v in values.items()) or any(c not in gold['citations'] for c in cited)
    return {
        'id': task['id'], 'bucket': task['bucket'], 'day': task.get('day'),
        'numeric': numeric, 'citations': citations, 'status': status,
        'accepted': numeric and citations and status and valid_text and not privacy,
        'invented_structured': invented, 'privacy_pattern': privacy,
        'latency_s': out.get('latency_s'), 'error': out.get('error'),
    }


def summary(details):
    out = {}
    buckets = sorted({d['bucket'] for d in details}) + ['all']
    for b in buckets:
        rr = [d for d in details if b == 'all' or d['bucket'] == b]
        times = sorted(d['latency_s'] for d in rr
                       if isinstance(d['latency_s'], (int, float))
                       and not isinstance(d['latency_s'], bool)
                       and same(d['latency_s'], d['latency_s'])
                       and d['latency_s'] not in (math.inf, -math.inf))
        out[b] = {'n': len(rr), **{k: sum(d[k] for d in rr) for k in ['numeric', 'citations', 'status', 'accepted', 'invented_structured', 'privacy_pattern']},
                  'p50_s': statistics.median(times) if times else None,
                  'p95_s': times[math.ceil(.95 * len(times)) - 1] if times else None}
    return out


def main():
    p = argparse.ArgumentParser(description='score answers against gold')
    p.add_argument('--tasks', required=True)
    p.add_argument('--gold', required=True)
    p.add_argument('--answers', required=True, help='jsonl; the last row per task id is scored')
    p.add_argument('--details', help='optional path for per-task grading jsonl')
    a = p.parse_args()
    tasks = rows(a.tasks)
    gold = {r['id']: r for r in rows(a.gold)}
    answers = {r['id']: r for r in rows(a.answers)
               if isinstance(r, dict) and isinstance(r.get('id'), str)}
    for g in gold.values():
        if g['status'] not in STATUSES:
            raise SystemExit(f"unknown gold status {g['status']} in {g['id']}")
    details = [grade(t, gold[t['id']], answers.get(t['id'], {})) for t in tasks]
    if a.details:
        Path(a.details).write_text(''.join(json.dumps(d) + '\n' for d in details))
    print(json.dumps(summary(details), indent=2))


if __name__ == '__main__':
    main()
