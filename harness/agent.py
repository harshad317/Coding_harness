"""D_val agent loop.

D_val is the self-test repair condition:

1. The model writes ``solution.py`` and ``self_tests.py``.
2. The harness runs only the generated self-tests as feedback.
3. The model repairs both files until the self-tests pass or the budget ends.
4. Hidden tests are scored after the loop and are never shown to the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .log import RunRecord, now
from .models import ChatClient
from .sandbox import RunResult, Sandbox, score_hidden


MODE = "D_val"
MAX_BASH_CALLS = 10
CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class AgentConfig:
    max_iterations: int = 3
    max_bash_calls: int = MAX_BASH_CALLS
    pytest_timeout: int = 10
    hidden_timeout: int = 60


SYSTEM_PROMPT = (
    "You are D_val, a repo-independent Python repair agent. "
    "Write executable production code and write your own pytest self-tests. "
    "Output full files only, never diffs. Put each file in a separate fenced "
    "Python block whose first line names the file, for example:\n"
    "```python\n# solution.py\n...\n```\n"
    "Every response must contain exactly two files in this order: "
    "solution.py, then self_tests.py."
)


def initial_prompt(prompt_md: str, starter: str) -> str:
    return "\n".join(
        [
            "Task description:",
            prompt_md.strip(),
            "",
            "Current solution.py:",
            "```python",
            starter.strip(),
            "```",
            "",
            "D_val protocol:",
            "1. Write the complete corrected solution.py.",
            "2. Write self_tests.py with pytest tests derived only from the task description.",
            "3. self_tests.py must import from solution.py.",
            "4. The harness will run self_tests.py and give you the terminal output.",
            "5. If self_tests.py fails, repair solution.py and/or self_tests.py.",
            "",
            "Output exactly two fenced Python blocks:",
            "```python",
            "# solution.py",
            "<full implementation>",
            "```",
            "```python",
            "# self_tests.py",
            "<pytest tests>",
            "```",
        ]
    )


def feedback_prompt(self_result: RunResult) -> str:
    return "\n\n".join(
        [
            _format_pytest_result("self_tests.py", self_result),
            "Repair the implementation and/or self-tests. Output the full "
            "solution.py first and the full self_tests.py second.",
        ]
    )


def _format_pytest_result(label: str, result: RunResult) -> str:
    status = "PASSED" if result.returncode == 0 else f"FAILED (exit {result.returncode})"
    output = (result.stdout + "\n" + result.stderr).strip()
    if len(output) > 3000:
        output = output[-3000:]
    return f"[{label}] {status}\n```\n{output}\n```"


def _canonical_generated_name(name: str) -> str:
    base = Path(name).name
    if base in {"tests.py", "test.py", "test_solution.py", "solution_tests.py"}:
        return "self_tests.py"
    return base


def _split_marked_block(block: str) -> list[tuple[str, str]]:
    marker_re = re.compile(
        r"^#\s*([\w.\-/]*(?:solution|self_tests|tests?|test_solution|solution_tests)\.py)\b.*$",
        re.MULTILINE,
    )
    markers = list(marker_re.finditer(block))
    pieces: list[tuple[str, str]] = []
    for idx, marker in enumerate(markers):
        start = block.find("\n", marker.start())
        start = len(block) if start == -1 else start + 1
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(block)
        name = _canonical_generated_name(marker.group(1))
        body = block[start:end].strip("\n")
        if body.strip():
            pieces.append((name, body + "\n"))
    return pieces


def _looks_like_pytest_block(block: str) -> bool:
    return bool(re.search(r"(^|\n)\s*def\s+test_", block)) or "from solution import" in block


def extract_files(text: str) -> dict[str, str]:
    """Extract ``solution.py`` and ``self_tests.py`` from model output."""
    out: dict[str, str] = {}
    unnamed: list[str] = []
    for block in CODE_FENCE_RE.findall(text):
        block = block.strip("\n")
        first_line = block.splitlines()[0] if block else ""
        marked = _split_marked_block(block)
        if marked:
            for name, body in marked:
                out[name] = body.lstrip("\n")
            continue
        marker = re.match(r"#\s*([\w.\-/]+\.py)\b", first_line)
        if marker:
            name = _canonical_generated_name(marker.group(1))
            body = "\n".join(block.splitlines()[1:])
            out[name] = body.lstrip("\n")
        else:
            unnamed.append(block)

    for block in unnamed:
        if "solution.py" not in out and not _looks_like_pytest_block(block):
            out["solution.py"] = block + "\n"
        elif "self_tests.py" not in out:
            out["self_tests.py"] = block + "\n"
    return out


def run_task(
    client: ChatClient,
    task_dir: Path,
    cfg: AgentConfig,
    record: RunRecord,
) -> RunRecord:
    sandbox = Sandbox(task_dir)
    start = now()
    last_self: Optional[RunResult] = None
    self_tests_present = False

    try:
        prompt_md = (task_dir / "prompt.md").read_text()
        starter = sandbox.read("solution.py")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": initial_prompt(prompt_md, starter)},
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

            files = extract_files(reply.text)
            if "solution.py" in files:
                sandbox.write("solution.py", files["solution.py"])
            if "self_tests.py" in files:
                sandbox.write("self_tests.py", files["self_tests.py"])
                self_tests_present = True

            if not (sandbox.tmp / "self_tests.py").exists():
                last_self = RunResult(2, "", "self_tests.py was not provided.", False)
            elif sandbox.bash_calls >= cfg.max_bash_calls:
                last_self = RunResult(124, "", "bash call budget exhausted.", False)
            else:
                last_self = sandbox.run_pytest("self_tests.py", cfg.pytest_timeout)

            if last_self.returncode == 0:
                break
            if step == cfg.max_iterations - 1:
                break
            messages.append({"role": "user", "content": feedback_prompt(last_self)})

        hidden = score_hidden(task_dir, sandbox, timeout=cfg.hidden_timeout)
        record.mode = MODE
        record.bash_calls_used = sandbox.bash_calls
        record.extra["pytest_timeout_s"] = cfg.pytest_timeout
        record.extra["hidden_timeout_s"] = cfg.hidden_timeout
        record.extra["self_tests_present"] = self_tests_present
        record.extra["ran_self_tests"] = last_self is not None
        record.extra["hidden_timed_out"] = hidden.timed_out
        record.passed_self = (last_self.returncode == 0) if last_self else None
        record.passed_hidden = hidden.returncode == 0

        if record.final_error_type is None and not record.passed_hidden:
            if not self_tests_present:
                record.final_error_type = "missing_self_tests"
            elif hidden.returncode == 124:
                record.final_error_type = "hidden_timeout"
            elif record.passed_self:
                record.final_error_type = "overfit_self"
            else:
                record.final_error_type = "incorrect"
    finally:
        record.wall_time_s = round(now() - start, 3)
        sandbox.cleanup()
    return record
