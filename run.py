#!/usr/bin/env python3
"""Run the D_val self-test repair loop."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.agent import AgentConfig, MODE, run_task
from harness.log import JsonlLogger, RunRecord
from harness.models import build_client
from harness.repo_agent import REPO_MODE, RepoConfig, run_repo_task


def collect_tasks(args: argparse.Namespace) -> list[Path]:
    if args.all:
        return sorted(path for path in Path(args.tasks_root).iterdir() if path.is_dir())
    return [Path(task) for task in args.tasks]


def status(value: bool | None) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "-"


def read_instruction(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.instruction:
        parts.append(args.instruction)
    if args.instruction_file:
        parts.append(Path(args.instruction_file).read_text())
    return "\n\n".join(part.strip() for part in parts if part.strip())


def run_repo_mode(args: argparse.Namespace) -> int:
    instruction = read_instruction(args)
    if not instruction:
        print("repo mode requires --instruction or --instruction-file", file=sys.stderr)
        return 2

    client = build_client(
        args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.model_timeout,
    )
    logger = JsonlLogger(Path(args.log))
    config = RepoConfig(
        max_iterations=args.max_iterations,
        max_bash_calls=args.max_bash_calls,
        command_timeout=args.repo_timeout,
        max_repo_bytes=args.max_repo_bytes,
        max_file_bytes=args.max_file_bytes,
        apply=args.apply,
        diff_out=Path(args.diff_out) if args.diff_out else None,
    )
    repo_dir = Path(args.repo)
    record = RunRecord(task_id=repo_dir.name, model=args.model, mode=REPO_MODE, k=args.max_iterations)
    try:
        run_repo_task(client, repo_dir, instruction, args.test_command, config, record)
    except Exception as exc:
        record.final_error_type = f"harness_error:{type(exc).__name__}:{exc}"
    logger.write(record)

    suggestions = record.extra.get("critical_suggestions") or []
    changed = record.extra.get("changed_paths") or []
    print(
        f"repo={repo_dir} tests={status(record.passed_self)} "
        f"iters={record.iterations_used} bash={record.bash_calls_used} "
        f"tokens={record.tokens_in + record.tokens_out} "
        f"changed={len(changed)} error={record.final_error_type or '-'}"
    )
    if changed:
        print("changed paths:")
        for path in changed:
            print(f"  {path}")
    if suggestions:
        print("critical suggestions:")
        for item in suggestions[:5]:
            print(f"  - {item}")
    if args.diff_out:
        print(f"wrote diff: {args.diff_out}")
    print(f"wrote log: {args.log}")
    return 0 if record.final_error_type is None else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--tasks", nargs="*", default=[])
    parser.add_argument("--all", action="store_true", help="run all tasks under --tasks-root")
    parser.add_argument("--tasks-root", default="tasks")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--max-bash-calls", type=int, default=10)
    parser.add_argument("--pytest-timeout", type=int, default=10)
    parser.add_argument("--hidden-timeout", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--model-timeout", type=int, default=300)
    parser.add_argument("--log", default="results/dval_runs.jsonl")
    parser.add_argument("--repo", help="run repo-mode harness against this repository path")
    parser.add_argument("--instruction", help="repo-mode change request")
    parser.add_argument("--instruction-file", help="file containing repo-mode change request")
    parser.add_argument("--test-command", help="repo-mode test command, for example: python -m pytest -q")
    parser.add_argument("--repo-timeout", type=int, default=60, help="repo-mode test command timeout in seconds")
    parser.add_argument("--max-repo-bytes", type=int, default=200_000, help="repo snapshot budget sent to the model")
    parser.add_argument("--max-file-bytes", type=int, default=30_000, help="max size for one file in repo snapshot")
    parser.add_argument("--diff-out", help="write repo-mode final diff to this path")
    parser.add_argument("--apply", action="store_true", help="apply repo-mode changes back to the source repo")
    args = parser.parse_args()

    if args.repo:
        return run_repo_mode(args)

    tasks = collect_tasks(args)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    if not tasks:
        print("no tasks specified; use --tasks or --all", file=sys.stderr)
        return 2

    client = build_client(
        args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.model_timeout,
    )
    logger = JsonlLogger(Path(args.log))
    config = AgentConfig(
        max_iterations=args.max_iterations,
        max_bash_calls=args.max_bash_calls,
        pytest_timeout=args.pytest_timeout,
        hidden_timeout=args.hidden_timeout,
    )

    passed = 0
    for index, task_dir in enumerate(tasks, start=1):
        record = RunRecord(task_id=task_dir.name, model=args.model, mode=MODE, k=args.max_iterations)
        print(f"[{index}/{len(tasks)}] {task_dir.name} ... ", end="", flush=True)
        try:
            run_task(client, task_dir, config, record)
        except Exception as exc:
            record.final_error_type = f"harness_error:{type(exc).__name__}:{exc}"
        logger.write(record)
        passed += int(record.passed_hidden is True)
        print(
            f"hidden={status(record.passed_hidden)} "
            f"self={status(record.passed_self)} "
            f"iters={record.iterations_used} "
            f"bash={record.bash_calls_used} "
            f"tokens={record.tokens_in + record.tokens_out} "
            f"error={record.final_error_type or '-'}"
        )

    print(f"\nD_val hidden pass rate: {passed}/{len(tasks)} ({passed / len(tasks):.1%})")
    print(f"wrote log: {args.log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
