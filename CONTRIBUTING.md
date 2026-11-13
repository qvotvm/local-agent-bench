# contributing

two kinds of contributions: a run of your model, or a correction to the benchmark.

## submit a run

you need python 3.10+ and any server that speaks the OpenAI chat completions API: ollama, llama.cpp `llama-server`, LM Studio, vLLM, `mlx_lm.server`, or a hosted endpoint. no other dependencies.

```
git clone https://github.com/qvotvm/local-agent-bench
cd local-agent-bench
ollama pull qwen3.5:9b
python3 scorer/run.py --base-url http://localhost:11434/v1 --model qwen3.5:9b --out runs/qwen3.5-9b.jsonl
```

this sends the 200 prompts in `data/prompts.jsonl`, one request each, temperature 0, max 800 output tokens, and saves the parsed answers. `--resume` continues an interrupted run. for a hosted endpoint, put the key in an environment variable and pass its name with `--api-key-env`. the key is never written to disk.

then write your result row:

```
python3 scorer/run.py --summarize runs/qwen3.5-9b.jsonl --model qwen3.5:9b \
  --quant "q4_K_M" --runtime "ollama <version>" --hardware "M2 Pro, 32 GB"
```

this scores the answers with `scorer/score.py` and writes `results/community/qwen3.5-9b.json`.

open a pull request that adds that one file. in the description, link the answers file and the `.meta.json` next to it (a gist is fine), and say:

- thinking on or off. the reference Qwen3-8B run used non-thinking mode. if your model thinks, say so; the runner strips `<think>` blocks before parsing.
- anything you changed from the defaults: temperature, max tokens, system prompt, context length.

## rules for a run

- same prompts, no edits. no system prompt unless the model needs one to answer in JSON, and then say what it was.
- one run, reported as it came out. no best-of-n.
- latency depends on your hardware and runtime, so it is reported next to the hardware line, not ranked.
- the scorer checks structured fields only. explanations are not graded for community runs.

we check each PR by rescoring your answers file. if the scores match, the row goes into the community table in the README.

## corrections

a scoring bug, a wrong gold answer, a fairer setting: open an issue with the task id and what you think is wrong. accepted corrections go in [docs/contested.md](docs/contested.md) and a new version. old numbers stay.

## license

by opening a PR you agree your result file is published under CC BY 4.0, like the rest of the results.
