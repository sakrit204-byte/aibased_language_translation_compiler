"""Measure how much ordinary Python the repair path can actually land.

Every program in tests/corpus/ is ordinary Python that the deterministic
compiler refuses, and that runs cleanly and deterministically under CPython.
So each one is a fair question to put to repair: rewrite this into the subset
so the compiled C prints the same bytes.

The number this prints is the only honest answer to "can it handle any
program". It costs real model calls, so it is a tool, not a test:

    python -m tools.repair_bench                 # the whole corpus
    python -m tools.repair_bench --only bignum   # one program
    python -m tools.repair_bench --attempts 5

Results land in reports/repair_bench/<timestamp>/, one JSON per program plus
a summary, so two runs can be compared after a change to the prompt.
"""
import argparse
import datetime
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CORPUS = os.path.join(ROOT, "tests", "corpus")
REPORTS = os.path.join(ROOT, "reports", "repair_bench")


def _plain(text):
    """Punctuation a Windows console can encode."""
    for fancy, plain in (("…", "..."), ("—", "-"), ("–", "-"),
                         ("→", "->"), ("‘", "'"), ("’", "'"),
                         ("“", '"'), ("”", '"')):
        text = text.replace(fancy, plain)
    return text


def run_one(path, attempts, model, verbose):
    from src.ai_assist.repair import repair

    name = os.path.splitext(os.path.basename(path))[0]
    with io.open(path, encoding="utf-8") as f:
        source = f.read()

    # The error repair is given is the one the compiler actually produced,
    # because that is what it would be given in the IDE.
    error = ""
    try:
        from src.driver import CompileError, compile_source_detailed
        try:
            compile_source_detailed(source)
        except CompileError as e:
            error = str(e)
        else:
            error = "(the compiler accepted it; this is not a repair case)"
    except Exception as e:                       # pragma: no cover - tooling
        error = "could not compile: %s" % e

    def progress(stage, text, ok):
        if verbose:
            mark = " " if ok is None else ("+" if ok else "!")
            print(_plain("    %s %-10s %s" % (mark, stage, text.splitlines()[0]
                                              if text else "")))

    started = time.time()
    repaired, report = repair(source, error, model=model, use_ai=True,
                              attempts=attempts, progress=progress)
    report["seconds"] = round(time.time() - started, 1)
    report["name"] = name
    report["path"] = os.path.relpath(path, ROOT)
    return repaired, report


def summarise(report):
    """One line per program: did it land, and where did the attempts fail."""
    trail = " > ".join(
        "%s%s" % (a.get("stage", "?"), "" if a.get("ok") else "*")
        for a in report.get("attempts", []))
    if report.get("accepted"):
        verdict = "PASS"
        if report.get("vacuous"):
            verdict = "VACUOUS"
    elif report.get("blocked"):
        verdict = "BLOCKED"
    else:
        verdict = "FAIL"
    return verdict, trail


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", action="append", default=[],
                    help="corpus program(s) to run, by stem (repeatable)")
    ap.add_argument("--attempts", type=int, default=None,
                    help="attempts per program (default: repair's own)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--quiet", action="store_true",
                    help="only the summary table")
    ap.add_argument("--out", default=None, help="results directory")
    args = ap.parse_args(argv)

    from src.ai_assist.repair import MAX_ATTEMPTS
    attempts = args.attempts or MAX_ATTEMPTS

    paths = sorted(os.path.join(CORPUS, f) for f in os.listdir(CORPUS)
                   if f.endswith(".py"))
    if args.only:
        wanted = {name.replace(".py", "") for name in args.only}
        paths = [p for p in paths
                 if os.path.splitext(os.path.basename(p))[0] in wanted]
        if not paths:
            ap.error("no corpus program matches %s" % ", ".join(sorted(wanted)))

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or os.path.join(REPORTS, stamp)
    os.makedirs(out_dir, exist_ok=True)

    print("repair bench: %d program(s), up to %d attempt(s) each"
          % (len(paths), attempts))
    print("results -> %s\n" % out_dir)

    rows = []
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        print("--- %s" % name)
        try:
            repaired, report = run_one(path, attempts, args.model,
                                       not args.quiet)
        except Exception as e:                   # pragma: no cover - tooling
            print(_plain("    !! %s: %s" % (type(e).__name__, e)))
            rows.append((name, "ERROR", "%s: %s" % (type(e).__name__, e), 0, 0))
            continue

        verdict, trail = summarise(report)
        print(_plain("    => %s  (%s attempt(s), %.0fs)  %s"
                     % (verdict, len(report.get("attempts", [])),
                        report["seconds"], report["reason"].splitlines()[0]
                        if report.get("reason") else "")))
        rows.append((name, verdict, trail, len(report.get("attempts", [])),
                     report["seconds"]))

        # The bytes themselves are not JSON, and the text form is already in
        # the report beside them.
        reference = report.get("reference")
        if reference:
            reference.pop("raw", None)
        with io.open(os.path.join(out_dir, name + ".json"), "w",
                     encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        if repaired:
            with io.open(os.path.join(out_dir, name + ".repaired.py"), "w",
                         encoding="utf-8") as f:
                f.write(repaired)

    passed = sum(1 for r in rows if r[1] == "PASS")
    print("\n%-12s %-8s %-6s %-7s %s"
          % ("program", "verdict", "tries", "seconds", "stages (* = failed)"))
    for name, verdict, trail, tries, seconds in rows:
        print("%-12s %-8s %-6s %-7s %s"
              % (name, verdict, tries, seconds, trail))
    print("\n%d/%d landed" % (passed, len(rows)))

    with io.open(os.path.join(out_dir, "summary.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"attempts": attempts, "passed": passed, "total": len(rows),
                   "rows": rows}, f, indent=2)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
