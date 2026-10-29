"""Run the repair path with no API key, no account and no network.

The pipeline does not care where a rewrite came from. The safety check, the
whole compiler, the shortcut check, the C build and the four compared runs all
happen *after* the answer arrives, and they are what decide the verdict. So the
answer can be written by hand, or by an assistant in a terminal, and be held to
exactly the same standard as one that came over the wire - because it goes
through exactly the same code.

    python -m tools.repair_offline demos/thing.py

The first run writes the prompt the compiler would have sent and stops:

    reports/repair_offline/thing/prompt-1.md     <- the question
    reports/repair_offline/thing/answer-1.py     <- write the rewrite here

Write the rewrite into answer-1.py and run the same command again. The answer
is picked up, put through everything, and the verdict printed. If it is
rejected, the next prompt is written as prompt-2.md - and it contains the
rejection, exactly as the online path would have told the model - so the loop
can continue for as many rounds as --attempts allows.

Nothing here is a shortcut past a check. If an answer is wrong, this says so
in the same words the IDE would use, because it is the same report.
"""
import argparse
import datetime
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "reports", "repair_offline")


class NeedAnswer(Exception):
    """Raised to stop a run once the prompt for this attempt is on disk."""

    def __init__(self, attempt, prompt_path, answer_path):
        Exception.__init__(self, "answer %d is not written yet" % attempt)
        self.attempt = attempt
        self.prompt_path = prompt_path
        self.answer_path = answer_path


def _plain(text):
    """Punctuation a Windows console can encode."""
    for fancy, plain in (("…", "..."), ("—", "-"), ("–", "-"), ("→", "->"),
                         ("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                         ("•", "*"), ("✓", "+"), ("✗", "!"), ("·", "-")):
        text = text.replace(fancy, plain)
    return text


def out_dir_for(path, override=None):
    if override:
        return override
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(OUT, stem)


def compiler_error(source):
    """The error the compiler actually produced, which is what repair is given
    in the IDE. An empty string means it compiled, so this is not a repair
    case at all."""
    from src.driver import CompileError, compile_source_detailed
    try:
        compile_source_detailed(source)
    except CompileError as e:
        return str(e)
    return ""


def make_ask(folder, verbose=True):
    """An `ask` for repair(): read answer-N.py, or write prompt-N.md and stop."""

    def ask(prompt, attempt):
        prompt_path = os.path.join(folder, "prompt-%d.md" % attempt)
        answer_path = os.path.join(folder, "answer-%d.py" % attempt)
        with io.open(prompt_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(prompt)
        if not os.path.exists(answer_path):
            raise NeedAnswer(attempt, prompt_path, answer_path)
        with io.open(answer_path, encoding="utf-8") as f:
            answer = f.read()
        if not answer.strip():
            raise NeedAnswer(attempt, prompt_path, answer_path)
        if verbose:
            print("    using %s (%d lines)"
                  % (os.path.basename(answer_path), answer.count("\n")))
        return answer

    ask.label = "the answer file"
    return ask


def report_lines(report):
    """The verdict and the evidence behind it, as the IDE would show it."""
    lines = []
    if "ported" in report:
        # The conversion stage. Deliberately a different word: nothing here
        # was verified against the original, and "accepted" would imply it.
        verdict = "CONVERTED, NOT VERIFIED" if report["ported"] else "NOT CONVERTED"
        if report.get("blocked"):
            verdict = "BLOCKED"
        lines = ["verdict: %s" % verdict]
        if report.get("reason"):
            lines.append("reason : %s" % report["reason"])
        for item in report.get("proof", []):
            lines.append("  established    : %s" % item)
        if report.get("ported"):
            lines.append("  NOT established: that it behaves like the original; "
                         "nothing compared them")
        for attempt in report.get("attempts", []):
            mark = "+" if attempt.get("ok") else "!"
            lines.append("  %s attempt %s at the %s stage"
                         % (mark, attempt.get("attempt"), attempt.get("stage")))
            for line in str(attempt.get("reason", "")).strip().splitlines()[:6]:
                lines.append("      %s" % line)
        return lines

    verdict = "ACCEPTED" if report.get("accepted") else "NOT ACCEPTED"
    if report.get("accepted") and report.get("vacuous"):
        verdict = "ACCEPTED BUT VACUOUS"
    elif report.get("blocked"):
        verdict = "BLOCKED"
    lines.append("verdict: %s" % verdict)
    if report.get("reason"):
        lines.append("reason : %s" % report["reason"])

    reference = report.get("reference") or {}
    if reference.get("bytes") is not None:
        lines.append("target : %s bytes, exit %s"
                     % (reference.get("bytes"), reference.get("exit")))

    for attempt in report.get("attempts", []):
        mark = "+" if attempt.get("ok") else "!"
        lines.append("  %s attempt %s at the %s stage"
                     % (mark, attempt.get("attempt"), attempt.get("stage")))
        reason = str(attempt.get("reason", "")).strip()
        for line in reason.splitlines()[:8]:
            lines.append("      %s" % line)
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program", help="the .py file the compiler refused")
    ap.add_argument("--dir", default=None,
                    help="where the prompts and answers live "
                         "(default: reports/repair_offline/<name>/)")
    ap.add_argument("--attempts", type=int, default=5,
                    help="how many rounds of question and answer at most")
    ap.add_argument("--use-ai", action="store_true",
                    help="allow the AI gate while recompiling a rewrite; off "
                         "by default, because the gate needs the network too")
    ap.add_argument("--port", action="store_true",
                    help="run the last-resort conversion stage instead of "
                         "repair, for a program whose behaviour is a window "
                         "and so cannot be verified by its output at all")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    path = args.program if os.path.isabs(args.program) \
        else os.path.join(ROOT, args.program)
    if not os.path.exists(path):
        ap.error("no such program: %s" % args.program)
    with io.open(path, encoding="utf-8") as f:
        source = f.read()

    folder = out_dir_for(path, args.dir)
    os.makedirs(folder, exist_ok=True)

    error = compiler_error(source)
    if not error:
        print("the compiler accepts %s as it stands, so there is nothing to "
              "repair." % args.program)
        return 0

    print("program: %s" % args.program)
    print("refused: %s" % _plain(error.splitlines()[0]))
    print("folder : %s\n" % folder)

    def progress(stage, text, ok):
        if not args.quiet:
            mark = " " if ok is None else ("+" if ok else "!")
            first = text.splitlines()[0] if text else ""
            print(_plain("  %s %-10s %s" % (mark, stage, first)))

    from src.ai_assist.port import port
    from src.ai_assist.repair import repair

    try:
        if args.port:
            repaired, report = port(source, error, use_ai=args.use_ai,
                                    attempts=args.attempts, progress=progress,
                                    ask=make_ask(folder, not args.quiet))
        else:
            repaired, report = repair(source, error, use_ai=args.use_ai,
                                      attempts=args.attempts, progress=progress,
                                      ask=make_ask(folder, not args.quiet))
    except NeedAnswer as need:
        print("\nthe prompt for attempt %d is written:" % need.attempt)
        print("  %s" % need.prompt_path)
        size = os.path.getsize(need.prompt_path)
        print("  %d bytes, %d lines"
              % (size, io.open(need.prompt_path, encoding="utf-8").read().count("\n")))
        print("\nwrite the rewrite into:")
        print("  %s" % need.answer_path)
        print("\nthen run the same command again. Nothing is skipped when you do:")
        if args.port:
            # No comparison is promised here, because there is none to
            # make. Saying otherwise would be the one lie this mode
            # cannot afford.
            print("the answer goes through the safety check, the whole "
                  "compiler,\nthe C build, and starting the program. But nothing "
                  "compares it\nwith your original, so it will be reported as "
                  "converted, not verified.")
        else:
            print("the answer goes through the safety check, the whole "
                  "compiler, the\nshortcut check, the C build and the four "
                  "compared runs.")
        return 2

    print()
    for line in report_lines(report):
        print(_plain(line))

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slim = dict(report)
    if slim.get("reference"):
        slim["reference"] = dict(slim["reference"])
        slim["reference"].pop("raw", None)
    with io.open(os.path.join(folder, "report-%s.json" % stamp), "w",
                 encoding="utf-8") as f:
        json.dump(slim, f, indent=2, ensure_ascii=False, default=str)

    if repaired and args.port:
        out = os.path.join(folder, "converted.py")
        with io.open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(repaired)
        print("\nwrote %s" % out)
    elif repaired:
        accepted = os.path.join(folder, "accepted.py")
        with io.open(accepted, "w", encoding="utf-8", newline="\n") as f:
            f.write(repaired)
        print("\nwrote %s" % accepted)
    return 0 if report.get("accepted") else 1


if __name__ == "__main__":
    sys.exit(main())
