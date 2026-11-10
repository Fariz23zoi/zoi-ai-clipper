import json
import urllib.request
import urllib.error

from .config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    CLAUDE_API_KEY,
    CLAUDE_MODEL,
)


def call_claude_llm(prompt: str) -> str:
    if not CLAUDE_API_KEY:
        raise RuntimeError("CLAUDE_API_KEY belum diatur.")

    data = {
        "model": CLAUDE_MODEL,
        "max_tokens": 8192,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(data).encode("utf-8"),
        headers={
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    return _send_request(req, "claude")


def call_chatgpt_llm(prompt: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY belum diatur.")

    data = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    return _send_request(req, "chatgpt")


def _send_request(req, provider):
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))

        if provider == "claude":
            return "".join(
                block.get("text", "")
                for block in result.get("content", [])
                if block.get("type") == "text"
            )

        return result["choices"][0]["message"]["content"] or ""

    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{provider} API error ({error.code}): {detail[:1000]}"
        ) from error

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Gagal menghubungi {provider}: {error.reason}"
        ) from error
