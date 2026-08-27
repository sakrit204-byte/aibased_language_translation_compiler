"""
The AI-usage policy, enforced in code rather than left as a comment.

A call reaches this module ONLY if the type checker already flagged it
(node.needs_ai = True), which itself only happens for names on the explicit
AI_FALLBACK_FUNCS allow-list in semantic/type_checker.py. That means, by
construction:
  - core language constructs (if/while/for/assignment/functions) never
    reach this file — they have static rules and are handled in codegen/.
  - type inference decisions never reach this file — semantic/type_checker.py
    is fully deterministic.
  - arbitrary unknown function calls never reach this file — they are a
    hard semantic error upstream, not silently routed to AI.

This file's only remaining job: for the narrow, allow-listed cases that DO
arrive, get a translation from the local model and refuse to accept it
unless it compiles.
"""

import time

from . import client
from .validator import is_valid_c_expression

MAX_ATTEMPTS = 2


def _prompt(call_node, args_str):
    return (
        f"Translate this Python builtin call to a single C expression "
        f"(no statements, no semicolons, just the expression). "
        f"Assume <math.h> and <stdlib.h> are available.\n"
        f"Python: {call_node.name}({args_str})\n"
        f"Respond with ONLY the C expression, nothing else."
    )


def _extract_expr(raw_response: str) -> str:
    text = raw_response.strip()
    # strip markdown code fences if the model added them anyway
    text = text.strip("`").strip()
    if text.startswith("c\n"):
        text = text[2:]
    return text.strip().rstrip(";")


def resolve(call_node, args_str, log_path=None):
    """Returns a validated C expression string, or a flagged stub if the
    retry budget is exhausted. Never returns unvalidated AI output."""
    attempts = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = _prompt(call_node, args_str)
        t0 = time.time()
        try:
            raw = client.query(prompt)
        except client.AIClientError as e:
            attempts.append({"attempt": attempt, "error": str(e)})
            break
        expr = _extract_expr(raw)
        ok, stderr = is_valid_c_expression(expr)
        elapsed = time.time() - t0
        attempts.append({
            "attempt": attempt, "line": call_node.line, "call": f"{call_node.name}({args_str})",
            "candidate": expr, "valid": ok, "gcc_stderr": stderr.strip() if not ok else "",
            "seconds": round(elapsed, 2),
        })
        if ok:
            _log(log_path, call_node, attempts, accepted=expr)
            return expr

    # Retry budget exhausted (or Ollama unreachable) — never ship unvalidated code.
    _log(log_path, call_node, attempts, accepted=None)
    stub = (
        f'({{ /* AI-ASSIST FAILED for {call_node.name}({args_str}) at line '
        f'{call_node.line} — manual review needed */ 0; }})'
    )
    return stub


def _log(log_path, call_node, attempts, accepted):
    if not log_path:
        return
    with open(log_path, "a") as f:
        f.write(f"### {call_node.name} @ line {call_node.line}\n")
        for a in attempts:
            f.write(f"- {a}\n")
        f.write(f"- **accepted**: {accepted!r}\n\n")
