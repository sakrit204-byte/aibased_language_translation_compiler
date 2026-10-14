"""
Language-model client.

Primary provider is OpenRouter running Google's Gemma 4 31B. Ollama is kept as
an offline fallback so the project still works with no network and no key.

Three things here exist because of how the free tier actually behaves, not
because they sounded prudent:

  * **Free-then-paid fallback.** `google/gemma-4-31b-it:free` is served from a
    shared upstream pool and returns HTTP 429 a large fraction of the time. The
    client retries the free model, then falls back to the paid endpoint for the
    *same* model, which costs about $0.0001 per translation. Without this a demo
    fails roughly one run in two.
  * **Temperature 0 and a fixed seed.** A compiler that emits different output
    for the same input on two runs is not a compiler.
  * **The key is never hard-coded.** It is read from $OPENROUTER_API_KEY or from
    secrets/openrouter_key.txt, which .gitignore excludes.
"""

import json
import os
import time
import urllib.error
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(PROJECT_ROOT, "secrets", "openrouter_key.txt")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OLLAMA_HOST = "http://localhost:11434"

# Claude, served through the same OpenRouter key as everything else. Haiku 5.5
# costs about $0.00005 per translation, which is cheaper than the engineering
# time spent working around the free tier's shared rate-limit pool.
CLAUDE_HAIKU = "anthropic/claude-haiku-5.5"
CLAUDE_SONNET = "anthropic/claude-sonnet-5.5"

# Same model, two endpoints. The free one is tried first and is rate-limited
# upstream; the paid one is the fallback and is charged per token.
FREE_MODEL = "google/gemma-4-31b-it:free"
PAID_MODEL = "google/gemma-4-31b-it"

# Claude Haiku by default: it answers reliably, where the Gemma free endpoint
# returns HTTP 429 roughly half the time. Switching to Gemma is one selection
# in the GUI dropdown, or one --model flag.
DEFAULT_MODEL = CLAUDE_HAIKU

FREE_ATTEMPTS = 3          # how many times to retry the free pool before paying
RETRY_PAUSE = 1.5          # seconds between free-tier retries (429s return instantly)
SEED = 0

# Offered in the GUI dropdown.
AVAILABLE_MODELS = [
    CLAUDE_HAIKU,
    CLAUDE_SONNET,
    FREE_MODEL,
    PAID_MODEL,
    "google/gemma-4-26b-a4b-it:free",
    "qwen2.5-coder:7b (ollama)",
]


class AIClientError(Exception):
    pass


# ----------------------------------------------------------------- the key

def api_key():
    """Return the OpenRouter key, or None if none is configured."""
    env = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env:
        return env
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
        return key or None
    except OSError:
        return None


def key_status():
    """Human-readable description of the credential situation, with no secret
    in it — safe to show in the GUI or write to a log."""
    key = api_key()
    if not key:
        return "no OpenRouter key (set OPENROUTER_API_KEY or secrets/openrouter_key.txt)"
    return f"OpenRouter key present ({key[:12]}..., {len(key)} chars)"


def is_ollama_model(model):
    return "ollama" in (model or "").lower()


# --------------------------------------------------------------- transport

def _openrouter_call(model, prompt, timeout, max_tokens):
    key = api_key()
    if not key:
        raise AIClientError(
            "No OpenRouter API key. Set OPENROUTER_API_KEY, or put the key in "
            "secrets/openrouter_key.txt (already excluded by .gitignore)."
        )
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "top_p": 1,
        "seed": SEED,
        "max_tokens": max_tokens,
    }).encode()
    request = urllib.request.Request(
        OPENROUTER_URL, data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "translation-compiler",
        })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise _http_error(e.code, model, body) from e
    except (urllib.error.URLError, OSError) as e:
        raise AIClientError(f"Could not reach OpenRouter ({e}).") from e
    except json.JSONDecodeError as e:
        raise AIClientError(f"OpenRouter returned malformed JSON: {e}") from e

    if "error" in data:
        raise AIClientError(f"OpenRouter error: {data['error']}")
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise AIClientError(f"Unexpected OpenRouter response shape: {str(data)[:300]}")


class RateLimited(AIClientError):
    """The upstream free pool is busy. Retrying, or paying, may work."""


def _http_error(code, model, body):
    if code == 429:
        return RateLimited(f"{model} is rate-limited upstream (HTTP 429).")
    if code == 401:
        return AIClientError("OpenRouter rejected the API key (HTTP 401).")
    if code == 402:
        return AIClientError("OpenRouter reports insufficient credit (HTTP 402).")
    return AIClientError(f"OpenRouter HTTP {code} for {model}: {body}")


def _ollama_call(model, prompt, timeout, max_tokens):
    name = (model or "").replace("(ollama)", "").strip() or "qwen2.5-coder:7b"
    payload = json.dumps({
        "model": name, "prompt": prompt, "stream": False,
        "options": {"temperature": 0, "top_p": 1, "seed": SEED,
                    "num_predict": max_tokens},
    }).encode()
    request = urllib.request.Request(
        OLLAMA_HOST + "/api/generate", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except (urllib.error.URLError, OSError) as e:
        raise AIClientError(
            f"Could not reach Ollama at {OLLAMA_HOST} ({e}). Is `ollama serve` running?"
        ) from e
    if "error" in data:
        raise AIClientError(f"Ollama error: {data['error']}")
    return data.get("response", "")


# ------------------------------------------------------------------- query

def query(prompt, model=None, timeout=60, trace=None, max_tokens=256):
    """Send `prompt` to the model and return its raw text reply.

    `trace`, if given, is a list that receives one dict per provider attempt —
    the audit trail the gate writes into its invocation log.
    """
    model = model or DEFAULT_MODEL

    if is_ollama_model(model):
        started = time.time()
        text = _ollama_call(model, prompt, timeout, max_tokens)
        if trace is not None:
            trace.append({"endpoint": model, "ok": True,
                          "seconds": round(time.time() - started, 2)})
        return text

    plan = [(model, FREE_ATTEMPTS)] if model.endswith(":free") else [(model, 1)]
    if model == FREE_MODEL:
        plan.append((PAID_MODEL, 1))        # same model, charged endpoint

    last = None
    for endpoint, attempts in plan:
        for attempt in range(1, attempts + 1):
            started = time.time()
            try:
                text = _openrouter_call(endpoint, prompt, timeout, max_tokens)
                if trace is not None:
                    trace.append({"endpoint": endpoint, "attempt": attempt, "ok": True,
                                  "seconds": round(time.time() - started, 2)})
                return text
            except RateLimited as e:
                last = e
                if trace is not None:
                    trace.append({"endpoint": endpoint, "attempt": attempt,
                                  "ok": False, "error": "rate limited"})
                if attempt < attempts:
                    time.sleep(RETRY_PAUSE)
            except AIClientError as e:
                last = e
                if trace is not None:
                    trace.append({"endpoint": endpoint, "attempt": attempt,
                                  "ok": False, "error": str(e)[:120]})
                break                        # auth/credit errors will not self-heal
    raise last or AIClientError("No provider could be reached.")


# ------------------------------------------------------------------ status

def list_models():
    """Names for the GUI dropdown. Ollama models are appended when the daemon
    is reachable, so the list reflects what can actually be used."""
    models = list(AVAILABLE_MODELS)
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=2) as r:
            data = json.loads(r.read().decode())
        for m in data.get("models", []):
            name = m.get("name")
            if name and f"{name} (ollama)" not in models:
                models.append(f"{name} (ollama)")
    except Exception:
        pass
    return models


def provider_available(timeout=5):
    """True when at least one provider looks usable. Used by the GUI to warn
    before a translation that needs AI is attempted."""
    if api_key():
        return True
    try:
        urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=timeout).close()
        return True
    except Exception:
        return False
