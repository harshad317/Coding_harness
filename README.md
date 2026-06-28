# D_val Coding Harness

D_val is a coding harness for Python tasks and repositories. This repo uses
OpenAI models only, and the default model is `gpt-5.4-mini`.

It has two loops:

1. **Task mode** reads a task prompt and starter `solution.py`, asks the model
   to write `solution.py` and `self_tests.py`, runs generated self-tests,
   feeds failures back, then scores private hidden tests.
2. **Repo mode** copies a full repository into an isolated workspace, gives the
   model the repo tree and file contents, applies structured add/edit/delete
   operations, runs the configured project test command, feeds failures back,
   and emits a final diff plus critical suggestions.

Hidden tests are never shown to the model in task mode. Repo mode edits a copy
of the repository by default; pass `--apply` only when you want the harness to
write the final changes back to the source repo.

## What This Repo Contains

```text
harness/
  agent.py       # single-file D_val self-test repair loop
  repo_agent.py  # full-repo add/edit/delete repair loop
  models.py      # OpenAI Responses API client for gpt-5.4-mini by default
  sandbox.py     # isolated task workspace and pytest runner
  log.py         # JSONL run records
harness_desktop/ # native Tkinter desktop app and run service
desktop_app.py   # desktop app launcher
run.py           # CLI entry point
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

## Run Repo Mode

Repo mode is for a real codebase. It reads the repo tree and text files into
the model prompt, asks for a step-by-step JSON plan, applies file operations in
a temporary copy, runs your test command, and writes a diff.

```bash
python run.py \
  --model gpt-5.4-mini \
  --repo /path/to/repo \
  --instruction "Fix the failing tests and keep the change minimal." \
  --test-command "python -m pytest -q" \
  --max-iterations 3 \
  --diff-out results/repo_fix.patch \
  --log results/repo_runs.jsonl
```

By default repo mode does not modify `/path/to/repo`; it only logs the run and
writes the diff. To write the final changed files back to the source repo only
when the run ends without a harness or test error, add:

```bash
--apply
```

Files omitted because of binary content or snapshot byte limits are reported in
`snapshot_omitted`. Increase `--max-repo-bytes` and `--max-file-bytes` when you
want the model to receive more of a large codebase.

Repo-mode model responses use this contract:

```json
{
  "analysis_steps": ["ordered technical reasoning"],
  "critical_suggestions": ["risks, missing tests, follow-ups"],
  "operations": [
    {"op": "write", "path": "relative/file.py", "content": "complete file content"},
    {"op": "delete", "path": "relative/old_file.py"}
  ]
}
```

## Run the Desktop App

This opens a native laptop window. It does not start a web server and does not
need a browser.

```bash
export OPENAI_API_KEY="your_api_key_here"
python desktop_app.py
```

The app lets you choose a repo folder, enter the instruction and test command,
adjust model settings, choose whether clean runs should be applied back to the
source repo, then view events, changed files, critical suggestions, step trace,
test output, and the generated diff. Diffs are saved under
`results/desktop_runs/`.

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
--repo               Run repo mode against a repository path
--instruction        Repo-mode change request
--instruction-file   Repo-mode change request file
--test-command       Repo-mode command such as "python -m pytest -q"
--diff-out           Repo-mode final diff output path
--apply              Apply repo-mode changes back to the source repo
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

Repo mode also records:

```text
changed_paths
operation_batches
analysis_steps
critical_suggestions
snapshot_omitted
final_diff
last_test_output
```

## Validate Locally

```bash
python -m pytest -q
python -m compileall -q harness harness_desktop run.py desktop_app.py
```

You can also run a no-API smoke check by importing `run_task` with a fake model
client in a Python script or REPL.

## Safety Notes

Generated `solution.py`, generated `self_tests.py`, and repo-mode test commands
run in temporary workspaces with sanitized subprocess environments. Secrets
such as `OPENAI_API_KEY` are not inherited by model-generated tests, hidden-test
scoring subprocesses, or repo-mode test commands.

## Design Boundary

This repository is intentionally focused on OpenAI-backed coding harness loops.
Benchmark plotting, local model hosting, non-OpenAI providers, and cloud
orchestration belong outside this core.
