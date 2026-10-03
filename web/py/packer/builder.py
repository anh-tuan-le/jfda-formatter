"""
JFDA body XML builder.

Takes a parsed manuscript (dict) and produces the XML block that sits
between the metadata table and <w:sectPr> in document.xml.

FORMATTING SPEC — derived from the JFDA author template v2 and the
published/formatted manuscripts (measured from word/document.xml, not
guessed):

    page          A4 11906x16838, margins top/bottom 720, left 1418, right 851
    body text     Times New Roman 11pt (sz 22), line 300 atLeast,
                  first-line indent 720
    heading        Times New Roman 12pt bold (sz 24), line 300 atLeast
    subheading     Times New Roman 11pt bold (sz 22)
    tables         horizontal rules only (APA/booktabs): rule above the
                   header row, rule below the header row, rule below the
                   last row; no vertical or inside-vertical rules;
                   autofit width; cells 11pt, left aligned, cell margins
                   top 28 / right 115
    table caption  above the table, 11pt, "Table N." bold + title plain
    figure caption below the image, 11pt, "Figure N." bold + caption plain
    equations      borderless two-column table: equation centred in the
                   wide cell, "(n)" right-aligned in the narrow cell
    references     11pt, no indent, line 240 atLeast (EndNote Bibliography)

Schema (all fields optional except where noted):
    {
        "sections": [
            {"heading": "1. Introduction", "paragraphs": ["para 1", "para 2"]},
            {"heading": "2. Literature Review", "subsections": [
                {"heading": "2.1 Sub", "paragraphs": [...]}
            ]},
            ...
        ],
        "back_matter": {...},
        "references": ["ref 1", "ref 2", ...],
    }
"""

from __future__ import annotations

import re
from typing import Iterable

# Times New Roman everywhere
FONT = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'

# Body content width for a JFDA A4 page: 11906 - 1418 (left) - 851 (right) = 9637 DXA
TABLE_WIDTH = 9637

# Rule weight used by the template's tables (w:sz="3" ≈ 0.25pt hairline)
RULE = '<w:top w:val="single" w:sz="3" w:space="0" w:color="000000"/>'
RULE_SZ = 3


def _esc(text: str) -> str:
    """Escape text for XML <w:t>, preserving typographic entities."""
    if text is None:
        return ""
    t = str(text)
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = t.replace("\u2019", "&#x2019;")
    t = t.replace("\u2018", "&#x2018;")
    t = t.replace("\u201C", "&#x201C;")
    t = t.replace("\u201D", "&#x201D;")
    t = t.replace("\u2013", "&#x2013;")
    t = t.replace("\u2014", "&#x2014;")
    t = t.replace("\u2212", "&#x2212;")
    t = t.replace("\u2026", "&#x2026;")
    return t


def _needs_preserve(text: str) -> bool:
    return bool(text) and (text[0] == " " or text[-1] == " ")


def _run(text: str, *, bold: bool = False, italic: bool = False, size: str = "22") -> str:
    """A single <w:r> in Times New Roman at given half-point size."""
    t = _esc(text)
    attr = ' xml:space="preserve"' if _needs_preserve(text) else ""
    rpr_parts = [FONT, f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>']
    if bold:
        rpr_parts.insert(1, "<w:b/><w:bCs/>")
    if italic:
        rpr_parts.insert(1, "<w:i/><w:iCs/>")
    return f'<w:r><w:rPr>{"".join(rpr_parts)}</w:rPr><w:t{attr}>{t}</w:t></w:r>'


_MD_TOKEN = re.compile(
    r'(\*\*.+?\*\*'
    r'|(?<!\*)\*(?!\*).+?(?<!\*)\*(?!\*)'
    r'|(?<!_)_(?!_).+?(?<!_)_(?!_))'
)


def _rich_runs(text: str, *, size: str = "22") -> str:
    """Split *italic* / **bold** / _italic_ markers into separate <w:r> runs.

    A Word run carries one formatting state, so part-italic text needs one run
    per segment. Used by references, where APA 7 italicises the journal name
    and the volume number.
    """
    out = []
    for part in _MD_TOKEN.split(str(text)):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append(_run(part[2:-2], bold=True, size=size))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append(_run(part[1:-1], italic=True, size=size))
        elif part.startswith("_") and part.endswith("_") and len(part) > 2:
            out.append(_run(part[1:-1], italic=True, size=size))
        else:
            out.append(_run(part, size=size))
    return "".join(out)


def _heading(text: str) -> str:
    """12pt bold section heading with 300-line spacing."""
    return (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        '<w:rPr>' + FONT + '<w:b/><w:bCs/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr>'
        '</w:pPr>'
        + _run(text, bold=True, size="24")
        + '</w:p>'
    )


def _subheading(text: str) -> str:
    """11pt bold subsection heading."""
    return (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        '<w:rPr>' + FONT + '<w:b/><w:bCs/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>'
        + _run(text, bold=True, size="22")
        + '</w:p>'
    )


def _paragraph(text: str, *, indent: bool = True, bold: bool = False, italic: bool = False,
               line: str = "300", align: str | None = None, rich: bool = False) -> str:
    """11pt body paragraph with optional 720-DXA first-line indent.

    rich=True parses inline *italic* / **bold** markers into separate runs.
    """
    ind = '<w:ind w:firstLine="720"/>' if indent else ""
    jc = f'<w:jc w:val="{align}"/>' if align else ""
    body = (_rich_runs(text, size="22") if rich
            else _run(text, bold=bold, italic=italic, size="22"))
    return (
        '<w:p><w:pPr>'
        f'<w:spacing w:line="{line}" w:lineRule="atLeast"/>'
        f'{jc}{ind}'
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>'
        + body
        + '</w:p>'
    )


def _spacer() -> str:
    """Empty body-sized paragraph between sections."""
    return (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr></w:p>'
    )


def _inline_bold_body(segments: list, *, indent: bool = False) -> str:
    """Body paragraph with mixed bold/plain inline runs."""
    ind = '<w:ind w:firstLine="720"/>' if indent else ""
    runs = "".join(_run(t, bold=b, size="22") for t, b in segments if t)
    return (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        f'{ind}'
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>'
        + runs
        + '</w:p>'
    )


def _list_item(text: str, *, marker: str = "\u2022") -> str:
    """Template-style list line: marker + tab at a 720 hanging indent."""
    return (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        '<w:ind w:left="720" w:hanging="360"/>'
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>'
        + _run(f"{marker}\t{text}", size="22")
        + '</w:p>'
    )


# ---------------------------------------------------------------------------
# Tables — JFDA convention: horizontal rules only, no vertical lines
# ---------------------------------------------------------------------------

def _cell_paragraph(text: str, *, bold: bool = False, italic: bool = False,
                    center: bool = False) -> str:
    """11pt table-cell paragraph (matches the template's cell text)."""
    jc = f'<w:jc w:val="{"center" if center else "left"}"/>'
    return (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>' + jc +
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>'
        + _run(text, bold=bold, italic=italic, size="22")
        + '</w:p>'
    )


def _tc_borders(*, top: bool = False, bottom: bool = False) -> str:
    """Cell borders: horizontal rules only — verticals are always nil."""
    t = (f'<w:top w:val="single" w:sz="{RULE_SZ}" w:space="0" w:color="000000"/>'
         if top else '<w:top w:val="nil"/>')
    b = (f'<w:bottom w:val="single" w:sz="{RULE_SZ}" w:space="0" w:color="000000"/>'
         if bottom else '<w:bottom w:val="nil"/>')
    return '<w:tcBorders>' + t + '<w:left w:val="nil"/>' + b + '<w:right w:val="nil"/></w:tcBorders>'


def _table_cell(inner: str, *, width: int | None = None,
                top: bool = False, bottom: bool = False, borders: bool = True) -> str:
    w = f'<w:tcW w:w="{width}" w:type="dxa"/>' if width else '<w:tcW w:w="0" w:type="auto"/>'
    bd = _tc_borders(top=top, bottom=bottom) if borders else ""
    return f'<w:tc><w:tcPr>{w}{bd}</w:tcPr>{inner}</w:tc>'


def _make_table(headers: list, rows: list, *, center_numeric: bool = False) -> str:
    """Data table in the JFDA/APA style measured from the template:

    - a rule above the header row and below the header row
    - a rule below the final data row
    - no vertical rules, no inside-vertical rules
    - autofit width, cells 11pt Times New Roman, left aligned
    """
    n = max(1, len(headers))
    col_w = TABLE_WIDTH // n
    widths = [col_w] * (n - 1) + [TABLE_WIDTH - col_w * (n - 1)]

    tbl_pr = (
        '<w:tblPr>'
        '<w:tblW w:w="0" w:type="auto"/>'
        '<w:tblInd w:w="0" w:type="dxa"/>'
        '<w:tblLayout w:type="autofit"/>'
        '<w:tblCellMar><w:top w:w="28" w:type="dxa"/><w:right w:w="115" w:type="dxa"/></w:tblCellMar>'
        '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
        '</w:tblPr>'
    )
    grid = '<w:tblGrid>' + ''.join(f'<w:gridCol w:w="{w}"/>' for w in widths) + '</w:tblGrid>'

    header_row = '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + ''.join(
        _table_cell(_cell_paragraph(h), width=None, top=True, bottom=True)
        for h in headers
    ) + '</w:tr>'

    numeric_re = re.compile(r'^[\-\u2212]?[\d\.,%\u2013\u2014\s\+]+$')
    data_rows = []
    last = len(rows) - 1
    for r_i, row in enumerate(rows):
        cells = []
        for val in row:
            s = str(val)
            is_num = bool(numeric_re.match(s.strip())) and s.strip() != ""
            cells.append(_table_cell(
                _cell_paragraph(s, center=center_numeric and is_num),
                width=None,
                bottom=(r_i == last),
            ))
        data_rows.append('<w:tr>' + ''.join(cells) + '</w:tr>')

    return '<w:tbl>' + tbl_pr + grid + header_row + ''.join(data_rows) + '</w:tbl>'


def _figure_placeholder(label: str, caption: str, source: str = "") -> str:
    """Bracketed 'insert figure' placeholder + bold caption + italic source line."""
    out = [_inline_bold_body([(f"[{label} \u2014 Insert figure here]", False)], indent=False)]
    combined = f"{label.rstrip('.')}. {caption}".strip()
    m = re.match(r'^(Figure \d+[a-z]?\.)\s*(.*)$', combined)
    if m:
        out.append(_inline_bold_body(
            [(m.group(1), True), (" ", False), (m.group(2), False)],
            indent=False,
        ))
    elif caption:
        out.append(_inline_bold_body(
            [(label or "Figure", True), (" ", False), (caption, False)],
            indent=False,
        ))
    if source:
        out.append(_paragraph(source, indent=False, italic=True))
    return "".join(out)


# Body column width in EMUs: 9637 DXA × (914400 / 1440) = 6 127 562 EMU ≈ 6.66"
DEFAULT_FIG_WIDTH_EMU = 6_127_562


def _figure_image(item: dict) -> str:
    """
    Emit a centred paragraph containing an inline <w:drawing>, plus the
    bold caption and italic source paragraphs.

    Required fields on `item`:
        rid (str)  — relationship ID (e.g. 'rId100') already registered
        width_px (int), height_px (int) — original pixel dims (optional)
    """
    rid = item["rid"]
    label = item.get("label", "")
    caption = item.get("caption", "")
    source = item.get("source", "")
    alt = f"{label} {caption}".strip() or "Figure"

    w_emu = DEFAULT_FIG_WIDTH_EMU
    w_px = item.get("width_px")
    h_px = item.get("height_px")
    if w_px and h_px and w_px > 0:
        ratio = h_px / w_px
        h_emu = int(w_emu * ratio)
    else:
        h_emu = int(w_emu * 2 / 3)

    doc_pr_id = item.get("doc_pr_id", 1000)
    drawing = (
        '<w:drawing>'
          '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{w_emu}" cy="{h_emu}"/>'
            '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{doc_pr_id}" name="{_xml_attr(label or "Figure")}" descr="{_xml_attr(alt)}"/>'
            '<wp:cNvGraphicFramePr>'
              '<a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>'
            '</wp:cNvGraphicFramePr>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
              '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                  '<pic:nvPicPr>'
                    f'<pic:cNvPr id="{doc_pr_id}" name="{_xml_attr(label or "Figure")}"/>'
                    '<pic:cNvPicPr/>'
                  '</pic:nvPicPr>'
                  '<pic:blipFill>'
                    f'<a:blip r:embed="{rid}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/>'
                    '<a:stretch><a:fillRect/></a:stretch>'
                  '</pic:blipFill>'
                  '<pic:spPr>'
                    '<a:xfrm>'
                      '<a:off x="0" y="0"/>'
                      f'<a:ext cx="{w_emu}" cy="{h_emu}"/>'
                    '</a:xfrm>'
                    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                  '</pic:spPr>'
                '</pic:pic>'
              '</a:graphicData>'
            '</a:graphic>'
          '</wp:inline>'
        '</w:drawing>'
    )

    image_para = (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        '<w:jc w:val="center"/>'
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>'
        '<w:r><w:rPr>' + FONT + '<w:noProof/></w:rPr>'
        + drawing +
        '</w:r></w:p>'
    )

    parts = [image_para]
    if not item.get("skip_caption"):
        if caption or label:
            m = re.match(r'^(Figure \d+[a-z]?\.)\s*(.*)$', f"{label.rstrip('.')}. {caption}".strip())
            if m:
                parts.append(_inline_bold_body(
                    [(m.group(1), True), (" ", False), (m.group(2), False)],
                    indent=False,
                ))
            else:
                parts.append(_inline_bold_body([(label or "Figure", True), (" ", False), (caption, False)], indent=False))
        if source:
            parts.append(_paragraph(source, indent=False, italic=True))
    return "".join(parts)


def _xml_attr(text: str) -> str:
    """Escape for an XML attribute value."""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _table_caption(label: str, title: str) -> str:
    return _inline_bold_body([(label, True), (" ", False), (title, False)], indent=False)


def _table_source(text: str) -> str:
    return _paragraph(text, indent=False, italic=True)


def _equation(item: dict) -> str:
    """Render an equation the way the template does: a borderless two-column
    table with the equation centred on the left and "(n)" right-aligned on
    the right. Falls back to a single centred paragraph when the item has no
    number.

    If `raw_math_xml` is present it is injected so Word renders a real
    equation; otherwise the text is set italic.
    """
    raw = item.get("raw_math_xml")
    text = item.get("text", "")
    number = item.get("number")

    if raw:
        body = raw
    else:
        body = _run(text, italic=True, size="22")

    eq_para = (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        '<w:jc w:val="center"/>'
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>' + body + '</w:p>'
    )

    if not number:
        return eq_para

    num_text = str(number)
    if not num_text.startswith("("):
        num_text = f"({num_text})"

    num_para = (
        '<w:p><w:pPr>'
        '<w:spacing w:line="300" w:lineRule="atLeast"/>'
        '<w:jc w:val="right"/>'
        '<w:rPr>' + FONT + '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:pPr>' + _run(num_text, size="22") + '</w:p>'
    )

    eq_w = int(TABLE_WIDTH * 0.88)
    num_w = TABLE_WIDTH - eq_w
    tbl_pr = (
        '<w:tblPr>'
        f'<w:tblW w:w="{TABLE_WIDTH}" w:type="dxa"/>'
        '<w:tblInd w:w="0" w:type="dxa"/>'
        '<w:tblBorders>'
        '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
        '</w:tblBorders>'
        '<w:tblLook w:val="04A0" w:firstRow="0" w:lastRow="0" w:firstColumn="0" w:lastColumn="0" w:noHBand="1" w:noVBand="1"/>'
        '</w:tblPr>'
    )
    grid = f'<w:tblGrid><w:gridCol w:w="{eq_w}"/><w:gridCol w:w="{num_w}"/></w:tblGrid>'
    row = ('<w:tr>'
           + _table_cell(eq_para, width=eq_w, borders=False)
           + _table_cell(num_para, width=num_w, borders=False)
           + '</w:tr>')
    return '<w:tbl>' + tbl_pr + grid + row + '</w:tbl>'


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def render_paragraph(item: dict) -> str:
    """Render one body item based on its `type`.

    Supported item shapes:
      {"type": "heading", "text": "1. Introduction"}
      {"type": "subheading", "text": "2.1 Sub"}
      {"type": "paragraph", "text": "...", "indent": true, "bold": false, "italic": false}
      {"type": "bold_paragraph", "text": "Heading-like inline bold"}
      {"type": "list", "items": ["a", "b"], "ordered": false}
      {"type": "spacer"}
      {"type": "table", "caption_label": "Table 1.", "caption_title": "...",
       "headers": [...], "rows": [[...], ...], "source": "..."}
      {"type": "figure", "label": "Figure 1", "caption": "...", "source": "..."}
      {"type": "equation", "text": "a+b=c", "number": 1, "raw_math_xml": "..."}
      {"type": "reference", "text": "Author, A. (2020)..."}
    """
    kind = item.get("type")
    if kind == "heading":
        return _heading(item["text"])
    if kind == "subheading":
        return _subheading(item["text"])
    if kind == "paragraph":
        return _paragraph(
            item["text"],
            indent=item.get("indent", True),
            bold=item.get("bold", False),
            italic=item.get("italic", False),
        )
    if kind == "bold_paragraph":
        return _paragraph(item["text"], indent=False, bold=True)
    if kind == "list":
        ordered = item.get("ordered", False)
        out = []
        for i, li in enumerate(item.get("items", []), start=1):
            out.append(_list_item(li, marker=f"{i}." if ordered else "\u2022"))
        return "".join(out)
    if kind == "reference":
        # References use the template's tighter 240 line spacing, flush left.
        # rich=True so APA italics (*Journal*, *Volume*) become real italics.
        return _paragraph(item["text"], indent=False, line="240", rich=True)
    if kind == "spacer":
        return _spacer()
    if kind == "table":
        parts = []
        if item.get("caption_label") or item.get("caption_title"):
            parts.append(_table_caption(item.get("caption_label", ""), item.get("caption_title", "")))
        parts.append(_make_table(item["headers"], item["rows"],
                                 center_numeric=item.get("center_numeric", False)))
        if item.get("source"):
            parts.append(_table_source(item["source"]))
        return "".join(parts)
    if kind == "figure":
        if item.get("rid"):
            return _figure_image(item)
        return _figure_placeholder(
            item.get("label", "Figure"),
            item.get("caption", ""),
            item.get("source", ""),
        )
    if kind == "equation":
        return _equation(item)
    raise ValueError(f"Unknown item type: {kind!r}")


def build_body(items: Iterable[dict]) -> str:
    """Render a sequence of content items into the body XML string."""
    return "".join(render_paragraph(it) for it in items)


def manuscript_to_items(manuscript: dict) -> list:
    """Convert a high-level manuscript dict into a flat item list.

    The manuscript dict uses the convenient nested shape; this flattens
    it into the linear item stream that `build_body` wants.
    """
    items: list = []

    def spacer():
        items.append({"type": "spacer"})

    def block(blk):
        """Paragraphs with each table/figure/equation back in its original
        position (`_at` = number of paragraphs that preceded it)."""
        paras = blk.get("paragraphs", [])
        content = blk.get("content", [])
        for n, p in enumerate(paras):
            items.extend(c for c in content if c.get("_at") == n)
            items.append({"type": "paragraph", "text": p, "indent": True})
        items.extend(c for c in content
                     if not isinstance(c.get("_at"), int) or c["_at"] >= len(paras))

    # Sections and subsections
    def section(sec):
        items.append({"type": "heading", "text": sec["heading"]})
        spacer()
        block(sec)
        for sub in sec.get("subsections", []):
            spacer()
            items.append({"type": "subheading", "text": sub["heading"]})
            block(sub)
            for subsub in sub.get("subsections", []):
                spacer()
                items.append({"type": "subheading", "text": subsub["heading"]})
                block(subsub)
        spacer()

    secs = manuscript.get("sections", [])
    for sec in secs:
        if not sec.get("appendix"):
            section(sec)

    # Back matter
    back = manuscript.get("back_matter") or {}
    back_order = [
        ("data_availability", "Data Availability Statement"),
        ("funding", "Funding"),
        ("acknowledgements", "Acknowledgements"),
        ("gen_ai", "Declaration of the Use of Generative AI"),
        ("conflicts", "Conflicts of Interest"),
    ]
    for key, heading in back_order:
        if back.get(key):
            items.append({"type": "heading", "text": heading})
            spacer()
            items.append({"type": "paragraph", "text": back[key], "indent": False})
            spacer()

    # References — template style: flush left, 240 line spacing
    refs = manuscript.get("references") or []
    if refs:
        items.append({"type": "heading", "text": "References"})
        spacer()
        for r in refs:
            items.append({"type": "reference", "text": r})

    # Appendices go after the references, as in the submission
    apps = [s for s in secs if s.get("appendix")]
    if apps:
        spacer()
        for sec in apps:
            section(sec)

    return items
