"""
Browser entry point. Everything JavaScript calls goes through here.

The heavy lifting is done by the same modules the server uses — docx_parser,
parser, packer.builder, packer.metadata, citation_converter — imported
unchanged. This file only supplies the pieces that used to be shell commands
(pandoc, pdftotext, ImageMagick) and hands results back as plain dicts.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

import docx_parser
import parser as md_parser
from pack_browser import format_manuscript_bytes, PackerError

TMP = Path("/tmp/jfda")
TMP.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

def parse_docx(data: bytes) -> dict:
    """Parse a .docx from raw bytes using the server's direct XML parser."""
    path = TMP / "in.docx"
    path.write_bytes(bytes(data))
    try:
        return docx_parser.parse_docx_direct(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def parse_text(text: str) -> dict:
    """Markdown, plain text, or PDF text already extracted by pdf.js."""
    return md_parser.parse_markdown(text)


def parse_json(text: str) -> dict:
    """A manuscript dict that was exported earlier."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON must be a manuscript object, not a list")
    return data


_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(
    r"</(p|div|h[1-6]|li|tr|blockquote|section|article)\s*>", re.IGNORECASE
)
_HEAD = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)


def parse_html(text: str) -> dict:
    """Flatten HTML to markdown-ish text, then reuse the markdown parser."""
    text = re.sub(r"<(script|style)\b.*?</\1>", "", text,
                  flags=re.IGNORECASE | re.DOTALL)
    # Headings become ATX so the markdown parser sees the structure
    text = _HEAD.sub(lambda m: "\n\n" + "#" * int(m.group(1)) + " "
                     + _TAG.sub("", m.group(2)).strip() + "\n\n", text)
    text = re.sub(r"<(b|strong)\b[^>]*>(.*?)</\1>", r"**\2**", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<(i|em)\b[^>]*>(.*?)</\1>", r"*\2*", text,
                  flags=re.IGNORECASE | re.DOTALL)
    text = _BLOCK_END.sub("\n\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub("", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return md_parser.parse_markdown(text.strip())


_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def parse_pptx(data: bytes) -> dict:
    """Slide text in slide order, flattened to markdown.

    Each slide's first line becomes a heading, the rest body text — enough
    structure for supplementary material to land in the template.
    """
    from xml.etree import ElementTree as ET

    lines = []
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
        slides = sorted(
            (n for n in z.namelist()
             if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)),
        )
        for name in slides:
            root = ET.fromstring(z.read(name))
            texts = [(t.text or "").strip()
                     for t in root.iter("{%s}t" % _A_NS)]
            texts = [t for t in texts if t]
            if not texts:
                continue
            lines.append("## " + texts[0])
            lines.extend(texts[1:])
            lines.append("")
    return md_parser.parse_markdown("\n\n".join(lines))


def extract_docx_images(data: bytes) -> list:
    """Every image embedded in a .docx, in document order where we can tell.

    Returns [{name, ext, data}] — the caller sizes them with an Image element,
    which is what ImageMagick's `identify` used to do server-side.
    """
    out = []
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
        names = [n for n in z.namelist()
                 if n.startswith("word/media/")
                 and re.search(r"\.(png|jpe?g|gif|bmp|tiff?|emf|wmf)$", n, re.I)]
        names.sort(key=lambda n: (len(n), n))
        for n in names:
            ext = n.rsplit(".", 1)[-1].lower()
            out.append({"name": n.rsplit("/", 1)[-1], "ext": ext,
                        "data": z.read(n)})
    return out


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def build_docx(template_data: bytes, manuscript_json: str) -> bytes:
    """The formatted .docx, as bytes, ready for download."""
    manuscript = json.loads(manuscript_json)
    figures = manuscript.get("figures") or []
    for f in figures:
        d = f.get("data")
        if isinstance(d, str):          # base64 from JS
            import base64
            f["data"] = base64.b64decode(d)
    return format_manuscript_bytes(bytes(template_data), manuscript)


def build_preview_html(manuscript_json: str) -> str:
    """A styled approximation of the formatted paper, for the preview pane.

    Deliberately cheap: it reuses the same item stream the .docx builder
    consumes, so what you see follows the same ordering and numbering rules,
    but it is HTML, not a Word render.
    """
    from packer.builder import manuscript_to_items

    m = json.loads(manuscript_json)
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))

    def rich(s):
        s = esc(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", s)
        return s

    out = []
    title = m.get("title") or "(untitled)"
    out.append('<h1 class="t">%s</h1>' % esc(title))

    authors = m.get("authors") or []
    if authors:
        out.append('<p class="au">%s</p>'
                   % esc(", ".join(a.get("name", "") for a in authors if a))) 

    if m.get("abstract"):
        out.append('<h2 class="hh">Abstract</h2><p class="ab">%s</p>'
                   % rich(m["abstract"]))
    if m.get("keywords"):
        out.append('<p class="kw"><strong>Keywords:</strong> %s</p>'
                   % esc("; ".join(m["keywords"])))
    if m.get("jel_codes"):
        out.append('<p class="kw"><strong>JEL:</strong> %s</p>'
                   % esc(", ".join(m["jel_codes"])))

    for item in manuscript_to_items(m):
        k = item.get("type")
        if k == "heading":
            out.append("<h2 class=\"hh\">%s</h2>" % rich(item.get("text", "")))
        elif k == "subheading":
            out.append("<h3 class=\"sh\">%s</h3>" % rich(item.get("text", "")))
        elif k == "paragraph":
            out.append("<p>%s</p>" % rich(item.get("text", "")))
        elif k == "reference":
            out.append('<p class="ref">%s</p>' % rich(item.get("text", "")))
        elif k == "list":
            out.append("<ul>%s</ul>" % "".join(
                "<li>%s</li>" % rich(li) for li in item.get("items", [])))
        elif k == "table":
            head = "".join("<th>%s</th>" % rich(h)
                           for h in item.get("headers", []))
            body = "".join(
                "<tr>%s</tr>" % "".join("<td>%s</td>" % rich(c) for c in row)
                for row in item.get("rows", []))
            cap = item.get("caption") or item.get("title") or ""
            out.append('<div class="tw">%s<table><thead><tr>%s</tr></thead>'
                       "<tbody>%s</tbody></table></div>"
                       % ('<p class="cap">%s</p>' % rich(cap) if cap else "",
                          head, body))
        elif k == "figure":
            cap = item.get("caption") or item.get("label") or "Figure"
            out.append('<div class="fig"><div class="figbox">%s</div>'
                       '<p class="cap">%s</p></div>'
                       % (esc(item.get("label") or "Figure"), rich(cap)))
        elif k == "equation":
            out.append('<p class="eq">%s</p>' % rich(item.get("text", "")))
    return "".join(out)
