"""OpenAI model client for D_val."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int


class ChatClient(Protocol):
    def chat(self, messages: list[dict]) -> ChatResult:
        ...


class OpenAIResponsesClient:
    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        timeout: int = 300,
    ):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required")

    def chat(self, messages: list[dict]) -> ChatResult:
        payload = {
            "model": self.model,
            "input": _responses_input(messages),
            "max_output_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        request = urllib.request.Request(
            OPENAI_RESPONSES_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout,
                context=_ssl_context(),
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"OpenAI request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI request failed: {exc}") from exc

        usage = body.get("usage") or {}
        return ChatResult(
            text=_extract_response_text(body),
            tokens_in=int(usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("output_tokens") or 0),
        )


def _responses_input(messages: list[dict]) -> list[dict]:
    items: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or "")
        if role == "system":
            role = "developer"
        content_type = "output_text" if role == "assistant" else "input_text"
        items.append(
            {
                "role": role,
                "content": [{"type": content_type, "text": content}],
            }
        )
    return items


def _extract_response_text(body: dict) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    chunks: list[str] = []
    for item in body.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def _ssl_context() -> ssl.SSLContext | None:
    try:
        import truststore
    except ImportError:
        pass
    else:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


def build_client(
    model_id: str,
    *,
    backend: str = "openai",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    timeout: int = 300,
    **_: object,
) -> ChatClient:
    if backend != "openai":
        raise ValueError("D_val-only repo supports only backend='openai'")
    return OpenAIResponsesClient(
        model=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )
