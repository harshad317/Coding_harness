import json
from pathlib import Path

import pytest

from harness.log import RunRecord
from harness.models import ChatResult
from harness.repo_agent import (
    REPO_MODE,
    RepoConfig,
    RepoSandbox,
    apply_operations,
    extract_plan,
    run_repo_task,
)


class FakeClient:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.messages: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> ChatResult:
        self.messages.append(list(messages))
        return ChatResult(self.replies.pop(0), tokens_in=11, tokens_out=22)


def write_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "sample_repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "app.py").write_text(
        "def add(left, right):\n"
        "    return left - right\n"
    )
    (repo / "obsolete.txt").write_text("delete me\n")
    (repo / "tests" / "test_app.py").write_text(
        "from app import add\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )
    return repo


def repo_reply(
    operations: list[dict],
    *,
    steps: list[str] | None = None,
    suggestions: list[str] | None = None,
) -> str:
    payload = {
        "analysis_steps": steps or ["Inspect failing behavior", "Apply minimal repo edits"],
        "critical_suggestions": suggestions or ["Keep tests focused on the public contract"],
        "operations": operations,
    }
    return f"```json\n{json.dumps(payload)}\n```"


def test_extract_plan_from_fenced_json():
    plan = extract_plan(
        repo_reply(
            [
                {
                    "op": "write",
                    "path": "app.py",
                    "content": "def add(left, right):\n    return left + right\n",
                }
            ]
        )
    )

    assert plan.operations[0]["path"] == "app.py"
    assert plan.analysis_steps
    assert plan.critical_suggestions


def test_repo_task_can_add_edit_delete_and_pass_tests(tmp_path):
    repo = write_python_repo(tmp_path)
    diff_out = tmp_path / "repo_fix.patch"
    client = FakeClient(
        [
            repo_reply(
                [
                    {
                        "op": "write",
                        "path": "app.py",
                        "content": "def add(left, right):\n    return left + right\n",
                    },
                    {
                        "op": "write",
                        "path": "README.md",
                        "content": "# Sample repo\n\nSupports add().\n",
                    },
                    {"op": "delete", "path": "obsolete.txt"},
                ],
                suggestions=["Add subtraction tests if subtraction is later required"],
            )
        ]
    )
    record = RunRecord(task_id=repo.name, model="fake", mode=REPO_MODE, k=2)

    result = run_repo_task(
        client,
        repo,
        "Fix add, remove obsolete docs, and add a README.",
        "python -m pytest -q",
        RepoConfig(max_iterations=2, diff_out=diff_out),
        record,
    )

    assert result.passed_self is True
    assert result.final_error_type is None
    assert result.iterations_used == 1
    assert result.bash_calls_used == 1
    assert result.extra["changed_paths"] == ["README.md", "app.py", "obsolete.txt"]
    assert "Add subtraction tests" in result.extra["critical_suggestions"][0]
    assert "--- a/app.py" in result.extra["final_diff"]
    assert "--- /dev/null" in diff_out.read_text()
    assert "b/README.md" in diff_out.read_text()
    assert repo.joinpath("obsolete.txt").exists()
    assert not repo.joinpath("README.md").exists()


def test_repo_task_repairs_after_test_failure(tmp_path):
    repo = write_python_repo(tmp_path)
    client = FakeClient(
        [
            repo_reply(
                [
                    {
                        "op": "write",
                        "path": "app.py",
                        "content": "def add(left, right):\n    return 0\n",
                    }
                ]
            ),
            repo_reply(
                [
                    {
                        "op": "write",
                        "path": "app.py",
                        "content": "def add(left, right):\n    return left + right\n",
                    }
                ]
            ),
        ]
    )
    record = RunRecord(task_id=repo.name, model="fake", mode=REPO_MODE, k=2)

    result = run_repo_task(
        client,
        repo,
        "Fix add.",
        "python -m pytest -q",
        RepoConfig(max_iterations=2),
        record,
    )

    assert result.passed_self is True
    assert result.final_error_type is None
    assert result.iterations_used == 2
    assert result.bash_calls_used == 2
    assert "FAILED" in client.messages[1][-1]["content"]


def test_repo_task_can_apply_changes_back_to_source(tmp_path):
    repo = write_python_repo(tmp_path)
    client = FakeClient(
        [
            repo_reply(
                [
                    {
                        "op": "write",
                        "path": "app.py",
                        "content": "def add(left, right):\n    return left + right\n",
                    },
                    {"op": "delete", "path": "obsolete.txt"},
                ]
            )
        ]
    )
    record = RunRecord(task_id=repo.name, model="fake", mode=REPO_MODE, k=1)

    result = run_repo_task(
        client,
        repo,
        "Fix add and delete obsolete file.",
        "python -m pytest -q",
        RepoConfig(max_iterations=1, apply=True),
        record,
    )

    assert result.passed_self is True
    assert result.extra["applied"] is True
    assert "left + right" in repo.joinpath("app.py").read_text()
    assert not repo.joinpath("obsolete.txt").exists()


def test_repo_task_does_not_apply_failing_changes_to_source(tmp_path):
    repo = write_python_repo(tmp_path)
    client = FakeClient(
        [
            repo_reply(
                [
                    {
                        "op": "write",
                        "path": "app.py",
                        "content": "def add(left, right):\n    return 0\n",
                    }
                ]
            )
        ]
    )
    record = RunRecord(task_id=repo.name, model="fake", mode=REPO_MODE, k=1)

    result = run_repo_task(
        client,
        repo,
        "Fix add.",
        "python -m pytest -q",
        RepoConfig(max_iterations=1, apply=True),
        record,
    )

    assert result.final_error_type == "tests_failed"
    assert result.extra["applied"] is False
    assert "left - right" in repo.joinpath("app.py").read_text()


def test_repo_mode_test_command_uses_sanitized_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "repo-secret-value")
    repo = tmp_path / "env_repo"
    repo.mkdir()
    (repo / "test_env.py").write_text(
        "import os\n"
        "\n"
        "\n"
        "def test_secret_absent():\n"
        "    assert 'OPENAI_API_KEY' not in os.environ\n"
        "    assert 'repo-secret-value' not in os.environ.values()\n"
    )
    client = FakeClient(
        [
            repo_reply(
                [
                    {
                        "op": "write",
                        "path": "NOTE.md",
                        "content": "environment smoke test\n",
                    }
                ]
            )
        ]
    )
    record = RunRecord(task_id=repo.name, model="fake", mode=REPO_MODE, k=1)

    result = run_repo_task(
        client,
        repo,
        "Do not change files; run tests.",
        "python -m pytest -q",
        RepoConfig(max_iterations=1),
        record,
    )

    assert result.passed_self is True
    assert result.final_error_type is None
    assert result.bash_calls_used == 1


def test_repo_operations_reject_paths_outside_workspace(tmp_path):
    sandbox = RepoSandbox(write_python_repo(tmp_path))
    try:
        with pytest.raises(ValueError, match="unsafe repo path"):
            apply_operations(
                sandbox,
                [{"op": "write", "path": "../escape.py", "content": "print('bad')\n"}],
            )
    finally:
        sandbox.cleanup()


def test_repo_operations_reject_ignored_paths(tmp_path):
    sandbox = RepoSandbox(write_python_repo(tmp_path))
    try:
        with pytest.raises(ValueError, match="ignored repo path"):
            apply_operations(
                sandbox,
                [{"op": "write", "path": ".git/config", "content": "[core]\n"}],
            )
    finally:
        sandbox.cleanup()
