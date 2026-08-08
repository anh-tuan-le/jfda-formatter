"""
Browser-side packer for JFDA.

Same job as app/packer/__init__.py — take the template .docx plus a manuscript
dict and produce a formatted .docx — but done entirely in memory with zipfile,
with no subprocess, no temp directories and no unpack/pack helper scripts.

Fidelity note: every part of the template we do not explicitly touch is copied
across byte-for-byte, so styles, numbering, fonts, headers and settings survive
exactly. Only word/document.xml, word/_rels/document.xml.rels,
[Content_Types].xml and any added word/media/* differ from the original.
"""

from __future__ import annotations

import io
import re
import zipfile

import xml.dom.minidom as _minidom

from packer.builder import build_body, manuscript_to_items
from packer.metadata import apply_metadata_str
from office.merge_runs import merge_runs_str
from office.simplify_redlines import simplify_redlines_str

DOC = "word/document.xml"
RELS = "word/_rels/document.xml.rels"
CONTENT_TYPES = "[Content_Types].xml"

IMAGE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
)
MIME_FOR = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "emf": "image/x-emf",
    "wmf": "image/x-wmf",
}


SMART_QUOTES = {
    "\u201c": "&#x201C;",
    "\u201d": "&#x201D;",
    "\u2018": "&#x2018;",
    "\u2019": "&#x2019;",
}


class PackerError(Exception):
    pass


def _prepare_document(xml_text: str) -> str:
    """Reproduce office/unpack.py's normalisation.

    The metadata placeholders in packer/metadata.py are written against the
    document.xml that comes out of that pipeline, not against the raw file in
    the template zip — pretty-print, simplify tracked changes, then merge
    adjacent runs with identical formatting, which is what turns a placeholder
    split across several <w:r> elements into one matchable <w:t>.
    """
    dom = _minidom.parseString(xml_text)
    xml_text = dom.toprettyxml(indent="  ")
    xml_text = simplify_redlines_str(xml_text)
    xml_text = merge_runs_str(xml_text)
    for ch, ent in SMART_QUOTES.items():
        xml_text = xml_text.replace(ch, ent)
    return xml_text


def _splice_body(doc: str, body_xml: str) -> str:
    """Replace everything between the metadata </w:tbl> and <w:sectPr>."""
    tbl_end = doc.find("</w:tbl>")
    if tbl_end < 0:
        raise PackerError("No </w:tbl> found in document.xml")
    splice_start = tbl_end + len("</w:tbl>")

    sect = doc.find("<w:sectPr")
    if sect < 0:
        raise PackerError("No <w:sectPr> found in document.xml")

    return doc[:splice_start] + "\n" + body_xml + "\n    " + doc[sect:]


def _strip_stale_hyperlink_rels(rels_text: str) -> str:
    """Drop relationships pointing at schemes Word rejects (chrome-extension://
    and friends ship inside the JFDA template)."""
    rel_pat = re.compile(r"\s*<Relationship\b[^>]*?/>", re.IGNORECASE | re.DOTALL)
    target_pat = re.compile(r'Target="([^"]+)"', re.IGNORECASE)

    def is_safe(t: str) -> bool:
        t = t.strip()
        if not t:
            return False
        if t.lower().startswith(("http://", "https://")):
            return True
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*:", t):
            return False
        return True

    def keep_or_drop(m: re.Match) -> str:
        elem = m.group(0)
        t = target_pat.search(elem)
        if t and not is_safe(t.group(1)):
            return ""
        return elem

    return rel_pat.sub(keep_or_drop, rels_text)


def _register_figures(figures, rels_text, ct_text):
    """Allocate rIds for figure images and return
    (media_files, rels_text, ct_text, rid_map)."""
    media = {}
    rid_map = {}
    if not figures:
        return media, rels_text, ct_text, rid_map

    used = {int(m.group(1)) for m in re.finditer(r'Id="rId(\d+)"', rels_text)}
    next_id = max(used, default=0) + 1

    new_rels = []
    seen_exts = set()

    for fig in figures:
        data = fig.get("data")
        if not data:
            continue
        ext = (fig.get("ext") or "png").lower().lstrip(".")
        if ext == "jpeg":
            ext = "jpg"
        name = "figure_%s.%s" % (fig["id"], ext)
        media["word/media/" + name] = bytes(data)
        seen_exts.add(ext)

        rid = "rId%d" % next_id
        next_id += 1
        new_rels.append(
            '  <Relationship Id="%s" Type="%s" Target="media/%s"/>'
            % (rid, IMAGE_REL_TYPE, name)
        )
        rid_map[fig["id"]] = {
            "rid": rid,
            "width_px": fig.get("width"),
            "height_px": fig.get("height"),
        }

    if new_rels:
        at = rels_text.rfind("</Relationships>")
        if at < 0:
            raise PackerError("No </Relationships> in document.xml.rels")
        rels_text = rels_text[:at] + "\n" + "\n".join(new_rels) + "\n" + rels_text[at:]

    new_defaults = [
        '<Default Extension="%s" ContentType="%s"/>' % (e, MIME_FOR.get(e, "image/png"))
        for e in sorted(seen_exts)
        if 'Extension="%s"' % e not in ct_text
    ]
    if new_defaults:
        at = ct_text.rfind("</Types>")
        ct_text = ct_text[:at] + "".join(new_defaults) + ct_text[at:]

    return media, rels_text, ct_text, rid_map


def _attach_rids(items, rid_map):
    doc_pr = 1000
    for item in items:
        if item.get("type") != "figure":
            continue
        info = rid_map.get(item.get("id"))
        if not info:
            continue
        item["rid"] = info["rid"]
        item["width_px"] = info["width_px"]
        item["height_px"] = info["height_px"]
        item["doc_pr_id"] = doc_pr
        item.setdefault("skip_caption", False)
        doc_pr += 1


def format_manuscript_bytes(template_bytes: bytes, manuscript: dict) -> bytes:
    """Return the formatted .docx as bytes."""
    if len(template_bytes) < 30_000:
        raise PackerError(
            "Template is only %d bytes — expected ~70KB. Wrong file?"
            % len(template_bytes)
        )

    src = zipfile.ZipFile(io.BytesIO(template_bytes))
    names = src.namelist()
    for required in (DOC, RELS, CONTENT_TYPES):
        if required not in names:
            raise PackerError("Template is missing %s" % required)

    doc_text = _prepare_document(src.read(DOC).decode("utf-8"))
    rels_text = _minidom.parseString(
        src.read(RELS).decode("utf-8")).toprettyxml(indent="  ")
    ct_text = src.read(CONTENT_TYPES).decode("utf-8")

    # 1. Metadata placeholders
    doc_text = apply_metadata_str(doc_text, manuscript)

    # 2. Figure images — before the body build, so items can carry rIds
    media, rels_text, ct_text, rid_map = _register_figures(
        manuscript.get("figures") or [], rels_text, ct_text
    )

    # 3. Body
    items = manuscript_to_items(manuscript)
    _attach_rids(items, rid_map)
    doc_text = _splice_body(doc_text, build_body(items))

    # 4. Stale template relationships Word would reject
    rels_text = _strip_stale_hyperlink_rels(rels_text)

    # 5. Rewrite the zip, copying everything we did not touch verbatim
    replaced = {
        DOC: doc_text.encode("utf-8"),
        RELS: rels_text.encode("utf-8"),
        CONTENT_TYPES: ct_text.encode("utf-8"),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename in replaced:
                dst.writestr(item, replaced[item.filename])
            else:
                dst.writestr(item, src.read(item.filename))
        for path, data in media.items():
            dst.writestr(path, data)
    src.close()
    return out.getvalue()
