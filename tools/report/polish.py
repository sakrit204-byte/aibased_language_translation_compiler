# -*- coding: utf-8 -*-
"""Three finishing passes over the report, applied to the document as it is.

This edits the existing file rather than rebuilding it, so hand edits made in
Word are kept. It is safe to run more than once: each pass checks whether its
change is already there.

  1. The architecture figure becomes a real diagram built from Word shapes,
     black outlines and black text with no fill, in place of the fixed width
     sketch that was there before.
  2. Every numbered section starts at the top of a page.
  3. A table cannot be split down the middle by a page boundary, and neither
     can a figure and its caption.

    python tools/report/polish.py
"""
import io
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
TARGET = os.path.join(PROJECT, "report_submission", "review 2.docx")
WORK = os.path.join(HERE, "_polish")

PT = 12700                      # EMU per point
TNR = ('<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
       'w:cs="Times New Roman"/>')
BLACK = '<w:color w:val="000000"/>'

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WPG_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"

PARA = re.compile(r"<w:p[ >](?:(?!</w:p>).)*?</w:p>|<w:p/>", re.S)
TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text_of(fragment):
    return "".join(TEXT.findall(fragment))


# ----------------------------------------------------------- the diagram

#: Each phase, with a short note of what it does. The notes are kept short on
#: purpose: a diagram that needs a paragraph in every box is a paragraph.
PHASES = [
    ("source.py", "the program as written"),
    ("Lexer", "tokens, indentation, escape decoding"),
    ("Parser", "recursive descent, Python precedence"),
    ("Semantic", "type inference, definite assignment, returns"),
    ("Codegen", "rule templates and runtime helpers"),
    ("out.c", "self contained, cc out.c -lm -o out"),
]

GATE = ["AI gate", "unknown call names only",
        "safety checked, compiled, cached"]

#: Sized to fit in the space left under the paragraph that introduces it, so
#: the figure stays on the page it starts on instead of pushing to the next
#: one and leaving half a page blank behind it.
COL_X, COL_W, BOX_H, STEP = 50, 215, 22, 32
GATE_X, GATE_W, GATE_H = 295, 145, 38
CANVAS_W, CANVAS_H = 440, 184
NAME_SIZE, NOTE_SIZE = 19, 14          # half points: 9.5pt and 7pt

_ids = {"next": 900}


def _next_id():
    _ids["next"] += 1
    return _ids["next"]


def _text_para(text, size, bold=False):
    weight = "<w:b/>" if bold else ""
    return ('<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="240" '
            'w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>'
            '<w:r><w:rPr>%s%s%s<w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr>'
            '<w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (TNR, weight, BLACK, size, size, esc(text)))


def _box(x, y, w, h, lines):
    """A rectangle with no fill, a black outline and centred black text."""
    body = "".join(_text_para(text, size, bold)
                   for text, size, bold in lines)
    ident = _next_id()
    return (
        '<wps:wsp><wps:cNvPr id="%d" name="Box %d"/><wps:cNvSpPr/>'
        '<wps:spPr>'
        '<a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '<a:noFill/>'
        '<a:ln w="9525" cap="flat"><a:solidFill><a:srgbClr val="000000"/>'
        '</a:solidFill><a:prstDash val="solid"/></a:ln>'
        '</wps:spPr>'
        '<wps:txbx><w:txbxContent>%s</w:txbxContent></wps:txbx>'
        '<wps:bodyPr rot="0" vert="horz" wrap="square" lIns="45720" '
        'tIns="0" rIns="45720" bIns="0" anchor="ctr" anchorCtr="0">'
        '<a:noAutofit/></wps:bodyPr></wps:wsp>'
        % (ident, ident, x * PT, y * PT, w * PT, h * PT, body))


def _arrow(x, y, dx, dy):
    """A black line with an arrow head at the far end."""
    ident = _next_id()
    return (
        '<wps:wsp><wps:cNvPr id="%d" name="Arrow %d"/><wps:cNvCnPr/>'
        '<wps:spPr>'
        '<a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
        '<a:prstGeom prst="straightConnector1"><a:avLst/></a:prstGeom>'
        '<a:ln w="9525" cap="flat"><a:solidFill><a:srgbClr val="000000"/>'
        '</a:solidFill><a:prstDash val="solid"/>'
        '<a:tailEnd type="triangle" w="med" len="med"/></a:ln>'
        '</wps:spPr><wps:bodyPr/></wps:wsp>'
        % (ident, ident, x * PT, y * PT, dx * PT, dy * PT))


def diagram_paragraph():
    shapes = []
    centre = COL_X + COL_W // 2
    for index, (name, note) in enumerate(PHASES):
        y = index * STEP
        shapes.append(_box(COL_X, y, COL_W, BOX_H,
                           [(name, NAME_SIZE, True), (note, NOTE_SIZE, False)]))
        if index < len(PHASES) - 1:
            shapes.append(_arrow(centre, y + BOX_H, 0, STEP - BOX_H))

    # The gate sits beside code generation, which is the phase that reaches
    # it, and the arrow into it is the only way a model is ever consulted.
    gate_y = 4 * STEP + BOX_H // 2 - GATE_H // 2
    shapes.append(_box(GATE_X, gate_y, GATE_W, GATE_H,
                       [(GATE[0], NAME_SIZE, True), (GATE[1], NOTE_SIZE, False),
                        (GATE[2], NOTE_SIZE, False)]))
    shapes.append(_arrow(COL_X + COL_W, 4 * STEP + BOX_H // 2,
                         GATE_X - (COL_X + COL_W), 0))

    ident = _next_id()
    group = (
        '<wpg:wgp xmlns:wpg="%s" xmlns:wps="%s">'
        '<wpg:cNvGrpSpPr/><wpg:grpSpPr>'
        '<a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="%d" cy="%d"/></a:xfrm>'
        '</wpg:grpSpPr>%s</wpg:wgp>'
        % (WPG_NS, WPS_NS, CANVAS_W * PT, CANVAS_H * PT,
           CANVAS_W * PT, CANVAS_H * PT, "".join(shapes)))

    drawing = (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="%d" cy="%d"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        '<wp:docPr id="%d" name="Architecture diagram"/>'
        '<wp:cNvGraphicFramePr/>'
        '<a:graphic xmlns:a="%s"><a:graphicData uri="%s">%s</a:graphicData>'
        '</a:graphic></wp:inline></w:drawing>'
        % (CANVAS_W * PT, CANVAS_H * PT, ident, A_NS, WPG_NS, group))

    return ('<w:p><w:pPr><w:spacing w:before="160" w:after="200"/>'
            '<w:jc w:val="center"/><w:ind w:left="720"/></w:pPr>'
            '<w:r>%s</w:r></w:p>' % drawing)


# ------------------------------------------------------------- the passes

def replace_diagram(xml):
    """Swap the fixed width sketch for the shape diagram."""
    if "Architecture diagram" in xml:
        return xml, "already a diagram"

    paragraphs = PARA.findall(xml)
    block = [i for i, p in enumerate(paragraphs)
             if "Consolas" in p and "w:pBdr" in p]
    if not block:
        return xml, "no sketch found"
    first, last = block[0], block[-1]
    if last - first + 1 != len(block):
        return xml, "the sketch is not one run of paragraphs"

    start = xml.find(paragraphs[first])
    end = xml.find(paragraphs[last]) + len(paragraphs[last])
    xml = xml[:start] + diagram_paragraph() + xml[end:]
    return xml, "replaced %d sketch line(s)" % len(block)


SECTIONS = ["Abstract:", "Introduction:", "Novelty in Project:",
            "Proposed Work:", "Implementation", "Conclusion:",
            "Project Repository Link"]


def section_page_breaks(xml):
    """Start every numbered section at the top of a page."""
    done = 0
    for paragraph in PARA.findall(xml):
        body = text_of(paragraph).strip()
        if not any(body.startswith(name) for name in SECTIONS):
            continue
        if "w:numPr" not in paragraph:          # not a numbered heading
            continue
        if "pageBreakBefore" in paragraph:
            continue
        # pageBreakBefore has a fixed place in the order of pPr children: it
        # follows pStyle, keepNext and keepLines and precedes everything else.
        updated = re.sub(r'(<w:pPr>(?:<w:pStyle[^/]*/>)?)',
                         r'\1<w:pageBreakBefore/>', paragraph, count=1)
        if updated != paragraph:
            xml = xml.replace(paragraph, updated, 1)
            done += 1
    return xml, "%d section(s) now start a page" % done


def trim_cover_spacers(xml):
    """Drop the blank paragraphs that pad the bottom of the cover page.

    They were doing the job a page break now does properly. Left in place they
    spill past the end of the cover, and the break before the first section
    then lands a page later, leaving an entirely blank page behind it.
    """
    paragraphs = PARA.findall(xml)
    index = None
    for position, paragraph in enumerate(paragraphs):
        if text_of(paragraph).strip().startswith("Abstract:"):
            index = position
            break
    if index is None:
        return xml, "no first section found"

    removed = 0
    position = index - 1
    while position >= 0:
        paragraph = paragraphs[position]
        if text_of(paragraph).strip() or "<w:drawing" in paragraph:
            break
        at = xml.find(paragraph)
        if at < 0:
            break
        # Only paragraphs in the body itself, never one inside the cover table.
        if xml.rfind("</w:tbl>", 0, at) < xml.rfind("<w:tbl>", 0, at):
            break
        xml = xml[:at] + xml[at + len(paragraph):]
        removed += 1
        position -= 1
    return xml, "%d blank spacer(s) removed" % removed


def keep_tables_whole(xml):
    """Stop a table being split across a page boundary.

    Two things are needed. Each row is marked so it cannot be broken in the
    middle, and every paragraph except those in the final row is tied to what
    follows, which is what makes Word move the whole table down rather than
    leave part of it behind.
    """
    tables = list(re.finditer(r"<w:tbl>.*?</w:tbl>", xml, re.S))
    changed = 0
    for match in reversed(tables):
        table = match.group(0)
        # The cover table is layout, not content, and is left alone.
        if "Reg No" in table or "MUTHUNAGAI" in table:
            continue
        rows = re.findall(r"<w:tr[ >].*?</w:tr>", table, re.S)
        if len(rows) < 2:
            continue
        updated = table
        for index, row in enumerate(rows):
            new_row = row
            if "cantSplit" not in new_row:
                if "<w:trPr>" in new_row:
                    new_row = new_row.replace("<w:trPr>",
                                              "<w:trPr><w:cantSplit/>", 1)
                else:
                    new_row = re.sub(r"(<w:tr[ >][^>]*>)",
                                     r"\1<w:trPr><w:cantSplit/></w:trPr>",
                                     new_row, count=1)
            if index < len(rows) - 1:
                new_row = new_row.replace("<w:pPr>", "<w:pPr><w:keepNext/>")
                new_row = re.sub(r"(<w:p(?: [^>]*)?>)(?!<w:pPr>)",
                                 r"\1<w:pPr><w:keepNext/></w:pPr>", new_row)
            updated = updated.replace(row, new_row, 1)
        if updated != table:
            xml = xml[:match.start()] + updated + xml[match.end():]
            changed += 1
    return xml, "%d content table(s) held together" % changed


def main():
    if not os.path.exists(TARGET):
        raise SystemExit("not found: %s" % TARGET)

    backup = TARGET.replace(".docx", ".before-polish.docx")
    shutil.copy(TARGET, backup)

    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    with zipfile.ZipFile(TARGET) as z:
        names = z.namelist()
        z.extractall(WORK)

    doc = os.path.join(WORK, "word", "document.xml")
    with io.open(doc, encoding="utf-8") as f:
        xml = f.read()

    for step in (replace_diagram, trim_cover_spacers, section_page_breaks,
                 keep_tables_whole):
        xml, note = step(xml)
        print("  %-22s %s" % (step.__name__, note))

    with io.open(doc, "w", encoding="utf-8", newline="") as f:
        f.write(xml)

    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.write(os.path.join(WORK, name.replace("/", os.sep)), name)

    print("wrote", TARGET)
    print("backup", backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
