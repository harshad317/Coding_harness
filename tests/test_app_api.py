from pathlib import Path

from fastapi.testclient import TestClient

from harness.log import RunRecord
from harness_app.main import create_app
from harness_app.service import HarnessAppService, HarnessRunRequest


class FakeRunner:
    def run(self, run_id: str, request: HarnessRunRequest, diff_path: Path) -> RunRecord:
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
        record.extra["critical_suggestions"] = ["Add regression tests around the fixed path."]
        record.extra["final_diff"] = diff_path.read_text()
        return record


def make_client(tmp_path: Path) -> TestClient:
    service = HarnessAppService(
        runner=FakeRunner(),
        results_dir=tmp_path / "results",
        run_inline=True,
    )
    return TestClient(create_app(service))


def test_health_endpoint(tmp_path):
    client = make_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_create_and_fetch_repo_run(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    client = make_client(tmp_path)

    response = client.post(
        "/api/runs",
        json={
            "repo_path": str(repo),
            "instruction": "Fix the repo.",
            "test_command": "python -m pytest -q",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["record"]["extra"]["changed_paths"] == ["app.py"]
    assert body["record"]["extra"]["critical_suggestions"]

    fetched = client.get(f"/api/runs/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    diff = client.get(f"/api/runs/{body['id']}/diff")
    assert diff.status_code == 200
    assert "--- a/app.py" in diff.text


def test_create_run_rejects_missing_repo(tmp_path):
    client = make_client(tmp_path)

    response = client.post(
        "/api/runs",
        json={
            "repo_path": str(tmp_path / "missing"),
            "instruction": "Fix the repo.",
            "test_command": "python -m pytest -q",
        },
    )

    assert response.status_code == 400
    assert "repo path is not a directory" in response.json()["detail"]
