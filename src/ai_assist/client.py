"""
Thin wrapper around a local Ollama server. $0 cost, no API key, no network
dependency beyond localhost. Requires `ollama serve` running and the model
pulled: `ollama pull qwen2.5-coder:7b`.
"""

import json
import urllib.request
import urllib.error

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5-coder:7b"


class AIClientError(Exception):
    pass


def query(prompt: str, model: str = DEFAULT_MODEL, timeout: int = 30) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise AIClientError(
            f"Could not reach Ollama at {OLLAMA_URL} ({e}). "
            f"Is `ollama serve` running and is '{model}' pulled?"
        ) from e
    return data.get("response", "")
