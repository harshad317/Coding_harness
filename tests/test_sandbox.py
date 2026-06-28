from pathlib import Path

from harness.sandbox import Sandbox, score_hidden


def write_task(tmp_path: Path) -> Path:
    task = tmp_path / "task_secret"
    task.mkdir()
    (task / "prompt.md").write_text("Implement answer().\n")
    (task / "solution.py").write_text(
        "def answer():\n"
        "    return 2\n"
    )
    (task / "hidden_tests.py").write_text(
        "from pathlib import Path\n"
        "from solution import answer\n"
        "\n"
        "def test_hidden_answer_and_workspace():\n"
        "    assert answer() == 2\n"
        "    assert not Path('self_tests.py').exists()\n"
    )
    return task


def test_generated_tests_do_not_inherit_openai_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-value")
    sandbox = Sandbox(write_task(tmp_path))
    try:
        sandbox.write(
            "self_tests.py",
            "import os\n"
            "\n"
            "def test_secret_env_absent():\n"
            "    assert 'OPENAI_API_KEY' not in os.environ\n"
            "    assert 'test-secret-value' not in os.environ.values()\n",
        )

        result = sandbox.run_pytest("self_tests.py")

        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        sandbox.cleanup()


def test_score_hidden_uses_fresh_directory_without_self_tests(tmp_path):
    task = write_task(tmp_path)
    sandbox = Sandbox(task)
    try:
        sandbox.write(
            "self_tests.py",
            "from solution import answer\n"
            "\n"
            "def test_answer():\n"
            "    assert answer() == 2\n",
        )

        result = score_hidden(task, sandbox)

        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        sandbox.cleanup()
