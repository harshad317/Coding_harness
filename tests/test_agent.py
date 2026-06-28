from pathlib import Path

from harness.agent import AgentConfig, extract_files, run_task
from harness.log import RunRecord
from harness.models import ChatResult


class FakeClient:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.messages: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> ChatResult:
        self.messages.append(list(messages))
        return ChatResult(self.replies.pop(0), tokens_in=10, tokens_out=20)


def write_task(tmp_path: Path, hidden: str) -> Path:
    task = tmp_path / "task_answer"
    task.mkdir()
    (task / "prompt.md").write_text("Implement answer() so it returns 2.\n")
    (task / "solution.py").write_text(
        "def answer():\n"
        "    raise NotImplementedError\n"
    )
    (task / "hidden_tests.py").write_text(hidden)
    return task


def model_reply(solution_body: str, self_tests_body: str) -> str:
    return (
        "```python\n"
        "# solution.py\n"
        f"{solution_body}"
        "```\n"
        "```python\n"
        "# self_tests.py\n"
        f"{self_tests_body}"
        "```\n"
    )


def test_extract_files_accepts_py_fences_and_test_aliases():
    files = extract_files(
        "```py\n"
        "# solution.py\n"
        "def answer():\n"
        "    return 2\n"
        "```\n"
        "```PYTHON\n"
        "# tests.py\n"
        "from solution import answer\n"
        "\n"
        "def test_answer():\n"
        "    assert answer() == 2\n"
        "```\n"
    )

    assert set(files) == {"solution.py", "self_tests.py"}
    assert "return 2" in files["solution.py"]
    assert "test_answer" in files["self_tests.py"]


def test_extract_files_splits_two_marked_files_in_one_fence():
    files = extract_files(
        "```python\n"
        "# solution.py\n"
        "def answer():\n"
        "    return 2\n"
        "# self_tests.py\n"
        "from solution import answer\n"
        "\n"
        "def test_answer():\n"
        "    assert answer() == 2\n"
        "```\n"
    )

    assert set(files) == {"solution.py", "self_tests.py"}
    assert files["solution.py"].strip().endswith("return 2")
    assert "from solution import answer" in files["self_tests.py"]


def test_run_task_repairs_until_self_tests_pass_and_scores_hidden(tmp_path):
    task = write_task(
        tmp_path,
        "from solution import answer\n"
        "\n"
        "def test_hidden_answer():\n"
        "    assert answer() == 2\n",
    )
    client = FakeClient(
        [
            model_reply(
                "def answer():\n"
                "    return 1\n",
                "from solution import answer\n"
                "\n"
                "def test_answer():\n"
                "    assert answer() == 2\n",
            ),
            model_reply(
                "def answer():\n"
                "    return 2\n",
                "from solution import answer\n"
                "\n"
                "def test_answer():\n"
                "    assert answer() == 2\n",
            ),
        ]
    )
    record = RunRecord(task_id=task.name, model="fake", mode="D_val", k=3)

    result = run_task(client, task, AgentConfig(max_iterations=3), record)

    assert result.passed_self is True
    assert result.passed_hidden is True
    assert result.final_error_type is None
    assert result.iterations_used == 2
    assert result.bash_calls_used == 2
    assert result.tokens_in == 20
    assert result.tokens_out == 40
    assert "FAILED" in client.messages[1][-1]["content"]


def test_run_task_flags_overfit_self_tests(tmp_path):
    task = write_task(
        tmp_path,
        "from solution import answer\n"
        "\n"
        "def test_hidden_answer():\n"
        "    assert answer() == 2\n",
    )
    client = FakeClient(
        [
            model_reply(
                "def answer():\n"
                "    return 1\n",
                "from solution import answer\n"
                "\n"
                "def test_weak_answer():\n"
                "    assert answer() == 1\n",
            )
        ]
    )
    record = RunRecord(task_id=task.name, model="fake", mode="D_val", k=1)

    result = run_task(client, task, AgentConfig(max_iterations=1), record)

    assert result.passed_self is True
    assert result.passed_hidden is False
    assert result.final_error_type == "overfit_self"
