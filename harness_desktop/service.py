"""Desktop app service for running repo-mode harness jobs."""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

from harness.log import JsonlLogger, RunRecord
from harness.models import build_client
from harness.repo_agent import REPO_MODE, RepoConfig, run_repo_task


EventCallback = Callable[[str], None]


@dataclass
class DesktopRunRequest:
    repo_path: str
    instruction: str
    test_command: str = "python -m pytest -q"
    model: str = "gpt-5.4-mini"
    max_iterations: int = 3
    max_bash_calls: int = 10
    repo_timeout: int = 60
    max_tokens: int = 4096
    temperature: float = 0.0
    model_timeout: int = 300
    max_repo_bytes: int = 200_000
    max_file_bytes: int = 30_000
    apply: bool = False


@dataclass
class DesktopRunResult:
    run_id: str
    status: str
    record: RunRecord | None
    diff_path: Path
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class DesktopHarnessRunner(Protocol):
    def run(self, run_id: str, request: DesktopRunRequest, diff_path: Path) -> RunRecord:
        ...


class RepoDesktopRunner:
    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)

    def run(self, run_id: str, request: DesktopRunRequest, diff_path: Path) -> RunRecord:
        client = build_client(
            request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            timeout=request.model_timeout,
        )
        record = RunRecord(
            task_id=Path(request.repo_path).resolve().name,
            model=request.model,
            mode=REPO_MODE,
            k=request.max_iterations,
        )
        run_repo_task(
            client,
            Path(request.repo_path),
            request.instruction,
            request.test_command,
            RepoConfig(
                max_iterations=request.max_iterations,
                max_bash_calls=request.max_bash_calls,
                command_timeout=request.repo_timeout,
                max_repo_bytes=request.max_repo_bytes,
                max_file_bytes=request.max_file_bytes,
                apply=request.apply,
                diff_out=diff_path,
            ),
            record,
        )
        JsonlLogger(self.log_path).write(record)
        return record


class DesktopHarnessService:
    def __init__(
        self,
        *,
        runner: DesktopHarnessRunner | None = None,
        results_dir: Path = Path("results/desktop_runs"),
    ):
        self.results_dir = Path(results_dir)
        self.runner = runner or RepoDesktopRunner(self.results_dir / "runs.jsonl")

    def validate_request(self, request: DesktopRunRequest) -> DesktopRunRequest:
        repo = Path(request.repo_path).expanduser().resolve()
        if not repo.is_dir():
            raise ValueError(f"repo path is not a directory: {request.repo_path}")
        if not request.instruction.strip():
            raise ValueError("instruction is required")
        if not request.test_command.strip():
            raise ValueError("test command is required")
        return DesktopRunRequest(
            **{
                **asdict(request),
                "repo_path": str(repo),
                "instruction": request.instruction.strip(),
                "test_command": request.test_command.strip(),
            }
        )

    def run_sync(self, request: DesktopRunRequest, on_event: EventCallback | None = None) -> DesktopRunResult:
        normalized = self.validate_request(request)
        run_id = uuid.uuid4().hex
        diff_path = self.results_dir / f"{run_id}.patch"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._emit(on_event, "Starting repo harness")
        self._emit(on_event, f"Repo: {normalized.repo_path}")
        self._emit(on_event, f"Test command: {normalized.test_command}")

        try:
            record = self.runner.run(run_id, normalized, diff_path)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._emit(on_event, f"Failed: {message}")
            return DesktopRunResult(run_id, "failed", None, diff_path, message)

        if record.final_error_type is None:
            self._emit(on_event, "Run completed successfully")
            return DesktopRunResult(run_id, "succeeded", record, diff_path)

        self._emit(on_event, f"Run completed with error: {record.final_error_type}")
        return DesktopRunResult(run_id, "failed", record, diff_path, record.final_error_type)

    def start_background(
        self,
        request: DesktopRunRequest,
        *,
        on_event: EventCallback | None = None,
        on_complete: Callable[[DesktopRunResult], None] | None = None,
    ) -> threading.Thread:
        def target() -> None:
            result = self.run_sync(request, on_event=on_event)
            if on_complete:
                on_complete(result)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _emit(callback: EventCallback | None, message: str) -> None:
        if callback:
            callback(message)
