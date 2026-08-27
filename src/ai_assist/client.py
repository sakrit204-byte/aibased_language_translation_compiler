"""
Thin wrapper around a local Ollama server. $0 cost, no API key, no network
traffic beyond localhost. Requires `ollama serve` running and a model pulled,
e.g. `ollama pull qwen2.5-coder:7b`.

Sampling is pinned to temperature 0 with a fixed seed. A compiler that emits
different output for the same input on two runs is not a compiler, and the
whole point of the gate is that its decisions are reproducible and auditable.
"""

import json
import urllib.error
import urllib.request

OLLAMA_HOST = "http://localhost:11434"
GENERATE_URL = OLLAMA_HOST + "/api/generate"
TAGS_URL = OLLAMA_HOST + "/api/tags"

DEFAULT_MODEL = "qwen2.5-coder:7b"
SEED = 0

# Models offered in the GUI when the Ollama server cannot be reached.
FALLBACK_MODELS = [
    "qwen2.5-coder:7b",
    "qwen2.5-coder:3b",
    "codellama:7b",
    "deepseek-coder:6.7b",
]


class AIClientError(Exception):
    pass


def query(prompt: str, model: str = None, timeout: int = 60) -> str:
    """Send `prompt` to the local model and return its raw text response."""
    model = model or DEFAULT_MODEL
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "top_p": 1, "seed": SEED},
    }).encode()
    req = urllib.request.Request(
        GENERATE_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise AIClientError(
            f"Ollama returned HTTP {e.code} for model {model!r}: {detail}"
        ) from e
    except (urllib.error.URLError, OSError) as e:
        raise AIClientError(
            f"Could not reach Ollama at {OLLAMA_HOST} ({e}). "
            f"Is `ollama serve` running and has {model!r} been pulled?"
        ) from e
    except json.JSONDecodeError as e:
        raise AIClientError(f"Ollama returned a malformed response: {e}") from e

    if "error" in data:
        raise AIClientError(f"Ollama error: {data['error']}")
    return data.get("response", "")


def list_models(timeout: int = 3):
    """Names of the models installed locally, or the fallback list if the
    server is not reachable. Used to populate the GUI dropdown."""
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        names = [m["name"] for m in data.get("models", []) if m.get("name")]
        return sorted(names) or list(FALLBACK_MODELS)
    except Exception:
        return list(FALLBACK_MODELS)


def server_available(timeout: int = 2) -> bool:
    try:
        urllib.request.urlopen(TAGS_URL, timeout=timeout).close()
        return True
    except Exception:
        return False
