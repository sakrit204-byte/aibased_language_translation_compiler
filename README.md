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
`max`, `pow`, `round`, `int`, `float`, `bool`, `put`). By construction the model is
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

### Providers

The model is reached through OpenRouter, so one API key covers every option.
Put it in `OPENROUTER_API_KEY` or in `secrets/openrouter_key.txt`, which
`.gitignore` excludes.

| Model | Cost per translation | Notes |
| --- | --- | --- |
| `anthropic/claude-haiku-5.5` | ~$0.00005 | **default** — reliable, answers in ~2s |
| `anthropic/claude-sonnet-5.5` | ~$0.0005 | stronger, for the Simplify pass |
| `google/gemma-4-31b-it:free` | free | shared upstream pool, HTTP 429 about half the time |
| `google/gemma-4-31b-it` | ~$0.00003 | the same model, paid endpoint |
| `… (ollama)` | free | fully offline, needs `ollama serve` |

The free Gemma endpoint is retried three times and then falls back to the paid
endpoint for the same model, because without that a demo fails about one run in
two. Pick any of them with `--model` or from the GUI dropdown.

### Trying the AI path

```bash
# Ten programs that cannot be translated without the gate
python -m src.driver tests/programs/ai/01_pythagoras.py --run --ai

# The same ten, compiled and diffed against CPython
RUN_AI_TESTS=1 python -m pytest tests/ -q
```

`tests/programs/ai/` holds ten programs that each call at least one function
with no static rule — `sqrt`, `sin`, `atan2`, `erf`, `radians` and so on. Every
one is a hard compile error without `--ai`. All ten produce output
byte-identical to CPython with it.

The clearest single case is `degrees`. There is no C `degrees()` to rename, so
the model has to *derive* `a0 * (180.0 / M_PI)` — and the compiler then proves
that derivation compiles before using it.

## Simplify: AI rewriting, accepted only when proven equivalent

`--simplify` on the CLI, or the **✦ Simplify** button in the IDE, asks the model
to tidy the generated C. Its answer is accepted only if it survives three
checks, in this order:

1. A **safety check** over code with comments and string literals stripped —
   no process, file, network or environment access, and no header the original
   did not already use. This runs *first*, because verification executes the
   result.
2. It must **compile**.
3. Both binaries are run and their **stdout and exit status compared byte for
   byte**.

Programs in this subset take no input — no `input()`, no files, no clock, no
randomness, and the grammar cannot express any of them. Standard output and
exit status are therefore the *entire* observable behaviour, so step 3 is not a
sample of it; it is all of it. For the program as written, equivalence is
established rather than estimated.

Both outcomes happen in practice:

```
10_projectile.py   ACCEPTED  213 -> 99 lines (54% smaller), 7 output lines verified
arithmetic.py      REJECTED  line 20 became '-7.5  = 0.5', expected '-7.5 % 2 = 0.5'
```

The rejection is the interesting one: the model dropped a `%` from a `printf`
format string. The result compiled cleanly and looked fine. Only running it
caught the corruption.

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
| `src/ai_assist/` | gate (policy), multi-provider client, safety + compiler validator, verified simplifier |
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

Classes, growable lists, dicts/sets/tuples, comprehensions, slicing, decorators, lambdas,
generators, exceptions, closures, nested functions, chained assignment and
comparison, default and keyword arguments, string concatenation and
formatting. These are rejected by the grammar with a message that says so, not
silently mishandled.

Imports are accepted, but only for the two modules in
`src/semantic/modules.py` — see below. Anything else is refused by name with
the reason.

String concatenation and `str()` are excluded specifically because they need
heap allocation, and the generated C has no allocator or ownership model.
Half-implementing one would be worse than saying no.

Two behavioural limits of the generated code, both documented rather than
hidden: Python integers are arbitrary precision while `py_int` is 64-bit and
wraps, and recursion depth is the C stack's rather than
`sys.getrecursionlimit()`. Everything else matches CPython exactly, and the
tests prove it by diffing against CPython.

## Arrays

A fixed-size array is the one compound type in the subset:

```python
def bump(values: list[int]) -> None:
    i: int = 0
    while i < len(values):
        values[i] = values[i] + 1
        i = i + 1

def main() -> int:
    counts: list[int] = [0] * 8
    primes: list[int] = [2, 3, 5, 7]
    bump(counts)
    print(counts[0], primes[-1], len(primes))
    return 0
```

`counts: list[int] = [0] * 8` becomes `py_int counts[8]`. It lives on the
stack, it is freed by returning, and it cannot leak — which is why a *fixed*
array fits a compiler with no allocator where a growable list does not. The
size has to be a literal because it becomes part of the C type. There is no
append, no slicing and no resizing.

Three places where C's defaults and Python's semantics disagree, and Python
wins, because the whole claim of this compiler is that the two agree:

- **A negative index counts from the end.** `a[-1]` is the last element, not
  a read off the front of the array.
- **An out-of-range index stops the program.** C would read whatever is at
  that address and carry on. The generated code checks, prints the same
  `IndexError` CPython does, and exits 1 — the same status.
- **An array argument is shared, not copied.** Python passes the same list;
  C passes the same storage. A function that writes through its parameter
  changes the caller's array in both.

C arrays decay to pointers when passed, which loses the size, so every array
parameter compiles to *two* C parameters — the pointer and its length — and
every call site passes both. That is what keeps `len()` and the bounds checks
working inside a function:

```c
py_int total(py_int *values, py_int values__len)
```

Elements are never widened. `a: list[float] = [0] * 3` is an error rather
than a silent conversion, because Python would keep the ints you wrote and C
would store doubles, and the two would then print different numbers.

**`demos/rubik.py` is what this was added for.** A Rubik's cube is 54
stickers and a permutation table, and neither was expressible before: the
table used to be a dict of lists of tuples, and is now one array of 72 ints.
The program's own output asserts the properties a real cube has — four turns
of a face is the identity, a move undoes its inverse, `(R U R' U')` has order
six — and it is byte-identical to CPython:

```
$ python -m src.driver demos/rubik.py --run
call sites: 38   AI-resolved: 0   model queries: 0   (AI-assist disabled)
solved
   WWW
   WWW
   WWW
OOOGGGRRRBBB
OOOGGGRRRBBB
OOOGGGRRRBBB
   YYY
   YYY
   YYY

every face turned four times, still solved:
True
every move followed by its inverse, still solved:
True
(R U R' U') six times, back to solved:
True
```

then a 30-move scramble, and the same scramble played backwards, which comes
back to solved.

## Imports

An import here binds names. It does not execute a module, because there is no
module object to execute and no file to go and read: the name is looked up in
a fixed table, and every member it brings into scope has a static type rule
and a static C translation — the same contract as a builtin.

```python
import math
from math import sqrt, floor
from math import degrees as to_degrees
import math as m
from math import *
```

Two modules exist.

**`math`** maps onto `<math.h>`: 33 names, including the places where Python
and C disagree. `math.floor` returns an `int` as Python's does, not the
`double` C's `floor` hands back. Importing it also makes the maths
*deterministic* — a bare `sqrt(x)` has no rule and costs a model round trip
that comes back proven only to compile, whereas `math.sqrt` is known-correct
here and never reaches the gate:

```
$ python -m src.driver tests/programs/imports_math.py --run
call sites: 31   AI-resolved: 0   model queries: 0   (AI-assist disabled)
```

**`gfx`** is the window and OpenGL shim in `src/runtime/gfx.c`, and importing
it is what links that file in. It is the one way a program in this subset can
open a window, and it works only because every function in it takes and
returns numbers — the shim owns the window handle, the GL context and the
keyboard, none of which the subset can express:

```python
import gfx
import math

def main() -> int:
    if gfx.open(900, 650) == 0:
        return 1
    while gfx.running() != 0:
        gfx.poll()
        gfx.clear(0.07, 0.08, 0.11)
        gfx.perspective(60.0, 0.1, 100.0)
        gfx.camera(math.cos(gfx.time()) * 6.0, 3.0, math.sin(gfx.time()) * 6.0,
                   0.0, 0.0, 0.0)
        gfx.color(0.9, 0.3, 0.25)
        gfx.tri(-1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.5, 0.0)
        gfx.present()
    gfx.close()
    return 0
```

`demos/cube3d.py` is a fuller version of that — a lit, rotating cube in a
real window, with arrow-key camera control.

**Everything else is refused by name, with the reason.** The reason is never
the import syntax; it is that the module's API hands back something the subset
has no way to hold:

```
$ python -m src.driver rubik.py
line 1: 'pygame' cannot be imported — its API is built from objects and
arrays, and this subset has neither, so there is no way to hold what it would
hand back. For a window, use `import gfx` instead.
  Importable here: gfx, math.
```

`random` is refused for a different and more interesting reason: it *could* be
translated, but C's `rand()` is not Python's Mersenne Twister, so the compiled
program would print different numbers from the Python one — and agreeing with
CPython exactly is how this compiler is tested.

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
