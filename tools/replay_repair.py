"""Run the real repair pipeline with a previously obtained model answer.

This exists for one reason: capturing the repair figures for the report needs
the pipeline to run end to end, and the API account can be out of credit at the
moment the figure is taken. Rather than typing a plausible-looking transcript
into the report by hand, this replays the rewrite the model actually produced
for the same program and lets everything else happen for real.

What is replayed: the model's answer, and only that.
What still runs live: the safety check, the whole compiler, the shortcut check,
the rewrite under CPython, the C build, both runs of the binary, and the
byte-for-byte comparison that decides the verdict. If any of those disagreed,
the run would be reported as a failure exactly as it normally is.

    python -m tools.replay_repair demos/repair_me.py --repair --run

The saved answer is <source>.repaired.py beside the program. With no saved
answer, or with credit available, nothing is replayed and the real model is
asked.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ai_assist import client  # noqa: E402

_real_query = client.query


def _saved_answer_for(argv):
    for arg in argv:
        if arg.endswith(".py") and os.path.exists(os.path.join(ROOT, arg)):
            candidate = os.path.join(ROOT, arg[:-3] + ".repaired.py")
            if os.path.exists(candidate):
                return candidate
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    saved = _saved_answer_for(argv)

    def query(prompt, **kwargs):
        try:
            return _real_query(prompt, **kwargs)
        except client.AIClientError as e:
            if not saved or "credit" not in str(e).lower():
                raise
            sys.stderr.write(
                "\n[replaying a previously obtained rewrite: %s]\n"
                "[the account is out of credit; every check below still runs "
                "for real]\n\n" % os.path.basename(saved))
            with io.open(saved, encoding="utf-8") as f:
                return f.read()

    client.query = query
    from src.driver import main as driver_main
    return driver_main(argv)


if __name__ == "__main__":
    sys.exit(main())
