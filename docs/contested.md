# contested scoring decisions

choices and limits in this benchmark that a reasonable reader could assess differently. measured effects are listed where available; unrun settings have no measured effect.

## 1. over-cautious "incomplete" counts as a status miss

22 of Qwen's 35 status misses outside the factual group are answers marked "incomplete" where the gold says the retained data answers the question ("ok"). Opus made the same miss 8 times. a more cautious reader could call these safe answers. we score them as misses because an agent may stop acting on data marked incomplete; agent behavior was not tested. if these status choices were accepted, Qwen's status score would rise from 160 / 200 to 182 / 200 and Opus's from 192 / 200 to 200 / 200. those are status scores, not complete-answer scores.

## 2. nulls next to a refusal or incomplete answer fail the numeric check

in 11 robustness tasks Opus returned null for a known value: 7 refusals and 4 incomplete answers. the gold expected the observed value to be copied alongside either status. this is why Opus scores 39 / 50 on exact fields in the robustness group against 49 for Qwen. these are conservative answers, not invented numbers. accepting those null substitutions for both statuses would raise Opus's exact-field total from 189 / 200 to 200 / 200. accepting only the 7 refusals would give 196 / 200. citations and status would still be scored separately.

## 3. explanations: 50 answers, one grader, not independent

the 19 / 50 and 36 / 50 explanation scores come from a written rubric applied by the AI coding session that ran the benchmark, not by an independent human. the grader is itself a language model, so self-preference toward one model's style is a real risk. an identity-masked re-grade gave 35 for Opus and 19 for Qwen; one Opus answer was raised to pass after unmasking, which makes it 36. the reviewer had already seen the original results, so masking was limited. treat these two numbers as indicative. an independent human grade of the 100 explanation answers is the open fix.

## 4. one comparison model, one run each

one small local model against one frontier model, one final run each. Qwen ran at temperature 0 with seed 0. Opus ran at temperature 0.4, the client default, with no seed sent, so its run-to-run variance is not measured. the temperatures were not matched; a matched rerun is open. the saved wider comparison has a partial hosted-model run, but no additional model completed. 8-bit weights and Qwen's thinking mode were not run.

## 5. prompt tuned on an Opus-only pilot

a pilot with 17 completed attempts on Opus, with a 384-token cap, came before the final runs. the output cap (now 800) and the rule wording were revised after it. no Qwen pilot is recorded in the supplied evidence, so the final prompt may suit Opus better.

## 6. structured checks measure copy fidelity

a deterministic SQL formatter that knows each task's intent in advance passes all 200 structured checks and none of the 50 explanation checks. the structured rows therefore measure whether a model copies the retrieved context faithfully and picks the right status, not whether it can find the data.

## 7. few independent days

200 tasks reference 158 distinct facts after deduplicating specification rules repeated across dates. the snapshot covers 14 days; tasks span 16 date groups, including a missing day and an after-cutoff day. the paired day-cluster bootstrap for complete structured answers, Qwen minus Opus, is -13.5 points (95% interval -22.8 to -5.9) over all tasks, -40 points (-46.2 to -32.6) in the explanation group, and -4 points (-11.9 to +5.9) on robustness, where it crosses zero. the explanation-group gap is for structured acceptance, not the separate prose rubric. the bootstrap uses 10,000 resamples, seed 20261016, with 16 groups overall, 4 for explanations and 6 for robustness. this is exploratory, not a general superiority or equivalence claim. there was no forward test.

## 8. Opus served through a third-party API

Opus was called through the Venice API, not Anthropic's own API. the run set the model id and output cap; temperature 0.4 came from the client default, and no seed was sent. any provider-side settings beyond those are not visible to us, and latency includes that provider's network path.

## 9. 4-bit weights stand in for the model

Qwen ran as the published MLX 4-bit conversion (group size 128), not full precision. quantization can cost accuracy, so these results describe this build of Qwen3-8B, not the model at 16-bit.
