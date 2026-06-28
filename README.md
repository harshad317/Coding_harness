# D_val Coding Harness

D_val is a minimal self-test repair harness for Python coding tasks. This repo
uses OpenAI models only, and the default model is `gpt-5.4-mini`.

It runs one loop:

1. Read a task prompt and starter implementation.
2. Ask the model to write both `solution.py` and `self_tests.py`.
3. Execute only the generated self-tests.
4. Feed self-test failures back to the model.
5. Score hidden tests only after the repair loop ends.

Hidden tests are never shown to the model. There are no baseline modes, public
test modes, plotting tools, benchmark loaders, local-model/Ollama code, or
non-OpenAI provider paths in this trimmed repo.

## What This Repo Contains

```text
harness/
  agent.py      # D_val self-test repair loop
  models.py     # OpenAI Responses API client for gpt-5.4-mini by default
  sandbox.py    # isolated task workspace and pytest runner
  log.py        # JSONL run records
run.py          # CLI entry point
tasks/          # small sample Python tasks
```

## Task Format

Each task is a directory with exactly this contract:

```text
prompt.md        # problem statement shown to the model
solution.py      # starter implementation the model rewrites
hidden_tests.py  # final private scoring tests, never shown to the model
```

During a run, D_val creates an isolated temporary workspace, copies the task
starter into it, and asks the model to produce:

```text
solution.py
self_tests.py
```

The generated `self_tests.py` must import from `solution.py` and use `pytest`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export OPENAI_API_KEY="your_api_key_here"
```

## Run All Sample Tasks With gpt-5.4-mini

```bash
python run.py \
  --model gpt-5.4-mini \
  --all \
  --max-iterations 3 \
  --log results/dval_runs.jsonl
```

## Run One Task With gpt-5.4-mini

```bash
python run.py \
  --model gpt-5.4-mini \
  --tasks tasks/task_001_sum_evens \
  --max-iterations 3
```

## Useful Options

```text
--model              OpenAI model name. Default: gpt-5.4-mini
--max-iterations     Repair attempts per task. Default: 3
--max-bash-calls     Self-test command budget. Default: 10
--pytest-timeout     Self-test timeout in seconds. Default: 10
--hidden-timeout     Hidden-test timeout in seconds. Default: 60
--max-tokens         Max model output tokens. Default: 4096
--temperature        Model temperature. Default: 0.0
--log                JSONL output path. Default: results/dval_runs.jsonl
```

## Output

Each run appends one JSON object to the log with:

```text
task_id
model
mode
passed_self
passed_hidden
iterations_used
bash_calls_used
tokens_in
tokens_out
wall_time_s
final_error_type
```

Common `final_error_type` values:

```text
missing_self_tests   model did not produce self_tests.py
overfit_self         generated self-tests passed, hidden tests failed
incorrect            self-tests and hidden tests failed
hidden_timeout       hidden scoring timed out
model_error:*        model API call failed
harness_error:*      harness runtime failed
```

## Validate Locally

```bash
python -m pytest -q
python -m compileall -q harness run.py
```

You can also run a no-API smoke check by importing `run_task` with a fake model
client in a Python script or REPL.

## Safety Notes

Generated `solution.py` and `self_tests.py` run in a temporary task workspace
with a sanitized subprocess environment. Secrets such as `OPENAI_API_KEY` are
not inherited by model-generated tests or hidden-test scoring subprocesses.

## Design Boundary

This repository is intentionally narrow. It is only the D_val self-test repair
loop using OpenAI models. Anything related to baseline comparisons, SWE-bench
scoring, plotting, benchmark downloading, local model hosting, non-OpenAI
providers, or GCP/Docker orchestration belongs outside this minimal core.
