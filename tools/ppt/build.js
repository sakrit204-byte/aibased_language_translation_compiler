/*
 * Build the project presentation.
 *
 *     node tools/ppt/build.js
 *
 * The deck is a companion to a live demonstration, not a substitute for one:
 * every content slide names what to do in the running application, and the
 * speaker notes are the script for doing it. The screenshots are there so the
 * three of us know exactly what we are pointing at, and as a fallback if the
 * machine misbehaves on the day.
 *
 * Twenty slides, three presenters, one section each.
 */
const pptxgen = require("pptxgenjs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const REPORT_SHOTS = path.join(ROOT, "report_submission", "screenshots");
const DECK_SHOTS = path.join(ROOT, "presentation", "screenshots");
const shot = (n) => path.join(REPORT_SHOTS, n);
const deckShot = (n) => path.join(DECK_SHOTS, n);

const THEME = {
  name: "Translation Compiler",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "14181F", lt1: "FFFFFF",
    dk2: "232A34", lt2: "EEF2F5",
    accent1: "0E7C66",   // proved
    accent2: "B3401A",   // refused
    accent3: "3E6B89",   // the AI boundary
    accent4: "7FB3A3",
    accent5: "5A6472",
    accent6: "C98A3C",
    hlink: "0E7C66", folHlink: "5A6472",
  },
};

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";            // 13.3 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "AI Based Language Translation Compiler";
pres.author = "Sakrit Kafle, Abendra Adhikari, Ankur Bhattarai";
pres.subject = "BCSE307P Compiler Design Lab";
const C = pres.SchemeColor;

const MONO = "Courier New";

/* ----------------------------------------------------------- layouts */

pres.defineSlideMaster({
  title: "TITLE_DARK",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.9, y: 2.3, w: 11.5, h: 1.5,
        fontSize: 40, bold: true, color: C.background1, align: "left" }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.9, y: 3.9, w: 11.5, h: 2.2,
        fontSize: 16, color: "B9C2CC", align: "left" }, text: "" } },
  ],
});

pres.defineSlideMaster({
  title: "DIVIDER",
  background: { color: C.text2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.9, y: 2.6, w: 11.5, h: 1.2,
        fontSize: 36, bold: true, color: C.background1, align: "left" }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.9, y: 3.9, w: 11.5, h: 1.8,
        fontSize: 16, color: "AEBAC6", align: "left" }, text: "" } },
  ],
  slideNumber: { x: 12.6, y: 6.95, fontSize: 10, color: "7A8794" },
});

pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: C.background1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.42, w: 12.1, h: 0.85,
        fontSize: 30, bold: true, color: C.text1, align: "left" }, text: "" } },
  ],
  slideNumber: { x: 12.6, y: 6.95, fontSize: 10, color: "9AA5B1" },
});

/* -------------------------------------------------------------- helpers */

function bullets(slide, items, opts) {
  // valign top is not optional here: the default box is 4.9 inches tall, so a
  // slide that moves the bullets down without also shrinking the box gets its
  // text centred in the leftover space - which is how the bullets on the
  // gate-off slide ended up underneath the demo bar and off the slide.
  const o = Object.assign({ x: 0.65, y: 1.45, w: 5.6, h: 4.9, fontSize: 15,
                            color: C.text1, lineSpacingMultiple: 1.05,
                            paraSpaceAfter: 9, valign: "top",
                            isTextBox: true, margin: 0 }, opts || {});
  slide.addText(items.map((t, i) => ({
    text: t, options: { bullet: true, breakLine: i < items.length - 1 },
  })), o);
}

function caption(slide, text, opts) {
  slide.addText(text, Object.assign({
    x: 0.65, y: 6.55, w: 12.0, h: 0.45, fontSize: 11, italic: true,
    color: "5A6472", isTextBox: true, margin: 0,
  }, opts || {}));
}

function codeBox(slide, lines, opts) {
  const o = Object.assign({ x: 6.6, y: 1.45, w: 6.1, h: 2.4 }, opts || {});
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.06,
    fill: { color: "F2F5F7" }, line: { color: "DCE3E9", width: 1 },
    objectName: "code frame",
  });
  slide.addText(lines.map((t, i) => ({
    text: t, options: { breakLine: i < lines.length - 1 },
  })), {
    x: o.x + 0.2, y: o.y + 0.16, w: o.w - 0.4, h: o.h - 0.32,
    fontSize: o.fontSize || 11.5, fontFace: MONO, color: "232A34",
    isTextBox: true, margin: 0, lineSpacingMultiple: 1.12,
  });
}

function badge(slide, text, color, opts) {
  const o = Object.assign({ x: 6.6, y: 4.1, w: 6.1, h: 0.6 }, opts || {});
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.1,
    fill: { color: color }, line: { color: color }, objectName: "verdict badge",
  });
  slide.addText(text, {
    x: o.x + 0.18, y: o.y, w: o.w - 0.36, h: o.h, fontSize: 13, bold: true,
    color: "FFFFFF", valign: "middle", isTextBox: true, margin: 0,
  });
}

function stat(slide, value, label, x, y, color) {
  slide.addText(value, { x: x, y: y, w: 2.75, h: 0.95, fontSize: 44, bold: true,
                         color: color || C.accent1, isTextBox: true, margin: 0 });
  slide.addText(label, { x: x, y: y + 0.92, w: 2.75, h: 0.75, fontSize: 12,
                         color: "5A6472", isTextBox: true, margin: 0 });
}

function demo(slide, text) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: 0.65, y: 5.72, w: 12.0, h: 0.62, rectRadius: 0.1,
    fill: { color: "E7F1EE" }, line: { color: "BFD9D2", width: 1 },
    objectName: "demo cue",
  });
  slide.addText([
    { text: "DEMO   ", options: { bold: true, color: "0E7C66" } },
    { text: text, options: { color: "232A34" } },
  ], { x: 0.9, y: 5.72, w: 11.5, h: 0.62, fontSize: 12.5, valign: "middle",
       isTextBox: true, margin: 0 });
}

function content(sectionTitle, title) {
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: sectionTitle });
  s.addText(title, { placeholder: "title" });
  return s;
}

/* =============================================== 1. title */

const SEC_A = "Part 1 - Sakrit Kafle";
const SEC_B = "Part 2 - Abendra Adhikari";
const SEC_C = "Part 3 - Ankur Bhattarai";

pres.addSection({ title: "Opening" });
{
  const s = pres.addSlide({ masterName: "TITLE_DARK", sectionTitle: "Opening" });
  s.addText("AI Based Language Translation Compiler", { placeholder: "title" });
  s.addText([
    { text: "A Python subset compiled to C, where every AI answer is checked by something that can refuse it", options: { breakLine: true, color: "B9C2CC" } },
    { text: "", options: { breakLine: true, fontSize: 8 } },
    { text: "Sakrit Kafle  24BCE2952      Abendra Adhikari  24BCE2952      Ankur Bhattarai  24BCE2953", options: { breakLine: true, color: "7FB3A3", bold: true } },
    { text: "BCSE307P  Compiler Design Lab", options: { color: "8A96A3" } },
  ], { placeholder: "body" });
  s.addText("This is a live demonstration. The slides mark what to show; the application does the talking.",
    { x: 0.9, y: 6.5, w: 11.5, h: 0.4, fontSize: 12, italic: true, color: "6E7B89",
      isTextBox: true, margin: 0 });
  s.addNotes("Open with the claim, not the architecture: this compiler uses AI in three places, and in none of them is the AI trusted. Everything it produces is checked by something that can say no. Say that the three of us will take one part each, and that the application will be running throughout.");
}

/* =============================================== Part 1 */

pres.addSection({ title: SEC_A });
{
  const s = pres.addSlide({ masterName: "DIVIDER", sectionTitle: SEC_A });
  s.addText("Part 1  The deterministic compiler", { placeholder: "title" });
  s.addText("Sakrit Kafle  -  the five stages, what each one produces, and the C that comes out", { placeholder: "body" });
  s.addNotes("Hand over. This part is the compiler proper: no AI at all. Make the point that everything in Part 1 would work with the network unplugged.");
}

{
  const s = content(SEC_A, "The problem, and the one rule we hold to");
  bullets(s, [
    "Python is dynamically typed and garbage collected. C is neither. A translation has to decide types, sizes and lifetimes that Python never writes down.",
    "So we compile a strict subset: fixed-size arrays, fixed-key dicts, classes as value structs, no allocator.",
    "The rule the whole project rests on: the output is the specification. A translation is correct only when the compiled C prints the same bytes as CPython.",
    "That rule is what makes AI usable here. An answer that cannot be checked is not accepted, so a wrong answer becomes a refusal rather than a wrong program.",
  ], { w: 6.0 });
  codeBox(s, [
    "def main() -> int:",
    "    total: int = 0",
    "    for i in range(1, 11):",
    "        total = total + i * i",
    "    print(total)",
    "    return 0",
  ], { y: 1.5, h: 1.95 });
  codeBox(s, [
    "py_int total = 0LL;",
    "for (py_int i = 1; i < 11; i++)",
    "    total = total + i * i;",
    'printf("%lld\\n", (long long)total);',
  ], { y: 3.7, h: 1.6 });
  demo(s, "Have the IDE open on this program before you start talking.");
  s.addNotes("Keep this short. The subset is not a limitation we are apologising for - it is the thing that makes a provable translation possible at all. Point at the two boxes: same program, left is what we write, right is what the compiler emits.");
}

{
  const s = content(SEC_A, "Five stages, and exactly where AI is allowed");
  const steps = [
    ["1", "Lexer", "tokens, indentation, escapes"],
    ["2", "Parser", "recursive descent, Python precedence"],
    ["3", "Semantic", "type inference, definite assignment, returns"],
    ["4", "Codegen", "rule templates and runtime helpers"],
    ["5", "out.c", "self contained; cc out.c -lm -o out"],
  ];
  steps.forEach((st, i) => {
    const y = 1.45 + i * 0.92;
    s.addShape(pres.ShapeType.ellipse, { x: 0.7, y: y, w: 0.52, h: 0.52,
      fill: { color: "0E7C66" }, line: { color: "0E7C66" }, objectName: "step " + st[0] });
    s.addText(st[0], { x: 0.7, y: y, w: 0.52, h: 0.52, fontSize: 14, bold: true,
      color: "FFFFFF", align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(st[1], { x: 1.42, y: y - 0.02, w: 2.2, h: 0.32, fontSize: 15, bold: true,
      color: C.text1, isTextBox: true, margin: 0 });
    s.addText(st[2], { x: 1.42, y: y + 0.26, w: 5.0, h: 0.32, fontSize: 12,
      color: "5A6472", isTextBox: true, margin: 0 });
  });
  s.addShape(pres.ShapeType.roundRect, { x: 7.1, y: 2.5, w: 5.5, h: 1.75, rectRadius: 0.1,
    fill: { color: "F4F1E8" }, line: { color: "C98A3C", width: 1.5 }, objectName: "gate" });
  s.addText([
    { text: "The AI gate\n", options: { bold: true, fontSize: 15, color: "8A5A14", breakLine: true } },
    { text: "Reached only from Codegen, and only for a call whose name the compiler has no rule for. One C expression, safety checked, compiled, cached.", options: { fontSize: 12.5, color: "4A3B20" } },
  ], { x: 7.35, y: 2.65, w: 5.0, h: 1.45, isTextBox: true, margin: 0 });
  s.addText("Stages 1 to 4 contain no AI whatsoever.", { x: 7.1, y: 4.45, w: 5.5, h: 0.4,
    fontSize: 13, bold: true, color: "0E7C66", isTextBox: true, margin: 0 });
  demo(s, "Show the Pipeline tab: the stage chips light up one by one as it compiles.");
  s.addNotes("This slide is the map for the whole talk. Say the gate is the ONLY route to a model inside the compiler, and that it is reached last, not first - the deterministic rules are tried first and the model only sees what is left over.");
}

{
  const s = content(SEC_A, "Stages 1 and 2  the front end");
  s.addImage({ path: shot("gui_02_stage_lexer.png"), x: 0.65, y: 1.72, w: 5.85, h: 2.78 });
  s.addImage({ path: shot("gui_03_stage_parser.png"), x: 6.8, y: 1.72, w: 5.85, h: 2.78 });
  s.addText("Lexer", { x: 0.65, y: 1.4, w: 5.85, h: 0.32, fontSize: 15, bold: true,
    color: "0E7C66", isTextBox: true, margin: 0 });
  s.addText("Parser", { x: 6.8, y: 1.4, w: 5.85, h: 0.32, fontSize: 15, bold: true,
    color: "0E7C66", isTextBox: true, margin: 0 });
  bullets(s, [
    "Hand written. INDENT and DEDENT come off a column stack - that is how a Python block becomes a C brace.",
    "Every token carries its line, which is what highlights the failing line later.",
  ], { x: 0.65, y: 4.62, w: 5.85, fontSize: 11.5, h: 1.05, paraSpaceAfter: 5 });
  bullets(s, [
    "Recursive descent, one function per precedence level, matching Python's own table.",
    "What the subset excludes is refused here by name, and the message says what to write instead.",
  ], { x: 6.8, y: 4.62, w: 5.85, fontSize: 11.5, h: 1.05, paraSpaceAfter: 5 });
  demo(s, "Click the Lexer chip, scroll the token table, then click Parser and expand a node.");
  s.addNotes("Two stages, one slide, because in the demo they are two clicks. Point out INDENT and DEDENT - that is the part people do not expect. On the parser, land this: a refusal here is a design decision, not a gap. We decided the subset has no decorators, so it says so in those words rather than reporting a syntax error.");
}

{
  const s = content(SEC_A, "Stage 3  Semantic analysis");
  s.addImage({ path: shot("gui_04_stage_semantic.png"), x: 0.65, y: 1.4, w: 7.4, h: 4.1 });
  bullets(s, [
    "Local type inference as a small dataflow pass. Types only widen along bool < int < float, so it reaches a fixpoint.",
    "Definite assignment: using a name before every path assigns it is an error here, not a garbage read in C.",
    "Return coverage: a function declared -> int must return on every path, because C would fall off the end.",
    "This phase has no AI by design. A wrong type here becomes a silently wrong value in C that no compiler would catch.",
  ], { x: 8.3, y: 1.4, w: 4.4, fontSize: 13 });
  demo(s, "Click Semantic and show the inferred type table.");
  caption(s, "Inferred types per function, the table codegen uses to hoist C declarations.");
  s.addNotes("This is the slide where we justify the AI policy. Read the last bullet out as written: this is the category of decision we refuse to delegate, and that refusal is in the source file as a comment.");
}

{
  const s = content(SEC_A, "Stage 4  Code generation, and the C that comes out");
  s.addImage({ path: shot("gui_05_stage_codegen.png"), x: 0.65, y: 1.4, w: 7.4, h: 4.1 });
  bullets(s, [
    "One rule per AST node. Only the runtime helpers a program actually uses are emitted.",
    "Helpers exist where C disagrees with Python: floor division, modulo of negatives, float formatting, power.",
    "Every integer literal carries LL. Without it C types the literal int and computes in 32 bits - 10^18 came out as -1486618624.",
    "The result is one self contained .c file: cc out.c -lm -o out, with no headers of ours to install.",
  ], { x: 8.3, y: 1.4, w: 4.4, fontSize: 13 });
  demo(s, "Press Translate, then F5 to run the generated C. The C runs, not the Python.");
  caption(s, "The generated C, beside the program it came from.");
  s.addNotes("The LL bullet is a real bug we found and fixed, with a test. If asked: the compiler used to emit bare literals, so an expression whose result fits in 64 bits could still wrap in 32. Worth mentioning because it shows the test suite earning its keep.");
}

/* =============================================== Part 2 */

pres.addSection({ title: SEC_B });
{
  const s = pres.addSlide({ masterName: "DIVIDER", sectionTitle: SEC_B });
  s.addText("Part 2  Proof, and the AI that is allowed in", { placeholder: "title" });
  s.addText("Abendra Adhikari  -  what byte for byte means, the gate, and whole program repair", { placeholder: "body" });
  s.addNotes("Hand over. This part answers the question the examiner will ask: how do you know the AI did not make it up?");
}

{
  const s = content(SEC_B, "What byte for byte actually means");
  bullets(s, [
    "Not 'the output looks right'. The raw bytes, compared with ==, including the trailing newline and the exit status.",
    "Captured as bytes, never as text. Python's text mode turns CRLF into LF on both sides, which hides exactly the difference we are looking for.",
    "The original is run twice before anything else happens. A program that disagrees with itself cannot have a repair verified against it.",
    "A program that never finishes has no finite output, so it is refused before a single token is spent.",
  ], { w: 6.1 });
  codeBox(s, [
    "target: 115 bytes, exit 0",
    "  your program, CPython      identical",
    "  the rewrite, CPython       identical",
    "  the compiled C, run 1      identical",
    "  the compiled C, run 2      identical",
  ], { x: 6.9, y: 1.5, w: 5.8, h: 1.85, fontSize: 11 });
  badge(s, "ACCEPTED   verified byte for byte", "0E7C66", { x: 6.9, y: 3.6, w: 5.8 });
  s.addText("Four runs, two programs, one answer. Anything less is a refusal.",
    { x: 6.9, y: 4.45, w: 5.8, h: 0.8, fontSize: 13, italic: true, color: "5A6472",
      isTextBox: true, margin: 0 });
  demo(s, "In the repair window press 'Run both and compare' - it redoes the comparison live.");
  s.addNotes("Stress that the comparison is repeatable on demand from inside the window that makes the claim. We did that deliberately so nothing has to be taken on trust.");
}

{
  const s = content(SEC_B, "The AI gate  one expression, and a wall around it");
  s.addImage({ path: shot("gui_07_ai_gate_report.png"), x: 0.65, y: 1.4, w: 7.4, h: 4.1 });
  bullets(s, [
    "Fires only for a call the compiler has no rule for, and only for a numeric one.",
    "The model may return a single C expression. Not a statement, not a function, not an include.",
    "The answer is checked against a whitelist, then compiled by a real C compiler, then cached by name and arity.",
    "A cached answer costs nothing on the next run, so the same program twice is one model call, not two.",
  ], { x: 8.3, y: 1.4, w: 4.4, fontSize: 13 });
  demo(s, "Turn AI assist on and translate the program that calls hypotenuse().");
  caption(s, "The gate's own report: which call sites it resolved, what it returned, and what it cost.");
  s.addNotes("The phrase to use is 'bounded by something that can refuse it'. The model proposes an expression; the C compiler decides whether it is real.");
}

{
  const s = content(SEC_B, "With the gate off, it is a hard error - never a guess");
  s.addImage({ path: shot("cli_05_gate_off_fails.png"), x: 0.65, y: 1.5, w: 7.6, h: 2.5 });
  bullets(s, [
    "The same program with AI assist off does not silently emit something plausible.",
    "It names the call it cannot translate and stops. The compiler fails closed.",
    "This is the difference between a tool you can trust and a tool that is usually right.",
    "It also makes the AI's contribution measurable: turn it off and the program does not compile.",
  ], { x: 0.65, y: 4.15, w: 12.0, h: 1.45, fontSize: 13.5, paraSpaceAfter: 5 });
  demo(s, "Toggle AI assist off, translate the same file, show the error naming the call.");
  s.addNotes("This is the strongest single slide for the AI-usage question. Toggling it live in front of them is more convincing than any sentence on a slide.");
}

{
  const s = content(SEC_B, "Repair  when the program itself is outside the subset");
  s.addImage({ path: shot("gui_09_repair_comparison.png"), x: 0.65, y: 1.4, w: 7.4, h: 4.1 });
  bullets(s, [
    "A different problem from the gate: here the whole program cannot be translated, not one call.",
    "The model is asked to reimplement it inside the subset - rewrite, not transliterate.",
    "The rewrite goes through the entire compiler like any other program. Nothing is special cased for it.",
    "Original on the left, rewrite on the right, and the rewrite is only shown once it has passed.",
  ], { x: 8.3, y: 1.4, w: 4.4, fontSize: 13 });
  demo(s, "Open demos/repair_me.py, translate it, let the repair window open.");
  s.addNotes("Make clear this is a rewrite, not a patch. The model is allowed to change the algorithm, the loop shapes, the function names - whatever gets the same output inside the subset.");
}

{
  const s = content(SEC_B, "What has to be true before a repair is accepted");
  const checks = [
    ["It parses, and declares main()", "a courtesy check before the compiler sees it"],
    ["The whole compiler accepts it", "lexer, parser, semantic, codegen"],
    ["It is not a pasted answer", "printing a constant instead of computing it is refused"],
    ["The C builds", "with a real C compiler, not a simulation"],
    ["Four runs agree, byte for byte", "original, rewrite, and the binary twice"],
  ];
  checks.forEach((c, i) => {
    const y = 1.45 + i * 0.84;
    s.addShape(pres.ShapeType.ellipse, { x: 0.7, y: y + 0.06, w: 0.38, h: 0.38,
      fill: { color: "0E7C66" }, line: { color: "0E7C66" }, objectName: "tick " + i });
    s.addText("✓", { x: 0.7, y: y + 0.06, w: 0.38, h: 0.38, fontSize: 13, bold: true,
      color: "FFFFFF", align: "center", valign: "middle", isTextBox: true, margin: 0 });
    s.addText(c[0], { x: 1.26, y: y, w: 6.0, h: 0.32, fontSize: 14.5, bold: true,
      color: C.text1, isTextBox: true, margin: 0 });
    s.addText(c[1], { x: 1.26, y: y + 0.3, w: 6.3, h: 0.32, fontSize: 12,
      color: "5A6472", isTextBox: true, margin: 0 });
  });
  s.addImage({ path: shot("gui_10_run_both.png"), x: 7.9, y: 1.45, w: 4.8, h: 3.4 });
  caption(s, "Any one of these failing is a refusal, and the original program is left untouched. The third check exists because the prompt shows the model the expected output: a rewrite that prints a figure it never computed is caught and rejected.");
  demo(s, "Point at the verdict line, then press 'Use this repair' to take it into the editor.");
  s.addNotes("If you only have time for one thing in this part, it is this list. Five gates, and the model's answer has to pass all five. Then say: the model never touches the compiler - its answer is just text that the compiler then judges. If asked whether the model could game the test: the prompt does show it the expected output so it can check itself, and a rewrite that prints a multi-digit figure found in that output but nowhere in its own arithmetic is refused. Single digits are exempt, because a digit-printing routine legitimately prints 0 to 9.");
}

/* =============================================== Part 3 */

pres.addSection({ title: SEC_C });
{
  const s = pres.addSlide({ masterName: "DIVIDER", sectionTitle: SEC_C });
  s.addText("Part 3  No key, the last resort, and the limits", { placeholder: "title" });
  s.addText("Ankur Bhattarai  -  running without an account, converting what cannot be proved, and what we do not claim", { placeholder: "body" });
  s.addNotes("Hand over. This part is the newest work and the most honest part of the project. Do not rush the limitations slide - it is the one that earns marks.");
}

{
  const s = content(SEC_C, "The pipeline does not need an API key");
  s.addImage({ path: deckShot("ppt_01_buffer_handoff.png"), x: 0.65, y: 1.4, w: 7.4, h: 4.1 });
  bullets(s, [
    "Nothing in the repair path cares where a rewrite came from. Every check happens after the answer arrives.",
    "So the question is written to a file, answered anywhere - by a person, or by an assistant in a terminal - and read back.",
    "The answer is picked up as soon as it is saved, and then goes through every check unchanged.",
    "An answer written by hand is refused on exactly the same grounds as a model's. The local route is not a privileged one.",
  ], { x: 8.3, y: 1.4, w: 4.4, fontSize: 12.5 });
  demo(s, "Translate the ursina cube with no credit: the question becomes prompt-1.md on screen.");
  caption(s, "The handoff: no model was reached, so the compiler writes the question it would have sent.");
  s.addNotes("Explain why this is not a workaround: the model call was always just filling in a blank, and the blank can be filled by anything. The safety check, the compiler, the build and the comparisons are what decide the verdict, and all of them still run.");
}

{
  const s = content(SEC_C, "The last stage  convert what cannot be proved");
  s.addImage({ path: deckShot("ppt_02_converted_verdict.png"), x: 0.65, y: 1.4, w: 7.4, h: 4.1 });
  bullets(s, [
    "A program whose behaviour is a window has no output, so byte comparison has nothing to work with.",
    "Refusing to act left such a program untranslated, which helped nobody.",
    "So a final stage converts it and reports only what it established: it compiles, it builds and links, it starts and stays up.",
    "It never reports 'verified'. A test asserts the word never appears alone in this report.",
  ], { x: 8.3, y: 1.4, w: 4.4, fontSize: 12.5 });
  demo(s, "Let the conversion finish and read the proof list out loud.");
  caption(s, "CONVERTED, NOT VERIFIED - with what was established, and what was not, side by side.");
  s.addNotes("The key sentence: this is the one result in the project that cannot be proved, so it must not be the one that looks strongest. That is why it is worded the way it is, and why the stage only runs when verification was impossible - never when it was possible and simply failed.");
}

{
  const s = content(SEC_C, "The payoff  an ursina program, running as native C");
  s.addImage({ path: deckShot("ppt_03_triple_cube.png"), x: 3.5, y: 1.35, w: 6.4, h: 4.2 });
  s.addText([
    { text: "Three Rubik's cubes\n", options: { bold: true, fontSize: 15, breakLine: true, color: "14181F" } },
    { text: "The original imports ursina and is refused outright. The conversion draws through gfx, our own window shim, whose API is nothing but numbers.\n\n", options: { fontSize: 12.5, color: "3A4450", breakLine: true } },
    { text: "290,248 bytes\n", options: { bold: true, fontSize: 20, color: "0E7C66", breakLine: true } },
    { text: "of native executable, no Python underneath", options: { fontSize: 11.5, color: "5A6472" } },
  ], { x: 0.65, y: 1.5, w: 2.65, h: 3.9, isTextBox: true, margin: 0 });
  s.addText([
    { text: "1 2 3  select a cube\n", options: { breakLine: true } },
    { text: "U D F B R L  turn a face\n", options: { breakLine: true } },
    { text: "SHIFT  reverse the turn\n", options: { breakLine: true } },
    { text: "S scramble   X reset\n", options: { breakLine: true } },
    { text: "SPACE  scramble all three", options: {} },
  ], { x: 10.1, y: 1.6, w: 2.6, h: 2.6, fontSize: 12, fontFace: MONO, color: "3A4450",
       isTextBox: true, margin: 0, lineSpacingMultiple: 1.25 });
  demo(s, "Run it. Select a cube, turn a face, scramble. Bright grout = selected, green = solved.");
  s.addNotes("This is the moment of the demonstration. Run the binary, not the Python. Turn a face so they see it is live. Then say the honest part: nothing compared this with the ursina original, and we do not claim it did - what we claim is that the subset can express it and the compiler built it.");
}

{
  const s = content(SEC_C, "Where AI is used, why, and what bounds it");
  const rows = [
    [{ text: "Where", options: { bold: true, color: "FFFFFF", fill: { color: "232A34" } } },
     { text: "Why it is used", options: { bold: true, color: "FFFFFF", fill: { color: "232A34" } } },
     { text: "What can refuse it", options: { bold: true, color: "FFFFFF", fill: { color: "232A34" } } }],
    ["Gate\none C expression",
     "A call the compiler has no rule for. Writing a rule for every maths function is not the point of the project.",
     "Whitelist, then a real C compiler. Cached, so it is asked once."],
    ["Simplify\ntidier C",
     "The emitted C is correct but verbose. A tidier version is a nicety, never a requirement.",
     "Both binaries are built and run; the outputs must match."],
    ["Repair\nwhole program",
     "The program is outside the subset entirely, so there is nothing to patch - it has to be rewritten.",
     "The whole compiler, the shortcut check, and four compared runs."],
    ["Port\nlast resort",
     "The program's behaviour is a window, so no comparison exists. Without this it stays untranslated.",
     "Compiler, build and start only - and it says so, in those words."],
  ];
  s.addTable(rows, {
    x: 0.65, y: 1.45, w: 12.0, colW: [2.5, 5.0, 4.5], rowH: 0.45,
    fontSize: 11.5, color: "232A34", valign: "top", border: { type: "solid", color: "DCE3E9", pt: 1 },
    fill: { color: "FFFFFF" },
  });
  s.addShape(pres.ShapeType.roundRect, { x: 0.65, y: 4.35, w: 12.0, h: 1.3, rectRadius: 0.1,
    fill: { color: "E7F1EE" }, line: { color: "BFD9D2", width: 1 }, objectName: "the pattern" });
  s.addText([
    { text: "In all four, the AI proposes and something deterministic disposes.\n",
      options: { bold: true, fontSize: 15, color: "0E7C66", breakLine: true } },
    { text: "The model is never the thing that decides. It writes text; a whitelist, a C compiler or a byte comparison decides whether that text becomes part of the program.", options: { fontSize: 12.5, color: "2C4A43" } },
  ], { x: 0.95, y: 4.55, w: 11.4, h: 0.95, isTextBox: true, margin: 0 });
  demo(s, "Nothing to run here - this is the slide to pause on and answer questions from.");
  s.addNotes("If there is one table to memorise, it is this one. Read the last column aloud - the examiner's real question is what stops the AI being wrong, and the answer is a different thing in each row.");
}

{
  const s = content(SEC_C, "What we do not claim");
  bullets(s, [
    "A conversion is not a proof. The port stage checks that a program compiles, builds and runs - never that it behaves like the original.",
    "Integer width. A constant too large for 64 bits is now refused with a diagnostic, but a value only reached at run time still wraps.",
    "Repair only runs on a program the compiler refused. A program that compiles and prints a wrong answer never reaches it.",
    "Two refusals are facts about the program, not weaknesses: a program that never finishes has no finite output, and one that disagrees with itself has no fixed output.",
    "The landing rate is measured, not asserted - and the measurement needs an account, so the number we quote is the last complete run.",
  ], { w: 7.3, h: 4.6, fontSize: 13.5 });
  s.addImage({ path: deckShot("ppt_04_int64_refused.png"), x: 8.15, y: 1.5, w: 4.5, h: 2.9 });
  caption(s, "2 ** 100 used to compile and print 0. It is now a diagnostic that names the value - which is also what lets repair act on it.");
  s.addNotes("Do not hurry this slide and do not apologise for it. Every item here is something we found by measuring rather than something we were told. The 2**100 example is the best one: a wrong answer that looked like an answer was the worst thing the compiler could do, so we turned it into an error.");
}

{
  const s = content(SEC_C, "Where it stands");
  stat(s, "724", "tests passing,\n13 skipped without a provider", 0.65, 1.5, "0E7C66");
  stat(s, "13", "ordinary Python programs\nin the repair corpus", 3.75, 1.5, "3E6B89");
  stat(s, "10/12", "landed in the last complete\nbenchmark run", 6.85, 1.5, "3E6B89");
  stat(s, "0", "AI calls in the\ndeterministic pipeline", 9.95, 1.5, "0E7C66");
  s.addShape(pres.ShapeType.roundRect, { x: 0.65, y: 3.75, w: 12.0, h: 1.75, rectRadius: 0.1,
    fill: { color: "F2F5F7" }, line: { color: "DCE3E9", width: 1 }, objectName: "closing" });
  s.addText([
    { text: "The claim we are willing to defend\n", options: { bold: true, fontSize: 15, color: "14181F", breakLine: true } },
    { text: "Three places in this compiler use a model, and in none of them is the model trusted. Each one is bounded by something that can refuse it - a whitelist, a C compiler, a byte comparison. Where no such bound exists, the result is labelled as a conversion and not as a proof, in those words, on the screen.", options: { fontSize: 13, color: "3A4450" } },
  ], { x: 0.95, y: 3.95, w: 11.4, h: 1.4, isTextBox: true, margin: 0 });
  demo(s, "Finish with the test suite running, or the cube still turning on screen.");
  s.addNotes("Close on the claim, not on thanks. If asked for the single best evidence, offer to toggle AI assist off and translate the same file - the compiler fails closed and names what is missing. That is the whole policy in one keystroke.");
}

/* ----------------------------------------------------------- write */

const OUT = path.join(ROOT, "presentation", "AI Based Language Translation Compiler.pptx");
pres.writeFile({ fileName: OUT }).then(async () => {
  const { applyTheme } = require(path.join(
    process.env.PPTX_SKILL || "", "scripts", "apply_theme.js"));
  await applyTheme(OUT, THEME);
  console.log("wrote " + OUT);
});
