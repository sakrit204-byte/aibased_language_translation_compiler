"""
The AI-usage policy, enforced in code rather than left as a comment.

**What can reach this module.** Exactly one thing: a call to a name that is
neither defined in the program nor one of the builtins the compiler translates
statically (`len`, `abs`, `min`, `max`, `pow`, `round`, `int`, `float`, `bool`).
Everything else is settled deterministically upstream and never gets here:

  * control flow, assignment, operators, function calls -> codegen/rules.py
  * type inference and checking                         -> semantic/type_checker.py
  * scope resolution                                    -> semantic/symbol_table.py

That boundary is the fix for the design's original flaw. `abs`, `pow`, `min`,
`max` and `round` used to be routed here despite each having a one-line C rule,
which made the AI look load-bearing when it was not. The compiler now has zero
AI invocations on every program in tests/programs/ — AI is the extension
mechanism for names the compiler does not know, not a crutch for ones it should.

**What this module guarantees.**

  * A translation is a *template* over placeholders (`a0`, `a1`, ...), so one
    verified answer covers every call site with that name and arity.
  * Nothing is accepted until it passes both the structural safety check and a
    real C compiler (see validator.py). With no C compiler present, everything
    is rejected — the gate fails closed.
  * Verified templates are written to a cache. A second compile of the same
    program consults the cache and makes no AI call at all, so builds are
    reproducible and the model is not in the loop twice for the same question.
  * Every attempt, accepted or not, is appended to the invocation log.
"""

import json
import os
import time

from . import client
from .validator import validate

MAX_ATTEMPTS = 3
CACHE_VERSION = 1


class AIAssistError(Exception):
    """Raised when no valid translation could be obtained."""


# --------------------------------------------------------------------- cache

class TemplateCache:
    """Verified translations, keyed by `name/arity` and persisted as JSON.

    A cache hit is not a shortcut around validation: entries only ever get
    written after passing it, and the validated form is exactly what is reused.
    """

    def __init__(self, path=None):
        self.path = path
        self.entries = {}
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") == CACHE_VERSION:
                    self.entries = data.get("templates", {})
            except (OSError, json.JSONDecodeError, AttributeError):
                self.entries = {}

    @staticmethod
    def key(name, arity):
        return f"{name}/{arity}"

    def get(self, name, arity):
        entry = self.entries.get(self.key(name, arity))
        return entry["template"] if entry else None

    def put(self, name, arity, template, model):
        self.entries[self.key(name, arity)] = {
            "template": template,
            "model": model,
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._save()

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": CACHE_VERSION, "templates": self.entries}, f, indent=2
                )
        except OSError:
            pass          # a cache we cannot write is a slowdown, not an error


# -------------------------------------------------------------------- prompt

def _prompt(name, arity, previous_failure=None):
    params = ", ".join(f"a{i}" for i in range(arity))
    prompt = (
        f"You are translating one Python builtin call into C for a compiler.\n\n"
        f"Python call:  {name}({params})\n\n"
        f"Reply with a single C expression that computes the same value.\n"
        f"Rules:\n"
        f"  - Use exactly the parameter names {params or '(none)'}; they are C doubles.\n"
        f"  - Use only functions from <math.h>.\n"
        f"  - No statements, no semicolons, no assignments, no function definitions.\n"
        f"  - Output the expression and nothing else: no prose, no code fences.\n\n"
        f"Example — for Python `sqrt(a0)` the correct reply is:\n"
        f"sqrt(a0)\n"
    )
    if previous_failure:
        prompt += (
            f"\nYour previous answer was {previous_failure['candidate']!r} and it was "
            f"{previous_failure['reason']}\nFix it and reply with only the corrected "
            f"expression.\n"
        )
    return prompt


def _extract_expression(raw: str) -> str:
    """Pull a bare C expression out of a model response that may be wrapped in
    prose or code fences."""
    text = (raw or "").strip()

    if "```" in text:
        blocks = text.split("```")
        if len(blocks) >= 2:
            block = blocks[1]
            if "\n" in block:
                first, rest = block.split("\n", 1)
                # drop a language tag like ```c
                block = rest if first.strip().lower() in ("c", "cpp", "c++") else block
            text = block.strip()

    # Keep the first non-empty, non-prose line.
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("//") and not line.startswith("/*"):
            text = line
            break

    return text.strip().rstrip(";").strip()


# ---------------------------------------------------------------- the gate

class Gate:
    def __init__(self, model=None, log_path=None, cache_path=None):
        self.model = model or client.DEFAULT_MODEL
        self.log_path = log_path
        self.cache = TemplateCache(cache_path)
        self.queries_made = 0
        self.cache_hits = 0
        self.records = []

    def resolve(self, call_node) -> str:
        """Return a validated C template for `call_node`, or raise AIAssistError.

        The compiler never emits an unvalidated translation: if no candidate
        passes, this raises and compilation fails loudly.
        """
        name = call_node.name
        arity = len(call_node.args)

        cached = self.cache.get(name, arity)
        if cached is not None:
            self.cache_hits += 1
            self._record(name, arity, [], accepted=cached, source="cache")
            return cached

        attempts = []
        previous = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.time()
            try:
                raw = client.query(_prompt(name, arity, previous), model=self.model)
                self.queries_made += 1
            except client.AIClientError as e:
                attempts.append({"attempt": attempt, "error": str(e)})
                break

            candidate = _extract_expression(raw)
            ok, reason = validate(candidate, arity)
            attempts.append({
                "attempt": attempt,
                "candidate": candidate,
                "valid": ok,
                "reason": reason,
                "seconds": round(time.time() - started, 2),
            })
            if ok:
                self.cache.put(name, arity, candidate, self.model)
                self._record(name, arity, attempts, accepted=candidate, source="model")
                return candidate
            previous = {"candidate": candidate, "reason": reason}

        self._record(name, arity, attempts, accepted=None, source="model")
        detail = attempts[-1].get("reason") or attempts[-1].get("error", "no candidate") \
            if attempts else "no candidate"
        raise AIAssistError(
            f"line {call_node.line}: AI-assist could not produce a valid C translation for "
            f"'{name}' after {len(attempts)} attempt(s). Last failure: {detail}. "
            f"Define '{name}' in your program, or add a static rule for it."
        )

    # ---- logging ----

    def _record(self, name, arity, attempts, accepted, source):
        record = {
            "call": f"{name}/{arity}",
            "model": self.model,
            "source": source,
            "attempts": attempts,
            "accepted": accepted,
        }
        self.records.append(record)
        if not self.log_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"### {name}({', '.join('a%d' % i for i in range(arity))})\n")
                f.write(f"- model: `{self.model}`  source: {source}  "
                        f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                for a in attempts:
                    f.write(f"- {a}\n")
                f.write(f"- **accepted**: {accepted!r}\n\n")
        except OSError:
            pass

    def stats(self):
        return {
            "model": self.model,
            "queries_made": self.queries_made,
            "cache_hits": self.cache_hits,
            "resolved": sum(1 for r in self.records if r["accepted"]),
        }
