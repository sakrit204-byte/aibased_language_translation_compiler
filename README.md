# Hybrid AI-assisted Python-to-C compiler

A source-to-source compiler for a defined subset of Python, targeting C.
The deterministic core (lexer, parser, semantic analysis, codegen) handles
every construct in the language on its own — no AI in the loop. A small
local model (via Ollama, $0 cost) is invoked *only* for an explicit,
allow-listed set of stdlib-style functions that have no static rule, and
its output is never accepted unless it compiles.

Run `python3 -m src.driver tests/programs/factorial.py -o out.c --no-ai`
right now, no setup beyond Python + gcc, and it works — that's the point:
the compiler is a real compiler with or without the AI piece.

## Quick start

```bash
# Compile without AI (works out of the box, no dependencies)
python3 -m src.driver tests/programs/factorial.py -o out.c --no-ai
gcc out.c -I src -lm -o out && ./out

# Run the full test suite (core programs, AI disabled)
python3 -m pytest tests/ -v

# With AI-assist enabled (needs Ollama running locally)
ollama pull qwen2.5-coder:7b
ollama serve &
python3 -m src.driver tests/programs/ai_fallback_demo.py -o out.c
RUN_AI_TESTS=1 python3 -m pytest tests/ -v
```

## Architecture

```
source.py --> Lexer --> Parser --> Semantic analysis --> Codegen --> out.c
              (tokens)   (AST)     (scopes, types)       (rule templates)
                                                                |
                                                    unmapped, allow-listed
                                                    calls only -> AI-assist
                                                    (gated, validated by gcc)
```

See `grammar/subset.ebnf` for the exact language accepted.

## AI-usage policy (enforced in code, not just documented)

AI is invoked **only** when all of these hold, checked in `src/ai_assist/gate.py`
and `src/semantic/type_checker.py:AI_FALLBACK_FUNCS`:

1. The call name is on an explicit allow-list (`abs`, `pow`, `min`, `max`, `round`)
   — a deliberate project decision, never inferred.
2. No static codegen rule matched (checked first, always).
3. The AI's candidate C expression compiles (`gcc -fsyntax-only`) before it's
   accepted — up to 2 attempts, then a flagged stub, never silent failure.

AI is **never** invoked for: control flow, type inference/checking, scope
resolution, or any construct with an existing static rule — see
`src/ai_assist/gate.py` module docstring for the full reasoning.

`--no-ai` disables the module entirely and makes any gap a hard compile
error instead of a fallback — this is how you demonstrate the deterministic
core stands on its own.

## Project layout

- `grammar/subset.ebnf` — the language spec
- `src/lexer/` — hand-written tokenizer with INDENT/DEDENT tracking
- `src/parser/` — recursive-descent parser, AST node definitions
- `src/semantic/` — symbol table, deterministic type inference/checking
- `src/codegen/` — AST -> C rule templates + emitter
- `src/ai_assist/` — gate (policy enforcement), Ollama client, gcc-based validator
- `src/runtime/pyrt.h` — C runtime header generated code links against
- `tests/programs/` — sample subset programs (Python-valid, so real `python3`
  doubles as the correctness oracle)
- `tests/test_e2e_harness.py` — compiles, runs the C binary, diffs vs. real Python
- `reports/ai_invocation_log.md` — every AI call this compiler makes, logged
  here for the ablation writeup

## What's intentionally out of scope

Classes, list/dict literals and comprehensions, decorators, lambdas,
multiple assignment, exceptions, imports, string formatting. Excluded by
grammar, not silently mishandled — see the bottom of `subset.ebnf`.

## Evaluation for your report

- Run the core suite (`pytest tests/`) with `--no-ai` implicitly enforced
  (`test_core_programs_match_python` asserts `ai_calls_used == 0`) — this is
  your evidence the deterministic core carries ordinary programs alone.
- Run with `RUN_AI_TESTS=1` to exercise the AI-fallback path and check
  `reports/ai_invocation_log.md` for the full transcript of every call, what
  was proposed, and whether it validated.
- Track the AI-invocation rate per program (see `stats["ai_calls_used"]`
  returned by `compile_source`); the policy in the README's "AI-usage
  policy" section is the argument for why that rate should be low.
