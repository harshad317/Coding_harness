"""Application service for running the harness from the web UI."""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from harness.log import JsonlLogger, RunRecord
from harness.models import build_client
from harness.repo_agent import REPO_MODE, RepoConfig, run_repo_task


TERMINAL_STATUSES = {"succeeded", "failed"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HarnessRunRequest:
    repo_path: str
    instruction: str
    test_command: str
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
class HarnessRunState:
    id: str
    request: HarnessRunRequest
    status: str = "queued"
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    events: list[str] = field(default_factory=list)
    record: dict | None = None
    error: str | None = None
    diff_path: str | None = None

    def append_event(self, message: str) -> None:
        self.events.append(f"{_utc_now()} {message}")
        self.updated_at = _utc_now()

    def to_dict(self) -> dict:
        request = asdict(self.request)
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": list(self.events),
            "record": self.record,
            "error": self.error,
            "diff_path": self.diff_path,
            **request,
        }


class HarnessRunner(Protocol):
    def run(self, run_id: str, request: HarnessRunRequest, diff_path: Path) -> RunRecord:
        ...


class RepoHarnessRunner:
    def __init__(self, *, log_path: Path = Path("results/app_runs/runs.jsonl")):
        self.log_path = Path(log_path)

    def run(self, run_id: str, request: HarnessRunRequest, diff_path: Path) -> RunRecord:
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


class HarnessAppService:
    def __init__(
        self,
        *,
        runner: HarnessRunner | None = None,
        results_dir: Path = Path("results/app_runs"),
        run_inline: bool = False,
    ):
        self.runner = runner or RepoHarnessRunner(log_path=results_dir / "runs.jsonl")
        self.results_dir = Path(results_dir)
        self.run_inline = run_inline
        self._runs: dict[str, HarnessRunState] = {}
        self._lock = threading.Lock()

    def create_run(self, request: HarnessRunRequest) -> HarnessRunState:
        repo = Path(request.repo_path).expanduser().resolve()
        if not repo.is_dir():
            raise ValueError(f"repo path is not a directory: {request.repo_path}")
        if not request.instruction.strip():
            raise ValueError("instruction is required")
        if not request.test_command.strip():
            raise ValueError("test_command is required")

        normalized = HarnessRunRequest(
            **{
                **asdict(request),
                "repo_path": str(repo),
                "instruction": request.instruction.strip(),
                "test_command": request.test_command.strip(),
            }
        )
        run_id = uuid.uuid4().hex
        state = HarnessRunState(id=run_id, request=normalized)
        state.append_event("queued")
        with self._lock:
            self._runs[run_id] = state

        if self.run_inline:
            self._execute(run_id)
        else:
            thread = threading.Thread(target=self._execute, args=(run_id,), daemon=True)
            thread.start()
        return self.get_run(run_id)

    def list_runs(self) -> list[HarnessRunState]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda item: item.created_at, reverse=True)

    def get_run(self, run_id: str) -> HarnessRunState:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError:
                raise KeyError(f"run not found: {run_id}") from None

    def _execute(self, run_id: str) -> None:
        with self._lock:
            state = self._runs[run_id]
            state.status = "running"
            state.append_event("running repo harness")
            request = state.request

        diff_path = self.results_dir / f"{run_id}.patch"
        try:
            record = self.runner.run(run_id, request, diff_path)
            record_dict = asdict(record)
            with self._lock:
                state = self._runs[run_id]
                state.record = record_dict
                state.diff_path = str(diff_path)
                if record.final_error_type is None:
                    state.status = "succeeded"
                    state.append_event("completed successfully")
                else:
                    state.status = "failed"
                    state.error = record.final_error_type
                    state.append_event(f"completed with error: {record.final_error_type}")
        except Exception as exc:
            with self._lock:
                state = self._runs[run_id]
                state.status = "failed"
                state.error = f"{type(exc).__name__}: {exc}"
                state.append_event(f"failed: {state.error}")
