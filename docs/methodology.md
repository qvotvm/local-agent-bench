# methodology

## data

a frozen snapshot of Robinhood Chain (chain id 4663) from 2 to 15 October 2026, cutoff 16 October 00:00 UTC. 694 metric rows:

- token launches and graduations on two launch venues (pons and Long.xyz)
- ETH flows on bonding curves
- quotum's own daily settlement records. quotum is the authors' project. once a day at 20:00 UTC (the "bell") it records what was spent on inference and what QUOTUM was bought back and burned
- frozen specification rules, repeated across dates

109 values are explicitly unknown in the snapshot. the tasks test whether a model keeps them unknown.

aggregates are chain-wide; no wallet-level data is used.

## retrieval

six read-only tools over a local SQLite copy of the snapshot. every value carries venue, unit, time window, block bounds, source and a coverage note; some bounds are unknown. each task has a fixed list of at most four tool calls (one or two in practice), and both models got the identical frozen context. tool choice is not tested.

## tasks

- 100 factual: one observed value or explicit unknown for one day, with unit, citation and coverage caveat; rule questions use the frozen specification
- 50 robustness: 43 need a status other than "ok" (stale, incomplete, refuse, after_cutoff); 7 expect "ok" with separate venue counts or a corrected flow definition
- 50 explanations: say why a value means what it means, for example why a burned token count is not an inference token count

## answer format

JSON with `values`, `status` (ok, incomplete, stale, refuse, after_cutoff), `citations` and one sentence of `text`. output capped at 800 tokens. gold answers were never sent to a model and were not changed after final answers came in. before the final runs, an Opus-only pilot had 17 completed attempts at a 384-token cap; the cap and rule wording were then revised.

## models

| | local | cloud |
|---|---|---|
| model | `Qwen/Qwen3-8B-MLX-4bit` (MLX 4-bit, group size 128), revision `383413e909f3bc5303ce195ebbdf0339c5a1a2a3` | `claude-opus-5-5` |
| runtime | mlx 0.32.4, mlx-lm 0.32.2 | Venice API, no web search |
| mode | non-thinking, temperature 0, seed 0 | temperature 0.4 |
| hardware | Apple silicon Mac, 48 GiB; chip model not recorded | hosted |
| memory | peak MLX allocator 4.8 GiB | |
| load | 2.3 s; first answer including load 5.6 s | |
| inference totals | 89,347 prompt and 13,895 output tokens in 470.9 s for 200 tasks | |

Qwen3-8B here is the stock post-trained release, not the `-Base` checkpoint, with no fine-tuning. 8-bit weights and thinking mode were not run. the totals above sum saved local request times, excluding model load; separate prefill and decode rates were not recorded. seed 0 was reset before each local request.

## scoring

a complete structured answer needs all four:

1. every numeric field exact within relative 1e-7, absolute 1e-8
2. the exact citation set, no duplicates
3. the correct status
4. no wallet address in the text (a generic leak check, reported as `privacy_pattern`)

[`../scorer/score.py`](../scorer/score.py) implements this. the last answer row per task id is scored. the saved `invented_structured` flag counts any value mismatch or unsupported citation, including rounding and null abstentions; it is not a hallucination count. the standalone adaptation retains the historical boolean-number equality (`true` can equal `1`), and its leak check only matches 40-hex addresses. it is not a security test.

explanation substance is graded separately against a written rubric:

- a recap must name the venue and observed scope
- arithmetic must state its operation
- a "why" answer must state the real coverage or unit distinction
- a rule answer must state the threshold and unit or counter
- generic phrases like "observed" or "unknown" fail

see [contested.md](contested.md) for who graded it and how far to trust it.

## latency and cost

median latency is over the last answer per task, excluding pacing and earlier authorization retries. cloud latency includes network and client overhead; local latency does not. the $1.64 is API cost for the final cloud run at the recorded Venice prices, without the pilot. local electricity, serving and hardware were not measured.
