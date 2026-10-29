# Run sheet

The deck is the companion, not the presentation. The application is the
presentation. Every content slide carries a green **DEMO** line saying what to
do on screen at that point, and the speaker notes in the .pptx are the script
for saying it.

Twenty slides, three presenters, roughly fifteen minutes with questions.

## Before you start

1. Open the IDE: `python gui.py`. Leave it on `demos/repair_me.py`.
2. Check a C compiler is found - the status bar says so. Without one the
   repair and conversion stages cannot run at all.
3. Build the cube once beforehand so the demo is instant:
   `python -m src.driver demos/rubik_triple.py --run`, then close its window.
4. Decide about the network. With no credit, the repair and conversion stages
   hand the question over as a file, which is a perfectly good thing to show -
   see slides 15 and 16. Do not discover this on stage; know which one you are
   showing.

## Who takes what

| Slides | Presenter | Thread |
|---|---|---|
| 2 - 7 | Sakrit Kafle | The deterministic compiler: five stages, no AI anywhere |
| 8 - 13 | Abendra Adhikari | What byte for byte means, the gate, repair and its five checks |
| 14 - 20 | Ankur Bhattarai | Running with no key, the last resort, and the limits |

Slide 1 is whoever opens. Slides 2, 8 and 14 are the handover points.

## The demo order, end to end

1. **Translate a good program.** Press Translate on `repair_me.py`'s fixed
   sibling or any sample. The C appears and runs by itself - say that it is
   the C running, not the Python.
2. **Walk the stages.** Click Lexer, Parser, Semantic, Codegen in the Pipeline
   tab. One sentence each; the screenshots on slides 5 to 7 are the same views.
3. **Turn AI assist off** and translate `tests/programs/ai/01_pythagoras.py`.
   It fails and names the call. Turn it on, translate again, it resolves.
   This is the strongest two minutes in the whole demonstration.
4. **Repair.** Open `demos/repair_me.py` and translate. The comparison window
   opens. Press *Run both and compare* so the verdict is earned on stage.
5. **No key.** Translate `tests/programs/ported/triple_ursina.py`. Repair
   declines - a window has no output - and the pipeline falls through to the
   conversion stage, which writes the question to a file.
6. **The payoff.** Run `demos/rubik_triple.py`'s binary. Select a cube with 2,
   turn a face with R, scramble with S. Bright grout is the selected cube,
   green is solved.
7. **Close** on slide 20, with the cube still turning or the test suite running.

## If something goes wrong

- **No C compiler.** Stages 1 to 4 and every screenshot still work. Say so and
  carry on; the slides carry the evidence for the rest.
- **The model is unreachable.** This is not a failure - it is slide 15. Show
  the handoff, write the answer into the file, and the stage carries on.
- **The cube will not build.** `presentation/screenshots/ppt_03_triple_cube.png`
  is the same window, photographed. Use it and say it was captured earlier.
- **A window will not close.** Escape quits the cube. The IDE's repair window
  has a Close button bottom right.

## The three questions you should expect

**"How do you know the AI did not just make it up?"** Nothing it produces is
accepted without something deterministic agreeing: a whitelist and a real C
compiler for the gate, four compared runs for a repair. Offer to toggle AI
assist off and translate the same file - the compiler fails closed and names
what is missing.

**"Is the conversion verified?"** No, and the program says so in those words.
It is the one result that cannot be proved, because a window has no output to
compare. We report what was established - it compiles, it builds, it runs -
and say plainly what was not.

**"What does it not handle?"** Slide 19, and do not hurry it. Runtime integer
overflow still wraps. Repair only runs on a program the compiler refused, so a
program that compiles and prints a wrong answer never reaches it. Both were
found by measuring, not by being told.

## Rebuilding the deck

```
python -m tools.shots_ppt          # the four figures that are not in the report set
node tools/ppt/build.js            # needs NODE_PATH=tools/ppt/node_modules
```

The eleven IDE and nine CLI figures come from `report_submission/screenshots/`,
captured by `tools/shots_gui.py` and `tools/shots_cli.py`.
