# data

local-agent-bench v0.2 contains the frozen inputs and saved outputs for 200 tasks: 100 factual, 50 robustness, and 50 explanations. no inference or chain access is needed to rescore them.

| file | schema and rows |
|---|---|
| `tasks.jsonl` | 200 objects: `id`, `bucket`, `day`, `question`, `requested` (ordered field names) |
| `gold.jsonl` | 200 objects: `id`, `values` (numbers or null), `status`, `citations` (evidence ids) |
| `prompts.jsonl` | 200 objects: `id`, `prompt` (the complete shared instruction and JSON evidence, with only the privacy renames below) |
| `answers/qwen3-8b-mlx-4bit.jsonl` | 200 objects: `id`, `latency_s`, `answer` |
| `answers/claude-opus-5-5.jsonl` | 207 objects: `id`, `latency_s`, `answer`; seven earlier parse failures also have `error` and an empty answer |
| `answers/sql-formatter-baseline.jsonl` | 200 objects: `id`, `latency_s`, `answer`; a fixed task-intent formatter, not an LLM planner |
| `grading/<model>.jsonl` | 200 objects per model: `id`, `bucket`, `day`, `numeric`, `citations`, `status`, `accepted`, `invented_structured`, `privacy_pattern`, `latency_s`, `error` |

an answer contains `values`, `status`, `citations`, and `text`. the last row for an id is scored. all seven failed Opus attempts were followed by successful answers; they do not contribute latency to the published scores. raw responses and provider diagnostics, costs, tool counts, and token counts are omitted from saved historical answers. the SQL formatter's prose is a neutral fixed template; its saved timings are retained, not regenerated.

evidence source labels were renamed to neutral names (`chain index sql`, `quotum record spec v1`), and a few wordings in questions, notes and answer prose were made neutral. no value, id, citation, status or gold row changed, and rescoring is byte-identical. the metric name `seated_tier2` is kept because it is part of the citation ids. `prompts.jsonl` holds the complete frozen evidence; there is no separate retrieval file.

run from the repository root:

```sh
python3 scorer/score.py --tasks data/tasks.jsonl --gold data/gold.jsonl --answers data/answers/qwen3-8b-mlx-4bit.jsonl
python3 scorer/score.py --tasks data/tasks.jsonl --gold data/gold.jsonl --answers data/answers/claude-opus-5-5.jsonl
python3 scorer/score.py --tasks data/tasks.jsonl --gold data/gold.jsonl --answers data/answers/sql-formatter-baseline.jsonl
```

the structured results reproduce `results/summary.json`; latency there is rounded to three decimal places. that file remains the historical v0.1 run record. explanation substance and API cost are not reconstructed by the structured scorer.
