#!/usr/bin/env python3
"""Run the D_val self-test repair loop."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.agent import AgentConfig, MODE, run_task
from harness.log import JsonlLogger, RunRecord
from harness.models import build_client


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
    args = parser.parse_args()

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
