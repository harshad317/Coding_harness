from pathlib import Path

import pytest

from harness.log import RunRecord
from harness_desktop.service import DesktopHarnessService, DesktopRunRequest


class FakeRunner:
    def run(self, run_id: str, request: DesktopRunRequest, diff_path: Path) -> RunRecord:
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text("--- a/app.py\n+++ b/app.py\n")
        record = RunRecord(
            task_id=Path(request.repo_path).name,
            model=request.model,
            mode="repo",
            k=request.max_iterations,
            passed_self=True,
            passed_hidden=True,
            iterations_used=1,
            bash_calls_used=1,
            tokens_in=10,
            tokens_out=20,
        )
        record.extra["changed_paths"] = ["app.py"]
        record.extra["critical_suggestions"] = ["Add a regression test for the fixed path."]
        record.extra["final_diff"] = diff_path.read_text()
        record.extra["last_test_output"] = "1 passed"
        return record


def test_desktop_service_runs_repo_harness_with_fake_runner(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    events: list[str] = []
    service = DesktopHarnessService(runner=FakeRunner(), results_dir=tmp_path / "runs")

    result = service.run_sync(
        DesktopRunRequest(
            repo_path=str(repo),
            instruction="Fix the repo.",
            test_command="python -m pytest -q",
        ),
        on_event=events.append,
    )

    assert result.succeeded is True
    assert result.record is not None
    assert result.record.extra["changed_paths"] == ["app.py"]
    assert result.diff_path.read_text().startswith("--- a/app.py")
    assert any("Starting repo harness" in event for event in events)


def test_desktop_service_rejects_missing_repo(tmp_path):
    service = DesktopHarnessService(runner=FakeRunner(), results_dir=tmp_path / "runs")

    with pytest.raises(ValueError, match="repo path is not a directory"):
        service.validate_request(
            DesktopRunRequest(
                repo_path=str(tmp_path / "missing"),
                instruction="Fix the repo.",
                test_command="python -m pytest -q",
            )
        )


def test_desktop_service_rejects_blank_instruction(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    service = DesktopHarnessService(runner=FakeRunner(), results_dir=tmp_path / "runs")

    with pytest.raises(ValueError, match="instruction is required"):
        service.validate_request(
            DesktopRunRequest(
                repo_path=str(repo),
                instruction=" ",
                test_command="python -m pytest -q",
            )
        )
