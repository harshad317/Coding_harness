from harness.models import _extract_response_text, _responses_input


def test_responses_input_maps_system_role_to_developer():
    items = _responses_input(
        [
            {"role": "system", "content": "follow harness protocol"},
            {"role": "user", "content": "solve the task"},
            {"role": "assistant", "content": "```python\n# solution.py\n```"},
        ]
    )

    assert items == [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": "follow harness protocol"}],
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "solve the task"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "```python\n# solution.py\n```"}],
        },
    ]


def test_extract_response_text_prefers_output_text():
    assert _extract_response_text({"output_text": "direct text"}) == "direct text"


def test_extract_response_text_collects_output_chunks():
    body = {
        "output": [
            {"content": [{"type": "output_text", "text": "first"}]},
            {"content": [{"type": "output_text", "text": "second"}]},
        ]
    }

    assert _extract_response_text(body) == "first\nsecond"
