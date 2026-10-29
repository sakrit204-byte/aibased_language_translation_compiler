# Recorded answers

One accepted rewrite per corpus program, produced through
`tools/repair_offline.py` with no API key and no account, and kept because it
makes the claim re-checkable by anyone at any time:

    python -m tools.repair_offline tests/corpus/<name>.py \
        --dir tests/corpus/answers/<name>

Each file in here was put through the safety check, the whole compiler, the
shortcut check, the C build and the four compared runs before it was accepted.
Nothing in here is evidence that a *model* can produce the rewrite - that is
what `tools/repair_bench.py` measures, and it needs an account. These are
evidence of something narrower and still worth having: that the subset can
express the program at all, and that the verification chain agrees.

A recorded answer must compute its output, not contain it. An answer holding
the expected digits as a literal would be caught by the shortcut check on the
way in, and `tests/test_repair_offline.py` checks it again from the outside.
