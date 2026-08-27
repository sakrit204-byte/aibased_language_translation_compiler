# Hybrid Python → C compiler

A source-to-source compiler for a defined subset of Python, targeting C, with a
desktop IDE. The deterministic core — lexer, parser, semantic analysis, code
generation — translates every construct in the language on its own. A local
model is consulted **only** when a program calls a function the compiler has
never heard of, and its answer is thrown away unless it passes a safety check
*and* compiles.

Everything in `tests/programs/` compiles and runs with the AI switched off, and
its output is byte-identical to CPython's.

```bash
# Translate and run — no AI, no setup beyond Python and a C compiler
python -m src.driver tests/programs/arithmetic.py --run

# The whole test suite
python -m pytest tests/ -q

# The IDE
python gui.py
```

## Why the translation is the hard part

The naive mapping — Python `/` to C `/`, `%` to `%`, `print` to `printf` — is
wrong in ways that compile cleanly and produce wrong numbers. This compiler
emits a small runtime (`src/codegen/runtime.py`) so the generated C means what
the Python meant:

| Python | naive C | what it should be |
| --- | --- | --- |
| `7 / 2` → `3.5` | `7 / 2` → `3` | `/` is always true division |
| `-7 // 2` → `-4` | `-7 / 2` → `-3` | `//` floors; C truncates |
| `-7 % 2` → `1` | `-7 % 2` → `-1` | `%` takes the divisor's sign |
| `2 ** -1` → `0.5` | — | a negative exponent yields a float |
| `1 / 0` → raises | undefined behaviour | `ZeroDivisionError`, exit 1 |
| `print(3.5)` → `3.5` | `printf("%f")` → `3.500000` | `repr()` picks the shortest round-tripping form |
| `print(True)` → `True` | `printf("%d")` → `1` | bools print as words |
| `a == b` on `str` | pointer comparison | `strcmp` |
| `round(2.5)` → `2` | `round(2.5)` → `3` | Python rounds halves to even |

Two more, structural rather than arithmetic:

* **Scope.** Python scopes a name to the whole function; C scopes it to the
  enclosing block. A variable first assigned inside an `if` would be declared
  inside that block and vanish afterwards, so codegen hoists every local to the
  top of the C function.
* **Loop bounds.** `for i in range(f(n))` evaluates `f(n)` once in Python.
  Emitting `for (i = 0; i < f(n); i++)` calls it every iteration, so the bounds
  are computed into temporaries first.

`tests/test_runtime_semantics.py` checks each of these differentially against
CPython, including a fuzz run of `repr()` over 800 random doubles.

## AI-usage policy, enforced in code

The rule is: **AI is the extension mechanism for names the compiler does not
know, never a substitute for a rule it should have.**

A call reaches `src/ai_assist/gate.py` only if the semantic phase tagged it
`kind == "ai"`, which happens only when the callee is neither defined in the
program nor one of the builtins with a static C rule (`len`, `abs`, `min`,
`max`, `pow`, `round`, `int`, `float`, `bool`). By construction the model is
never asked about:

* control flow, assignment, operators, or any construct in the grammar
  — those are `src/codegen/rules.py`;
* type inference or checking — that is `src/semantic/type_checker.py`, and a
  wrong type there becomes a silently wrong value in C that the C compiler
  cannot catch;
* scope resolution — `src/semantic/symbol_table.py`.

When the gate *is* reached:

1. It asks for a **template** over placeholders (`a0`, `a1`, …), so one verified
   answer covers every call site with that name and arity.
2. The answer must pass a **structural safety check**: only the call's own
   arguments, numeric literals, operators, and an allow-list of pure `<math.h>`
   functions. This is not decoration — `gcc -fsyntax-only` accepts
   `system("rm -rf /")` perfectly happily, so "it compiles" is not on its own a
   safe criterion for pasting a model's output into a program.
3. It must then **compile**. If no C compiler is installed, validation *fails*;
   it does not pass by default. The gate fails closed.
4. Failures are fed back into the retry, up to three attempts. If none passes,
   compilation **fails loudly** — there is no unvalidated fallback stub.
5. Verified templates are cached in `reports/ai_cache.json`, so a rebuild makes
   no model call and the build is reproducible. Sampling is pinned to
   temperature 0; a compiler that emits different output on two identical runs
   is not a compiler.

Every attempt, accepted or rejected, is appended to
`reports/ai_invocation_log.md`.

`tests/test_ai_policy.py` asserts all of this with the model stubbed out, so the
policy is tested even on a machine with no model installed.

### Trying the AI path

```bash
ollama pull qwen2.5-coder:7b
ollama serve &
python -m src.driver tests/programs/ai_math_demo.py -o out.c --ai
RUN_AI_TESTS=1 python -m pytest tests/ -q
```

`ai_math_demo.py` calls `hypot`, `degrees` and `log` — real functions that live
in Python's `math` module, which this subset has no import syntax for. Without
`--ai` you get a hard error naming the missing function; with it, each is sent
to the gate once.

## Architecture

```
source.py
   │
   ├─ Lexer          tokens, INDENT/DEDENT, CRLF and tab handling, escape decoding
   ├─ Parser         recursive descent, CPython operator precedence
   ├─ Semantic       fixpoint type inference, definite assignment, return coverage
   ├─ Codegen        rule templates + only the runtime helpers actually used
   └─ out.c          self-contained: `cc out.c -lm -o out`
                        │
                        └─ unknown call names only ──► AI gate (safety-checked,
                                                       compiler-validated, cached)
```

Three analyses run per function in the semantic phase:

* **Type inference** — a small monotone dataflow pass. Types only widen along
  `bool < int < float`, so iterating to a fixpoint terminates. This is what lets
  `total = 0` followed by `total = total / n` infer `total: float` instead of
  rejecting the program.
* **Definite assignment** — using a variable before an assignment that dominates
  the use is a compile error, not an `UnboundLocalError` at runtime or a garbage
  read in C.
* **Return coverage** — a function declared `-> int` must return on every path.
  C would fall off the end and return whatever was in the return register.

## Layout

| Path | What it is |
| --- | --- |
| `grammar/subset.ebnf` | the accepted language, and what is deliberately excluded |
| `src/lexer/` | hand-written tokenizer |
| `src/parser/` | recursive-descent parser and AST nodes |
| `src/semantic/` | symbol table, type checker, builtin type rules |
| `src/codegen/` | AST → C rules, the emitter, and the C runtime prelude |
| `src/ai_assist/` | gate (policy), Ollama client, safety + compiler validator |
| `src/toolchain.py` | finds whichever C compiler is installed |
| `src/driver.py` | CLI and pipeline orchestration |
| `gui.py` | the IDE |
| `tests/` | unit, differential and end-to-end tests |

## The IDE

`python gui.py` — split view with Python on the left and generated C on the
right, a clickable pipeline showing each phase's result, an integrated terminal
that runs either side, translation history, and light/dark themes. Errors
highlight the offending source line.

Standard-library tkinter only; no third-party packages.

* `Ctrl+Enter` translate · `F5` run the C · `Ctrl+S` save · `Ctrl+O` open ·
  `Ctrl+L` clear the console

## What's out of scope

Classes, lists/dicts/sets/tuples, comprehensions, slicing, decorators, lambdas,
generators, exceptions, imports, closures, nested functions, chained assignment
and comparison, default and keyword arguments, string concatenation and
formatting. These are rejected by the grammar with a message that says so, not
silently mishandled.

String concatenation and `str()` are excluded specifically because they need
heap allocation, and the generated C has no allocator or ownership model.
Half-implementing one would be worse than saying no.

Two behavioural limits of the generated code, both documented rather than
hidden: Python integers are arbitrary precision while `py_int` is 64-bit and
wraps, and recursion depth is the C stack's rather than
`sys.getrecursionlimit()`. Everything else matches CPython exactly, and the
tests prove it by diffing against CPython.

## Evaluation

* `pytest tests/` — the core suite. `test_core_programs_match_python` builds
  each sample and diffs its stdout against CPython, and asserts
  `ai_calls_used == 0`. That is the evidence the deterministic core carries
  ordinary programs alone.
* `test_no_sample_program_uses_ai_even_when_it_is_enabled` runs the same
  programs with AI *switched on* and asserts the model is still never queried.
  The AI-invocation rate on real programs is zero, not merely low.
* `RUN_AI_TESTS=1 pytest tests/` exercises the gate for real and leaves a full
  transcript in `reports/ai_invocation_log.md`.
* `stats` from `compile_source` reports `total_call_sites`, `ai_calls_used`,
  `ai_queries` and `ai_cache_hits` for any program you want to measure.

## Requirements

Python 3.9+ and a C compiler (`gcc`, `clang`, `cc`, `tcc` or MSVC `cl`). The
compiler is found on `PATH`, via `$CC`, or in the usual Windows install
locations; if none is present you get an actionable message rather than a
crash, and everything except building and running still works.
