# Local models for agents: a small benchmark on Robinhood Chain data

quotum · technical note · 13 November 2026

## Summary

We ran the stock Qwen3-8B model, with no fine-tuning, locally on an Apple silicon Mac against 200 question-answering tasks built from two weeks of Robinhood Chain data (chain id 4663). We ran the same tasks through a frontier cloud model, Claude Opus 5.5, with the same retrieved context.

| | Qwen3-8B, local, 4-bit | Claude Opus 5.5, cloud |
|---|---:|---:|
| complete structured answers | 154 / 200 | 181 / 200 |
| exact numeric fields | 197 / 200 | 189 / 200 |
| exact citation set | 197 / 200 | 195 / 200 |
| correct status | 160 / 200 | 192 / 200 |
| explanations passing the rubric | 19 / 50 | 36 / 50 |
| median latency per answer | 2.1 s | 6.4 s |
| API cost for 200 tasks | $0 (hardware not costed) | $1.64 |

On these tasks, the small local model matched the frontier model on factual numbers and citations. It scored lower on status (ok, stale, incomplete, refuse, after_cutoff) and explanation substance. Most of its status misses were over-cautious. No fine-tuning was tested, so the benchmark does not establish whether narrow training would close that gap.

## What we measured

**Data.** A frozen snapshot of Robinhood Chain activity from 2 to 15 October 2026, cut off at 16 October 00:00 UTC. It holds 694 metric rows: token launches and graduations on two launch venues, ETH flows on bonding curves, quotum's own daily settlement records (quotum is the authors' project; once a day at 20:00 UTC, the "bell", it records what was spent on inference and what QUOTUM was bought back and burned), and frozen specification rules repeated across dates. The aggregates cover 41,256 observed launches on one venue and 17,079 on another. 109 values are explicitly unknown in the snapshot, and the tasks test whether a model keeps them unknown.

**Tools.** Six read-only tools over a local SQLite copy of the snapshot. Every number they return carries its venue, unit, time window, block bounds, source and a coverage note; some bounds are unknown. The audit checked counts and flows against saved SQL results, settlement amounts against receipt sums and chain RPC reads, and rules against the frozen specification. Supplementary checks were finalized after the model runs.

**Tasks.** 200 tasks in three groups:

- 100 factual: report one observed value or explicit unknown for one day with its unit, citation and coverage caveat; rule questions use the frozen specification.
- 50 robustness: 43 require a status other than "ok": stale data, incomplete or missing history, refusal, or a request after the cutoff. Seven expect "ok" with separate venue counts or a corrected flow definition.
- 50 explanations: say in words why a value means what it means, for example why a burned token count is not an inference token count.

**Protocol.** Each model got identical frozen retrieval contexts built with at most four tool calls; in practice one or two. Gold answers were never sent to a model and were not changed after final answers came in. Output was capped at 800 tokens. A cloud pilot with 17 completed attempts used a 384-token cap; the cap and rule wording were revised before the final runs. The answer format is JSON: values, status, citations, and a one-sentence text.

**Models.**

- Local: `Qwen/Qwen3-8B-MLX-4bit` (MLX 4-bit, group size 128) at a pinned revision, run with mlx-lm in non-thinking mode at temperature 0 on a 48 GiB Apple silicon Mac. Peak MLX memory 4.8 GiB. Model load took 2.3 s, and the first answer including load took 5.6 s.
- Cloud: `claude-opus-5-5` through the Venice API at temperature 0.4, with no web search. Its latency includes network and client overhead.

**Scoring.** A *complete structured answer* needs four things at once: every numeric field exact within the scorer's tolerance (relative 1e-7, absolute 1e-8), the exact citation set, the correct status, and no wallet address in the text. Explanation substance was scored separately against a written rubric. A recap must name the venue and observed scope. Arithmetic must state its operation. A "why" answer must state the real coverage or unit distinction. Generic phrases like "observed" or "unknown" fail.

## Results by group

| group | model | exact fields | citations | status | complete |
|---|---|---:|---:|---:|---:|
| factual (100) | Qwen3-8B | 100 | 100 | 95 | 95 |
| factual (100) | Opus 5.5 | 100 | 100 | 100 | 100 |
| robustness (50) | Qwen3-8B | 49 | 47 | 37 | 33 |
| robustness (50) | Opus 5.5 | 39 | 45 | 46 | 35 |
| explanations (50) | Qwen3-8B | 48 | 50 | 28 | 26 |
| explanations (50) | Opus 5.5 | 50 | 50 | 46 | 46 |

Three findings stand out.

1. **On factual retrieval the small model is close.** It got 100 of 100 fields and citations exact. Its three numeric misses across all 200 tasks were rounding to two decimals. We found no invented numbers and no invented citations in its saved answers.
2. **On status the small model is weak.** On robustness tasks it picked the right status 37 times out of 50, against 46 for Opus. Across the 100 non-factual tasks Qwen missed the status 35 times. Most misses were over-cautious: 22 times it marked a complete answer "incomplete". It also wrote "incomplete" 8 times where the right answer was to refuse, and "ok" 3 times on stale data. The other two misses were "ok" instead of "incomplete" and "incomplete" instead of "stale". Five more status misses on factual tasks bring Qwen's total to 40. Opus missed the status 8 times, each time marking a complete answer "incomplete".
3. **On explanations the gap is large.** Opus wrote 36 substantive explanations out of 50 and Qwen wrote 19. Qwen's typical miss restates the field name instead of the reason.

The frontier model's lower exact-field score on robustness (39 against 49) reflects the strict gold. In 11 cases Opus returned null for a known value: 7 refusals and 4 incomplete answers. The gold expected the observed value to be copied alongside either status. These are conservative answers, not invented ones.

## Two examples

**Stale data.** The question asks what is happening now, but the only data is from an older day.

- Qwen3-8B: status `ok`, text "observed launches in the window". The number is right, but the answer presents an old day as the present.
- Opus 5.5: status `stale`, text "this is an observed, uncertified launch count from an older pons day, so it does not describe what is happening now at the cutoff."

**Explanation.** The question asks why the number of QUOTUM tokens burned is not the number of inference tokens used.

- Qwen3-8B: values and citations correct, text "tokensin is unknown" (tokensIn is the inference input-token field). It is true, but it explains nothing.
- Opus 5.5: "the observed burned count is measured in quotum tokens settled at the bell, not inference tokens, and inference token usage was not counted, so it remains unknown."

## Limitations

- **This is fixed-retrieval question answering.** Tool calls were fixed per task. We did not test whether a model picks the right tool, and we did not test what it knows about Robinhood Chain on its own.
- **Few independent days.** The 200 tasks reference 158 distinct facts after deduplicating specification rules repeated across dates. The snapshot covers 14 days, but tasks span 16 date groups, including a missing day and an after-cutoff day. A paired bootstrap over those 16 date groups (10,000 resamples) puts the gap in complete answers, Qwen minus Opus, at -13.5 points, with a 95% interval of -22.8 to -5.9. That is too few independent days to claim one model is better or equivalent in general. There was no forward test on later dates.
- **Explanations were not reviewed by an independent human.** The rubric was applied by the AI coding session that ran the benchmark. A later identity-masked re-grade initially gave 35 for Opus and 19 for Qwen. One Opus answer was corrected after unmasking, bringing it to 36. The reviewer had read the original results and could recognize reused prose, so masking was limited. Treat the explanation numbers as indicative.
- **Settings differ.** Qwen ran at temperature 0 and Opus at 0.4. Opus latency includes network and client overhead, and Qwen latency does not. Latencies cover the final attempt per task, excluding pacing and earlier authorization retries.
- **Cost is API cost only.** The $1.64 covers the final cloud run. Including the pilot, the saved ledger records $1.7607, with an upper accounting total of $1.8007 including reserves. Local electricity, serving and hardware were not measured. A zero API bill is not free serving.
- **Stock model only.** This is the post-trained Qwen3-8B release, not the `-Base` checkpoint. No fine-tuning was done. 8-bit weights and thinking mode were not run. Only one small model and one frontier model completed all 200 tasks. A wider comparison was started and did not finish, so it is not reported.

## What the numbers suggest for agent design

A deterministic query-and-formatter baseline passed the structured checks on all 200 tasks, because it knows the intent in advance. Its generic prose passed none of the 50 explanation checks. Qwen copied the numbers well and explained poorly. Opus wrote 36 substantive explanations out of 50, at about a cent in API cost per structurally accepted answer and a median latency of 6.4 s against Qwen's 2.1 s.

These results suggest keeping numbers, citations and limits in deterministic tools, and evaluating a stronger model when a task needs explanation or status judgement. A hybrid agent would still need separate tests of tool choice, private-data handling and signing security. None was measured here.

## Code and data

The v0.1 package contains the scorer, saved per-group results, methodology and a list of contested scoring decisions. The 200 tasks, gold answers, frozen retrieval contexts and both models' saved answers are planned for v0.2 after a privacy pass. Repository URL for the package: https://github.com/quotumorg/local-agent-bench.

## Sources

- Qwen3-8B, MLX 4-bit weights. https://huggingface.co/Qwen/Qwen3-8B-MLX-4bit
- quotum daily settlement records and usage. https://quotum.org/usage

Benchmark run 16 October 2026. Structured scores are from that run; explanation re-review and confidence intervals are from the later saved comparison. No model was rerun for this note.
