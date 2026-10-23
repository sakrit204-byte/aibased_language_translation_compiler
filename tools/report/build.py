# -*- coding: utf-8 -*-
"""Fill the supplied report template with this project's report.

The template is edited in place rather than rebuilt, so the cover page, the
logo, the numbered section list and every style come from the original file
exactly as supplied.
"""
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import content as C

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(HERE))
TEMPLATE = os.path.join(HERE, "template.docx")
SHOTS = os.path.join(PROJECT, "report_submission", "screenshots")
WORK = os.path.join(HERE, "_build")
TARGET = os.path.join(PROJECT, "report_submission", "review 2.docx")

TNR = ('<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
       'w:cs="Times New Roman"/>')
MONO = ('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>')

BODY_INDENT = 720           # line body text up under the numbered headings
USABLE_TWIPS = 9026 - BODY_INDENT
EMU_PER_TWIP = 635


def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))


def run(text, size=24, bold=False, mono=False):
    fonts = MONO if mono else TNR
    bits = [fonts]
    if bold:
        bits.append("<w:b/><w:bCs/>")
    bits.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size))
    return ('<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
            % ("".join(bits), esc(text)))


def para(text, size=24, bold=False, indent=BODY_INDENT, justify=True,
         before=0, after=120, mono=False, align=None):
    props = ['<w:spacing w:before="%d" w:after="%d" w:line="276" '
             'w:lineRule="auto"/>' % (before, after)]
    if indent:
        props.append('<w:ind w:left="%d"/>' % indent)
    if align:
        props.append('<w:jc w:val="%s"/>' % align)
    elif justify:
        props.append('<w:jc w:val="both"/>')
    return ("<w:p><w:pPr>%s</w:pPr>%s</w:p>"
            % ("".join(props), run(text, size, bold, mono)))


def heading(text):
    return para(text, size=24, bold=True, justify=False, before=240, after=100)


def bullet(text):
    props = ('<w:pStyle w:val="ListParagraph"/>'
             '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="9"/></w:numPr>'
             '<w:spacing w:before="0" w:after="100" w:line="276" '
             'w:lineRule="auto"/>'
             '<w:ind w:left="1134" w:hanging="283"/>'
             '<w:jc w:val="both"/>')
    return "<w:p><w:pPr>%s</w:pPr>%s</w:p>" % (props, run(text))


def code_block(text):
    """A fixed width block inside a thin box, drawn once around the whole run
    of lines rather than once per line."""
    lines = text.split("\n")
    out = []
    for index, line in enumerate(lines):
        borders = ['<w:left w:val="single" w:sz="4" w:space="4" '
                   'w:color="808080"/>',
                   '<w:right w:val="single" w:sz="4" w:space="4" '
                   'w:color="808080"/>']
        if index == 0:
            borders.insert(0, '<w:top w:val="single" w:sz="4" w:space="2" '
                              'w:color="808080"/>')
        if index == len(lines) - 1:
            borders.append('<w:bottom w:val="single" w:sz="4" w:space="2" '
                           'w:color="808080"/>')
        # Every line but the last is tied to the one after it, so the box is
        # not broken in half by a page boundary.
        keep = "<w:keepNext/>" if index < len(lines) - 1 else ""
        props = ('%s<w:pBdr>%s</w:pBdr>'
                 '<w:spacing w:before="0" w:after="0" w:line="240" '
                 'w:lineRule="auto"/>'
                 '<w:ind w:left="%d" w:right="113"/>'
                 % (keep, "".join(borders), BODY_INDENT))
        out.append("<w:p><w:pPr>%s</w:pPr>%s</w:p>"
                   % (props, run(line or " ", size=18, mono=True)))
    return "".join(out)


def table(rows):
    """First row is the header. Columns share the usable width evenly, with a
    narrower first column because it holds the shortest text."""
    columns = len(rows[0])
    if columns == 3:
        widths = [int(USABLE_TWIPS * f) for f in (0.26, 0.24, 0.50)]
    else:
        widths = [USABLE_TWIPS // columns] * columns
    widths[-1] = USABLE_TWIPS - sum(widths[:-1])

    grid = "".join('<w:gridCol w:w="%d"/>' % w for w in widths)
    body = []
    for index, row in enumerate(rows):
        cells = []
        for width, text in zip(widths, row):
            cell_para = ('<w:p><w:pPr><w:spacing w:before="40" w:after="40" '
                         'w:line="240" w:lineRule="auto"/></w:pPr>%s</w:p>'
                         % run(text, size=22, bold=(index == 0)))
            cells.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
                         '<w:vAlign w:val="center"/></w:tcPr>%s</w:tc>'
                         % (width, cell_para))
        body.append("<w:tr>%s</w:tr>" % "".join(cells))

    props = ('<w:tblPr><w:tblStyle w:val="TableGrid"/>'
             '<w:tblW w:w="%d" w:type="dxa"/>'
             '<w:tblInd w:w="%d" w:type="dxa"/>'
             '<w:tblBorders>'
             '<w:top w:val="single" w:sz="4" w:color="000000"/>'
             '<w:left w:val="single" w:sz="4" w:color="000000"/>'
             '<w:bottom w:val="single" w:sz="4" w:color="000000"/>'
             '<w:right w:val="single" w:sz="4" w:color="000000"/>'
             '<w:insideH w:val="single" w:sz="4" w:color="000000"/>'
             '<w:insideV w:val="single" w:sz="4" w:color="000000"/>'
             '</w:tblBorders></w:tblPr>'
             % (USABLE_TWIPS, BODY_INDENT))
    return ('<w:tbl>%s<w:tblGrid>%s</w:tblGrid>%s</w:tbl>'
            '<w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>'
            % (props, grid, "".join(body)))


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(26)
    return struct.unpack(">II", head[16:24])


_image_state = {"rel": 100, "doc": 1000}


def figure(filename, caption, number, rels, media_dir):
    source = os.path.join(SHOTS, filename)
    width_px, height_px = png_size(source)
    shutil.copy(source, os.path.join(media_dir, filename))

    _image_state["rel"] += 1
    _image_state["doc"] += 1
    rel_id = "rIdImg%d" % _image_state["rel"]
    doc_id = _image_state["doc"]
    rels.append('<Relationship Id="%s" Type="http://schemas.openxmlformats.org'
                '/officeDocument/2006/relationships/image" Target="media/%s"/>'
                % (rel_id, filename))

    # Fit to the text width, and cap the height so one figure cannot take a
    # whole page on its own.
    cx = USABLE_TWIPS * EMU_PER_TWIP
    cy = int(cx * height_px / width_px)
    max_cy = int(7.3 * 914400)
    if cy > max_cy:
        cx = int(cx * max_cy / cy)
        cy = max_cy

    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    pic = "http://schemas.openxmlformats.org/drawingml/2006/picture"
    drawing = (
        '<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        '<wp:extent cx="%d" cy="%d"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        '<wp:docPr id="%d" name="Figure %d"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="%s" '
        'noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic xmlns:a="%s"><a:graphicData uri="%s">'
        '<pic:pic xmlns:pic="%s">'
        '<pic:nvPicPr><pic:cNvPr id="%d" name="%s"/><pic:cNvPicPr/>'
        '</pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/>'
        '</a:stretch></pic:blipFill>'
        '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'
        % (cx, cy, doc_id, number, a, a, pic, pic, doc_id, filename, rel_id,
           cx, cy))

    # keepNext holds the caption on the same page as the picture it names.
    image_para = ('<w:p><w:pPr><w:keepNext/>'
                  '<w:spacing w:before="160" w:after="60"/>'
                  '<w:jc w:val="center"/><w:ind w:left="%d"/></w:pPr>'
                  '<w:r>%s</w:r></w:p>' % (BODY_INDENT, drawing))
    caption_para = para("Figure %d. %s" % (number, caption), size=20,
                        justify=False, align="center", after=220)
    return image_para + caption_para


def render(blocks, rels, media_dir, figure_counter):
    out = []
    for block in blocks:
        kind = block[0]
        if kind == "p":
            out.append(para(block[1]))
        elif kind == "h":
            out.append(heading(block[1]))
        elif kind == "b":
            out.append(bullet(block[1]))
        elif kind == "code":
            out.append(code_block(block[1]))
        elif kind == "table":
            out.append(table(block[1]))
        elif kind == "fig":
            figure_counter[0] += 1
            out.append(figure(block[1], block[2], figure_counter[0], rels,
                              media_dir))
        else:
            raise SystemExit("unknown block kind: %r" % kind)
    return "".join(out)


BULLET_DEFINITION = (
    '<w:abstractNum w:abstractNumId="9" w15:restartNumberingAfterBreak="0">'
    '<w:nsid w:val="3A1B0C7D"/><w:multiLevelType w:val="hybridMultilevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
    '<w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="1134" w:hanging="283"/></w:pPr>'
    # Times New Roman rather than Symbol. The bullet character here is
    # U+2022, which Times New Roman has; in the Symbol font that codepoint is
    # not the bullet, so the marker printed as an empty box wherever the
    # Symbol font was not available.
    '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
    'w:cs="Times New Roman" w:hint="default"/>'
    '</w:rPr></w:lvl></w:abstractNum>')


def main():
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    with zipfile.ZipFile(TEMPLATE) as z:
        z.extractall(WORK)

    doc_path = os.path.join(WORK, "word", "document.xml")
    with io.open(doc_path, encoding="utf-8") as f:
        xml = f.read()

    # ---- cover page -----------------------------------------------------
    xml = xml.replace("<w:t>Project Title</w:t>",
                      "<w:t>%s</w:t>" % esc(C.TITLE))

    # The two cover cells each hold a heading paragraph followed by one
    # paragraph per member. The cells are rebuilt by position rather than by
    # searching for the placeholder text, because two members can legitimately
    # share a roll number string and a text search would then put a value in
    # the wrong row.
    def fill_cell(cell_xml, values):
        paragraphs = re.findall(r"<w:p[ >](?:(?!</w:p>).)*?</w:p>", cell_xml,
                                re.S)
        if len(paragraphs) < 2:
            raise SystemExit("cover cell has no row to clone")
        header, row_template = paragraphs[0], paragraphs[1]
        old_text = re.search(r"<w:t[^>]*>(.*?)</w:t>", row_template, re.S)
        rows = []
        for value in values:
            rows.append(row_template.replace(
                "<w:t>%s</w:t>" % old_text.group(1),
                "<w:t>%s</w:t>" % esc(value)))
        rebuilt = header + "".join(rows)
        first = cell_xml.find(paragraphs[0])
        last = cell_xml.find(paragraphs[-1]) + len(paragraphs[-1])
        return cell_xml[:first] + rebuilt + cell_xml[last:]

    table_match = re.search(r"<w:tbl>.*?</w:tbl>", xml, re.S)
    if not table_match:
        raise SystemExit("cover table not found")
    table_xml = table_match.group(0)
    cells = re.findall(r"<w:tc>.*?</w:tc>", table_xml, re.S)
    if len(cells) < 2:
        raise SystemExit("cover table does not have two cells")
    new_table = table_xml
    new_table = new_table.replace(
        cells[0], fill_cell(cells[0], [m[0] for m in C.MEMBERS]), 1)
    new_table = new_table.replace(
        cells[1], fill_cell(cells[1], [m[1] for m in C.MEMBERS]), 1)
    xml = xml.replace(table_xml, new_table, 1)

    # ---- body -----------------------------------------------------------
    rels = []
    media_dir = os.path.join(WORK, "word", "media")
    figure_counter = [0]

    implementation = list(C.IMPLEMENTATION_INTRO)
    for filename, caption in C.FIGURES:
        implementation.append(("fig", filename, caption))

    sections = [
        ("Abstract:", C.ABSTRACT),
        ("Introduction:", C.INTRODUCTION),
        ("Novelty in Project:", C.NOVELTY),
        ("Proposed Work:", C.PROPOSED),
        ("Implementation &amp; Screenshots:", implementation),
        ("Conclusion:", C.CONCLUSION),
        ("Project Repository Link: (GitHub or drive link)",
         [("p", C.REPO)]),
    ]

    for marker_text, blocks in sections:
        marker = "<w:t>%s</w:t>" % marker_text
        index = xml.find(marker)
        if index < 0:
            raise SystemExit("section heading not found: %r" % marker_text)
        end = xml.find("</w:p>", index) + len("</w:p>")
        body = render(blocks, rels, media_dir, figure_counter)
        xml = xml[:end] + body + xml[end:]

    # The template leaves an empty numbered paragraph under some headings,
    # which would print as a stray number now that real content follows.
    xml = re.sub(
        r'<w:p [^>]*>(?:(?!</w:p>).)*?<w:pStyle w:val="ListParagraph"/>'
        r'(?:(?!</w:p>).)*?</w:p>',
        lambda m: "" if "<w:t" not in m.group(0) else m.group(0), xml)

    with io.open(doc_path, "w", encoding="utf-8", newline="") as f:
        f.write(xml)

    # ---- relationships and numbering ------------------------------------
    rels_path = os.path.join(WORK, "word", "_rels", "document.xml.rels")
    with io.open(rels_path, encoding="utf-8") as f:
        rels_xml = f.read()
    rels_xml = rels_xml.replace("</Relationships>",
                                "".join(rels) + "</Relationships>")
    with io.open(rels_path, "w", encoding="utf-8", newline="") as f:
        f.write(rels_xml)

    num_path = os.path.join(WORK, "word", "numbering.xml")
    with io.open(num_path, encoding="utf-8") as f:
        num_xml = f.read()
    num_xml = num_xml.replace(
        "<w:num ", BULLET_DEFINITION + '<w:num w:numId="9"><w:abstractNumId '
        'w:val="9"/></w:num><w:num ', 1)
    with io.open(num_path, "w", encoding="utf-8", newline="") as f:
        f.write(num_xml)

    # ---- repackage -------------------------------------------------------
    if os.path.exists(TARGET):
        os.remove(TARGET)
    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(WORK):
            for name in files:
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, WORK).replace("\\", "/"))

    print("wrote", TARGET)
    print("figures:", figure_counter[0], " images embedded:", len(rels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
