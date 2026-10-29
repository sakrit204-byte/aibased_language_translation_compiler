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

There are three places the model can be consulted, and each one is bounded by
something that checks its answer rather than by trust:

| Where | What it is asked | What decides |
| --- | --- | --- |
| the gate, `gate.py` | one C expression for one unknown call | a structural safety check, then a real C compiler |
| Simplify, `simplify.py` | tidier C for a successful translation | both binaries run, stdout and exit compared |
| Repair, `repair.py` | a rewrite of a program that will not compile | the whole compiler, then four runs compared byte for byte |

The gate is the only one inside the pipeline; the other two sit outside it and
can only ever propose. None of them can produce output that was not checked
against something the model did not write.

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

## Repair: when the byte-for-byte translation cannot be made

Simplify works on a translation that succeeded. **Repair** is for the other
case — the program does not compile at all, because it uses an f-string, a
comprehension, `enumerate`, `.items()`, a tuple unpack, or anything else the
subset has no rule for.

`--repair` on the CLI, or the **✦ Repair** button in the IDE, asks the model to
rewrite the *program* into the subset. The rewrite then goes through the whole
compiler — lexer, parser, semantic analysis, codegen — exactly as a hand-written
program would. Nothing is special-cased for it, and the model's text never
reaches the C compiler: only the C that *this compiler* emitted is ever built.

```
python -m src.driver demos/repair_me.py --repair --run
```

Up to five rewrites are tried, each told everything the earlier ones got wrong;
`--repair-attempts N` raises or lowers that. An attempt is only spent when the
one before it was rejected, so a program that lands on the first try costs one
model call whatever the ceiling is.

The code it writes does not have to resemble yours. Only the output is
checked, so the model is told to **reimplement, not transliterate** - and that
it must not try to reproduce a construct the subset lacks. A decorated function
is just a function; a comprehension is just a loop; an iterator is just an
index. `demos/decorated.py` has a decorator factory, a closure, a class with a
property and a staticmethod, a comprehension, a tuple and f-strings. Nothing
survived the rewrite except the seven lines it prints:

```python
@traced("deposit")                     def deposit_traced(balance: int,
def deposit(self, amount):                                amount: int) -> int:
    self._balance += amount    ->          result: int = balance + amount
    return self._balance                   put("deposit ")
                                           write_int(amount)
                                           ...
```

### What has to be true before a repair is accepted

Five things, in order, and all five:

1. **The original agrees with itself.** It is run twice before anything else
   happens. A program that uses the clock, randomness, or anything else that
   varies has no fixed output to preserve, and matching one of its runs would
   say nothing about the next — so it is refused here, before a single token is
   spent.
2. **The rewrite compiles.** Not "looks plausible": the real pipeline accepts
   it, which means every construct in it has a deterministic C rule. If it does
   not, the compiler's own diagnostic goes back to the model and it tries again.
3. **The rewrite, under CPython, prints what the original printed.** This is
   safe to run only at this point, because compiling has *proved* the program
   is inside a subset with no syntax for opening a file, starting a process or
   reaching the network.
4. **The generated C builds.**
5. **That binary, run twice, prints what the original printed** — both times.

"Prints what it printed" means the **raw bytes** of stdout and the exit status.
Every run is captured as bytes, never as text: `subprocess.run(text=True)`
translates CRLF to LF on both sides of a comparison, so a real difference in
line endings — or in stdout encoding — would compare equal. On Windows this is
not hypothetical; the comparison there is of CRLF bytes on both sides, and it
is checked rather than assumed.

Steps 3 and 5 together are the point. If the rewrite *and* the compiled C both
match the original, the model preserved the behaviour and the compiler
translated it faithfully — and a mistake in one cannot be hidden by a matching
mistake in the other, because neither of them produced the output they are
being compared against.

```
your program under CPython, run twice   identical
the rewrite under CPython               identical
the compiled C, run 1                   identical
the compiled C, run 2                   identical
```

The one case where the argument does not hold is a program that prints nothing:
matching "no output" is not evidence. The report marks that `vacuous` and says
so rather than showing a tick.

### A program whose output is a window

A program that imports a windowing toolkit is refused before it is run, and
the refusal says why rather than just saying no. Two things were wrong with
running it first and deciding afterwards. The program's own window appears on
screen, twice, because the reference run runs it twice to check it agrees with
itself - nobody pressing Translate asked for their tkinter app to start. And
a windowed program has no bytes on stdout, which is the whole of what accepts
a repair: a rewrite that printed nothing was accepted while proving nothing,
and a rewrite that did the work and showed it was refused for not matching an
empty target. A seven-line program that summed 0 to 8 and printed nothing was
accepted as a repair of a 360-line Rubik's cube before this check existed.

Translating a program runs the C it produced, not the Python it came from. The
compiled program is the point; running the Python proves nothing about the
translation, and "Run Python" is still a button for when you want it. Set
`auto_run_c` to false in settings to turn the auto-run off.

### The hole in "the output is the whole spec"

If output is all that is checked, then for a program with **no input** the spec
is satisfied by a program that prints the answer and computes nothing. It
builds, it runs, its bytes match exactly - and nothing behavioural can tell it
apart from a real translation, because there is no input to vary. So the check
has to be on the text, and it rests on a property of the subset: **it cannot
compute a string.** No concatenation, no `str()`, no formatting. Every piece of
text in the output therefore came from a literal or is a number, and the only
thing a rewrite can cheat at is printing a *number* it should have worked out.

A candidate is rejected at the `shortcut` stage, before anything is run, when
either holds:

* it prints a run of **two or more digits** that appears in the expected output
  and nowhere in your source - `385` when your program computed it;
* it does **no arithmetic and no looping** at all and your program does - a
  list of `print` statements rather than a translation.

Both are retried with the reason, so the model is told what it did rather than
just refused. Neither fires on an honest rewrite: a single digit is exempt
(`write_int` legitimately prints `"0"` through `"9"`), a number already in your
source is exempt, and text without digits is exempt - an earlier version of
this check rejected `put("calling bump")`, where `bump` is a function name the
original wrote and the subset has no introspection to recover. Hardcoding that
is the correct translation, not a shortcut.

Stated rather than papered over: a program whose every computed number is a
single digit could in principle be pasted past the first rule. The second rule
is what covers it, and `tests/test_repair.py` pins both.

### The comparison window

In the IDE a failed translation opens the repair window: the program that did
not compile on the left with its error line marked, the accepted rewrite on the
right, the two aligned line for line so an unchanged line sits on the same row
on both sides. Replaced lines are tinted on the left, their replacements on the
right. Underneath is the evidence — the error that stopped the compiler, the
stage that rejected each attempt, the output both programs produce, and the
four runs that were compared.

**Run both and compare** runs your original under CPython and builds and runs
the C right there, prints both outputs, and diffs the raw bytes - the same
comparison the verdict rests on, repeated on demand so it can be watched rather
than believed:

```
  $ python your_program.py
sum of squares 1..10 = 385
  ...
  [exit 0]

  $ cc repair.c -lm -o repair  &&  ./repair
sum of squares 1..10 = 385
  ...
  [exit 0]

  identical: 115 bytes either way, both exit 0
  sha256 3b5c6b6c292f807d940fdc2e80e9fa3dc0d516729e741887229fcc9701217eb9
```

**Use this repair** puts the rewrite in the source pane and translates it again
as an ordinary translation. It is never applied silently.

The window opens by itself only when a translation **fails** and AI-assist is
on - there is nothing to compare when one succeeds. With the switch off, the
Pipeline tab says so and the toolbar's **✦ Repair** button starts the same thing
by hand.

### When repair is refused

A refusal is a real answer about the program, not a failure of the model:

```
Not repairable — the original program does not finish on its own (it was still
running after 20s), so it has no finite output to preserve. A program that opens
a window or waits for events cannot be repaired into this subset: there is
nothing to compare a rewrite against.
```

Those two are refusals *about the program*, and they cost nothing: both are
decided by running the original twice, before a token is spent.

Everything else is a question of whether the model finds a way, and the answer
is measured rather than asserted — see **How much it actually lands** below.
When it does not find one, every attempt is reported with the stage that
rejected it, so the failure is legible instead of a shrug.

`tests/test_repair.py` covers all of this with the model stubbed out, so it runs
offline — including that a program which does not agree with itself is refused
*without* a model call, and that a difference in line endings alone is still
reported as a difference.

### How much it actually lands

"It handles any program" is an opinion. `tests/corpus/` makes it a number:
thirteen ordinary Python programs the compiler refuses, each verified to run
cleanly and deterministically under CPython, so each is a fair question.
`python -m tools.repair_bench` asks all of them.

The first run landed **10 of 12**. Both failures burned every attempt at the
*compile* stage, and in both cases the model was guessing at a restriction
nothing had told it about:

| what it guessed | what is true |
|---|---|
| `s[0]`, `for ch in s`, `ord()` | a `str` cannot be indexed, iterated or taken apart, and nothing here can build one at runtime |
| `def f() -> list[int]` | a function cannot return an array — pass the destination in and return how many entries you filled |
| `list[list[int]]` | arrays hold numbers only; a grid is one flat array indexed `row * width + col` |
| `def f(a: list[int, 6])` | an array *parameter* is unsized — a sized one accepts that one length and nothing else, while `len(a)` works either way |

None of that was written down anywhere the model could read, so it is now in
the grammar it is given as spec, with a worked idiom for each in the prompt.
The same run also exposed a diagnostic of ours that no one could have acted
on: passing a `list[int, 3]` where a `list[int, 2]` was wanted reported
`argument 1 of 'total' (a) is list[int], got list[int]`, naming the same type
twice, because `describe()` dropped the size that was the entire problem.

What the fixes bought, re-measured: `cipher` went from failing three times at
the front end to landing, and the remaining failures moved from *compile* to
*equivalence* — from "cannot say it" to "said it slightly wrong", which is the
failure a retry can actually close. One more idiom came out of that: Python's
`repr` of a list is an exact format, `[`, items joined by `", "`, `]`, and a
rewrite that approximates it fails the byte comparison, so `write_list` is now
given verbatim alongside `write_int`.

Two caveats worth more than the headline number. The re-measurement stopped
early when the provider ran out of credit, so four programs that passed before
the prompt changes were not re-run against them. And although sampling is
pinned to temperature 0, one run per program is thin evidence about any single
program — `counting` passed before the changes and failed after. The aggregate
is the signal; a single row is not.

`matrix` and `counting` are open. Both compile and both get the output nearly
right, which is recorded here rather than rounded off.

The corpus is thirteen programs now: `bigconst.py` joined it when constant
integer overflow became a diagnostic instead of a wrapped number.

### Repair with no API key at all

Nothing in the repair path cares where a rewrite came from. The safety check,
the whole compiler, the shortcut check, the C build and the four compared runs
all happen *after* the answer arrives, and they are what decide the verdict.
So the answer can come from a file:

```
python -m tools.repair_offline tests/corpus/bigconst.py
```

The first run writes the prompt the compiler would have sent and stops. Fill
in the answer file, run the same command again, and the answer goes through
every check unchanged. An answer written by hand is held to exactly the same
standard as one that arrived over the network, because it is the same code —
`tests/test_repair_offline.py` pins that by feeding the local route an answer
that prints the number instead of working it out, and watching the shortcut
check refuse it.

What this mode is and is not evidence of matters. It shows that the subset can
express a given program and that the verification chain agrees. It says
nothing about whether a *model* can find that rewrite unaided; that is what
`tools/repair_bench.py` measures, and it needs an account. The accepted
answers are kept under `tests/corpus/answers/` so the first claim is
re-checkable by anyone with no account at all.

The IDE does the same thing without being asked. Press Repair with no credit
and, instead of stopping at the error, it writes the question into
`reports/repair_buffer/<name>-<digest>/prompt-1.md` and enables **Continue
from the answer file**. Write the rewrite into `answer-1.py` beside it, press
that button, and the answer goes through every check. The folder is keyed on a
digest of the source rather than the file name, because the file name is
whatever the editor last had open: without the digest, an answer written for
one program could be offered for another, and the refusal that followed would
make no sense to read. `python -m tools.check_buffer_flow` drives the real
window through that whole path and is the only test of it, because it needs a
desktop.

An empty account is reported as **blocked**, not as a failed attempt. The
difference is not cosmetic: scoring an unreachable model as a failure turns a
billing problem into "0/12 landed", which reads as a verdict on the compiler
and is not a measurement of anything.

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
| `src/ai_assist/` | gate (policy), multi-provider client, safety + compiler validator, verified simplifier, verified source repair |
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

When a translation fails and AI-assist is on, the IDE opens the **repair
comparison window**: the program that did not compile on the left, the verified
rewrite on the right, aligned line for line, with the four compared runs
underneath. See *Repair*, above.

* `Ctrl+Enter` translate · `F5` run the C · `Ctrl+S` save · `Ctrl+O` open ·
  `Ctrl+L` clear the console

## What's out of scope

Growable lists, sets, tuples, comprehensions, slicing, decorators, lambdas,
generators, exceptions, inheritance, closures, nested functions, chained
assignment and comparison, default and keyword arguments, string concatenation
and formatting. These are rejected by the grammar with a message that says so,
not silently mishandled — and `--repair` will offer to rewrite a program that
uses them, if its behaviour can be reproduced without them.

Fixed-size lists, fixed-key dicts and classes-as-structs **are** supported; what
each gives up — `append`, new keys, a base class — is the part that would need
an allocator or a table of function pointers.

Imports are accepted, but only for the two modules in
`src/semantic/modules.py` — see below. Anything else is refused by name with
the reason.

String concatenation and `str()` are excluded specifically because they need
heap allocation, and the generated C has no allocator or ownership model.
Half-implementing one would be worse than saying no.

Two behavioural limits of the generated code, both documented rather than
hidden. Python integers are arbitrary precision while `py_int` is 64-bit, and
recursion depth is the C stack's rather than `sys.getrecursionlimit()`.
Everything else matches CPython exactly, and the tests prove it by diffing
against CPython.

Integer width is split into the half that can be caught and the half that
cannot. A **constant** too large for 64 bits is refused at compile time with a
message naming the value, so `print(2 ** 100)` is an error rather than a
printed `0` — which is what it used to be, and a wrong answer that looks like
an answer is the worst thing a compiler can do. A value only reached at **run**
time still wraps: `n ** 100` for a computed `n` is not detected statically, and
is caught only by the output comparison when repair is involved. Both halves
have a corpus program: `tests/corpus/bigconst.py` for the constant,
`tests/corpus/bignum.py` for the runtime case.

The reason the distinction matters is that repair only ever runs on a program
the compiler **refused**. Turning the constant case into a diagnostic is what
puts it within reach of repair at all, and the subset can hold the answer: a
number carried as its digits in an array computes `2 ** 100` exactly.

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

## Asking what it would take

The compiler stops at the first construct it cannot handle, which is right
for a compiler and the wrong answer to "can this program be translated?".
`--check` surveys the whole file instead:

```
$ python -m src.driver some_gui_app.py --check
some_gui_app.py: 72 things outside the subset

FIXABLE — the subset can express these, the code needs a change  (28)
    def __init__(...)                  x3    first at line 24
    parameter 'move'                   x2    first at line 47
    code at module level               x1    first at line 233

NOT YET — the compiler could support these  (9)
    raise                              x3    first at line 10
    tuple unpacking                    x3    first at line 95

NEVER — these need an allocator, dispatch, or a runtime that is not here  (35)
    tuple                              x12   first at line 87
    tk.Label                           x3    first at line 138
    lambda                             x2    first at line 175

This program cannot be translated: 35 of the 72 are in the NEVER group.
```

The three verdicts are the point. **FIXABLE** is work on the program,
**NOT YET** is work on the compiler, and **NEVER** means no amount of either
will help — it needs a heap, dynamic dispatch, or a library the generated C
has no binding to. Two tests keep the survey honest in both directions: it
must not pass anything the compiler rejects, and must not flag anything the
compiler accepts.

The IDE shows the same survey under a failed translation, so the whole
picture is on screen rather than one error per attempt.

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

`demos/rubik3d.py` is the one that puts both halves together: a Rubik's cube
you can turn, in a window. Arrays hold the 54 stickers and the permutation
table, `gfx` draws them, and the whole thing compiles with no AI gate
involved:

```
$ python -m src.driver demos/rubik3d.py --run
call sites: 69   AI-resolved: 0   model queries: 0   (AI-assist disabled)
linking shims: gfx
```

W Y G B R O turn a face, 1-6 turn it back, space scrambles, arrows orbit.

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
* `tests/corpus/` is the repair corpus: ordinary Python programs the
  deterministic compiler refuses, each of which runs cleanly and
  deterministically under CPython, so each is a fair question to put to
  repair. `python -m tools.repair_bench` asks all of them and prints the
  score; `tests/test_corpus.py` keeps the corpus honest offline, asserting
  that every program still parses, is still refused by the compiler, defines
  `main()` without calling it, and can still serve as a reference. A program
  that stops being refused is the interesting failure there — it means the
  subset grew, and the corpus should grow with it.

## Requirements

Python 3.9+ and a C compiler (`gcc`, `clang`, `cc`, `tcc` or MSVC `cl`). The
compiler is found on `PATH`, via `$CC`, or in the usual Windows install
locations; if none is present you get an actionable message rather than a
crash, and everything except building and running still works.
