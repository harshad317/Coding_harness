"""Repo-mode coding harness.

Repo mode copies a whole source tree into an isolated workspace, asks the model
for structured file operations, runs a project test command, and feeds failures
back through the same repair loop used by the single-file harness.
"""
from __future__ import annotations

import difflib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .log import RunRecord, now
from .models import ChatClient
from .sandbox import RunResult, _safe_subprocess_env


REPO_MODE = "repo"
DEFAULT_REPO_TIMEOUT = 60
DEFAULT_MAX_REPO_BYTES = 200_000
DEFAULT_MAX_FILE_BYTES = 30_000
DIFF_LOG_LIMIT = 30_000
OUTPUT_LOG_LIMIT = 6_000
JSON_FENCE_RE = re.compile(r"```(?:json)?[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)
IGNORE_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "results",
    "tasks_bench",
    "venv",
}
IGNORE_SUFFIXES = {
    ".egg-info",
    ".pyc",
    ".pyo",
}


@dataclass
class RepoConfig:
    max_iterations: int = 3
    max_bash_calls: int = 10
    command_timeout: int = DEFAULT_REPO_TIMEOUT
    max_repo_bytes: int = DEFAULT_MAX_REPO_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    apply: bool = False
    diff_out: Path | None = None


@dataclass
class RepoPlan:
    operations: list[dict[str, Any]]
    analysis_steps: list[str]
    critical_suggestions: list[str]


class RepoSandbox:
    def __init__(self, source_dir: Path):
        self.source_dir = Path(source_dir).resolve()
        if not self.source_dir.is_dir():
            raise ValueError(f"repo path is not a directory: {source_dir}")
        self.tmp = Path(tempfile.mkdtemp(prefix="codehyp_repo_"))
        shutil.copytree(
            self.source_dir,
            self.tmp,
            dirs_exist_ok=True,
            ignore=_copy_ignore,
        )
        self.bash_calls = 0
        self.original_files = self.read_text_files()

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def safe_path(self, name: str) -> Path:
        if not name or Path(name).is_absolute():
            raise ValueError(f"unsafe repo path: {name}")
        if _is_ignored_path(Path(name)):
            raise ValueError(f"ignored repo path: {name}")
        safe = (self.tmp / name).resolve()
        try:
            safe.relative_to(self.tmp.resolve())
        except ValueError:
            raise ValueError(f"unsafe repo path: {name}") from None
        return safe

    def source_path(self, name: str) -> Path:
        safe = (self.source_dir / name).resolve()
        try:
            safe.relative_to(self.source_dir)
        except ValueError:
            raise ValueError(f"unsafe source path: {name}") from None
        return safe

    def write_file(self, name: str, content: str) -> None:
        path = self.safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def delete_file(self, name: str) -> None:
        path = self.safe_path(name)
        if path.exists() and not path.is_file():
            raise ValueError(f"delete supports files only: {name}")
        path.unlink(missing_ok=True)

    def run(self, command: str, timeout: int = DEFAULT_REPO_TIMEOUT) -> RunResult:
        self.bash_calls += 1
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return RunResult(2, "", f"invalid test command: {exc}", False)
        if not argv:
            return RunResult(2, "", "empty test command", False)
        try:
            proc = subprocess.run(
                argv,
                cwd=self.tmp,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_safe_subprocess_env(),
            )
            return RunResult(proc.returncode, proc.stdout, proc.stderr, False)
        except OSError as exc:
            return RunResult(127, "", f"test command failed to start: {exc}", False)
        except subprocess.TimeoutExpired as exc:
            return RunResult(
                returncode=124,
                stdout=_timeout_text(exc.stdout),
                stderr=_timeout_text(exc.stderr),
                timed_out=True,
            )

    def relative_files(self) -> list[str]:
        files: list[str] = []
        for path in self.tmp.rglob("*"):
            if path.is_file() and not _is_ignored_path(path.relative_to(self.tmp)):
                files.append(path.relative_to(self.tmp).as_posix())
        return sorted(files)

    def read_text_files(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel in self.relative_files():
            path = self.tmp / rel
            text = _read_text(path)
            if text is not None:
                out[rel] = text
        return out

    def snapshot_text(self, max_repo_bytes: int, max_file_bytes: int) -> tuple[str, list[str]]:
        files = self.relative_files()
        omitted: list[str] = []
        parts = [
            "Repository tree:",
            "\n".join(files) if files else "<empty>",
            "",
            "Repository file contents:",
        ]
        used = sum(len(part) for part in parts)
        for rel in files:
            path = self.tmp / rel
            raw_size = path.stat().st_size
            text = _read_text(path)
            if text is None:
                omitted.append(f"{rel}: binary or non-UTF-8")
                continue
            if raw_size > max_file_bytes:
                omitted.append(f"{rel}: file too large ({raw_size} bytes)")
                continue
            block = f"\n--- {rel} ---\n{text}"
            if used + len(block) > max_repo_bytes:
                omitted.append(f"{rel}: repo snapshot byte limit reached")
                continue
            parts.append(block)
            used += len(block)
        if omitted:
            parts.extend(["", "Omitted files:", "\n".join(omitted)])
        return "\n".join(parts), omitted

    def diff(self) -> str:
        current = self.read_text_files()
        lines: list[str] = []
        for rel in sorted(set(self.original_files) | set(current)):
            old = self.original_files.get(rel)
            new = current.get(rel)
            if old == new:
                continue
            old_lines = [] if old is None else old.splitlines(keepends=True)
            new_lines = [] if new is None else new.splitlines(keepends=True)
            lines.extend(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{rel}" if old is not None else "/dev/null",
                    tofile=f"b/{rel}" if new is not None else "/dev/null",
                )
            )
        return "".join(lines)

    def changed_paths(self) -> list[str]:
        current = self.read_text_files()
        return sorted(rel for rel in set(self.original_files) | set(current) if self.original_files.get(rel) != current.get(rel))

    def apply_to_source(self, paths: list[str]) -> None:
        for rel in paths:
            src = self.safe_path(rel)
            dst = self.source_path(rel)
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            else:
                dst.unlink(missing_ok=True)


SYSTEM_PROMPT = """You are D_val repo mode, an end-to-end coding harness agent.
You edit whole repositories step by step. You may add, edit, or delete files.
Use the repository snapshot as authoritative. Preserve unrelated behavior.

Respond with exactly one fenced JSON block and no prose outside it:
```json
{
  "analysis_steps": [
    "step-by-step technical reasoning about what must change"
  ],
  "critical_suggestions": [
    "important risks, follow-ups, missing tests, or design suggestions"
  ],
  "operations": [
    {"op": "write", "path": "relative/path.py", "content": "complete file content"},
    {"op": "delete", "path": "relative/obsolete_file.py"}
  ]
}
```

Rules:
- Use relative paths only.
- For edits and new files, use op="write" with complete final file content.
- For deletions, use op="delete".
- Do not output diffs.
- Do not include secrets, absolute paths, or files outside the repo.
- Keep analysis_steps concrete and ordered.
"""


def initial_prompt(
    instruction: str,
    snapshot: str,
    *,
    test_command: str | None,
    omitted: list[str],
) -> str:
    omitted_text = "\n".join(omitted) if omitted else "None"
    return "\n".join(
        [
            "User change request:",
            instruction.strip(),
            "",
            f"Test command: {test_command or 'No test command provided'}",
            "",
            "Snapshot coverage notes:",
            omitted_text,
            "",
            snapshot,
            "",
            "Work step by step, then output the JSON file-operation plan.",
        ]
    )


def feedback_prompt(result: RunResult, diff_text: str) -> str:
    return "\n\n".join(
        [
            _format_command_result(result),
            "Current diff:",
            f"```diff\n{_tail(diff_text, DIFF_LOG_LIMIT)}\n```",
            "Repair the repository by outputting a new JSON operation plan.",
        ]
    )


def extract_plan(text: str) -> RepoPlan:
    candidates = [text.strip()]
    candidates.extend(block.strip() for block in JSON_FENCE_RE.findall(text))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("operations"), list):
            return RepoPlan(
                operations=list(data["operations"]),
                analysis_steps=_string_list(data.get("analysis_steps")),
                critical_suggestions=_string_list(data.get("critical_suggestions")),
            )
    raise ValueError("model response did not contain a JSON plan with operations")


def apply_operations(sandbox: RepoSandbox, operations: list[dict[str, Any]]) -> list[str]:
    changed: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("operation must be an object")
        op = str(operation.get("op") or operation.get("action") or "").lower()
        path = str(operation.get("path") or "")
        if op in {"write", "create", "edit", "update"}:
            content = operation.get("content")
            if not isinstance(content, str):
                raise ValueError(f"write operation for {path} requires string content")
            sandbox.write_file(path, content)
            changed.append(path)
        elif op in {"delete", "remove"}:
            sandbox.delete_file(path)
            changed.append(path)
        else:
            raise ValueError(f"unsupported repo operation: {op or '<missing>'}")
    return sorted(set(changed))


def run_repo_task(
    client: ChatClient,
    repo_dir: Path,
    instruction: str,
    test_command: str | None,
    cfg: RepoConfig,
    record: RunRecord,
) -> RunRecord:
    sandbox = RepoSandbox(repo_dir)
    start = now()
    last_test: RunResult | None = None
    all_steps: list[str] = []
    all_suggestions: list[str] = []
    operation_batches: list[dict[str, Any]] = []

    try:
        snapshot, omitted = sandbox.snapshot_text(cfg.max_repo_bytes, cfg.max_file_bytes)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": initial_prompt(
                    instruction,
                    snapshot,
                    test_command=test_command,
                    omitted=omitted,
                ),
            },
        ]

        for step in range(cfg.max_iterations):
            record.iterations_used = step + 1
            try:
                reply = client.chat(messages)
            except Exception as exc:
                record.final_error_type = f"model_error:{type(exc).__name__}:{exc}"
                break

            record.tokens_in += reply.tokens_in
            record.tokens_out += reply.tokens_out
            messages.append({"role": "assistant", "content": reply.text})

            try:
                plan = extract_plan(reply.text)
                changed_this_step = apply_operations(sandbox, plan.operations)
            except Exception as exc:
                record.final_error_type = f"plan_error:{type(exc).__name__}:{exc}"
                break

            all_steps.extend(plan.analysis_steps)
            all_suggestions.extend(plan.critical_suggestions)
            operation_batches.append(
                {
                    "iteration": step + 1,
                    "changed_paths": changed_this_step,
                    "operation_count": len(plan.operations),
                }
            )

            if not plan.operations:
                record.final_error_type = "missing_operations"
                break

            if not test_command:
                last_test = RunResult(0, "No test command provided.", "", False)
                break
            if sandbox.bash_calls >= cfg.max_bash_calls:
                last_test = RunResult(124, "", "bash call budget exhausted.", False)
                break

            last_test = sandbox.run(test_command, timeout=cfg.command_timeout)
            if last_test.returncode == 0:
                break
            if step == cfg.max_iterations - 1:
                break
            messages.append({"role": "user", "content": feedback_prompt(last_test, sandbox.diff())})

        diff_text = sandbox.diff()
        changed_paths = sandbox.changed_paths()

        record.mode = REPO_MODE
        record.bash_calls_used = sandbox.bash_calls
        record.passed_self = (last_test.returncode == 0) if last_test else None
        record.passed_hidden = record.passed_self
        record.extra["test_command"] = test_command
        record.extra["command_timeout_s"] = cfg.command_timeout
        record.extra["apply"] = cfg.apply
        record.extra["applied"] = False
        record.extra["changed_paths"] = changed_paths
        record.extra["operation_batches"] = operation_batches
        record.extra["analysis_steps"] = all_steps
        record.extra["critical_suggestions"] = all_suggestions
        record.extra["snapshot_omitted"] = omitted
        record.extra["final_diff"] = _truncate(diff_text, DIFF_LOG_LIMIT)
        record.extra["final_diff_truncated"] = len(diff_text) > DIFF_LOG_LIMIT
        if last_test:
            record.extra["last_test_returncode"] = last_test.returncode
            record.extra["last_test_timed_out"] = last_test.timed_out
            record.extra["last_test_output"] = _truncate(
                (last_test.stdout + "\n" + last_test.stderr).strip(),
                OUTPUT_LOG_LIMIT,
            )
        if record.final_error_type is None and last_test and last_test.returncode != 0:
            record.final_error_type = "tests_failed"
        if cfg.diff_out:
            cfg.diff_out.parent.mkdir(parents=True, exist_ok=True)
            cfg.diff_out.write_text(diff_text)
            record.extra["diff_out"] = str(cfg.diff_out)
        if cfg.apply and changed_paths and record.final_error_type is None:
            sandbox.apply_to_source(changed_paths)
            record.extra["applied"] = True
    finally:
        record.wall_time_s = round(now() - start, 3)
        sandbox.cleanup()
    return record


def _copy_ignore(_dir: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in IGNORE_DIRS or any(name.endswith(suffix) for suffix in IGNORE_SUFFIXES):
            ignored.add(name)
    return ignored


def _is_ignored_path(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts) or any(path.name.endswith(suffix) for suffix in IGNORE_SUFFIXES)


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _format_command_result(result: RunResult) -> str:
    status = "PASSED" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    output = _tail((result.stdout + "\n" + result.stderr).strip(), OUTPUT_LOG_LIMIT)
    return f"[test command] {status}\n```\n{output}\n```"


def _timeout_text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]
