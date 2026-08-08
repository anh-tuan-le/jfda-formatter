"""
Apply manuscript metadata to the JFDA template's document.xml in-place.

Each replacement targets a placeholder string that exists exactly once
in the template (using `w14:paraId` context to disambiguate author blocks).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def _esc(text: str) -> str:
    """Escape text for XML text content. Preserves smart quotes as entities."""
    if text is None:
        return ""
    t = str(text)
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Also normalise straight quotes to smart for the metadata area
    t = t.replace("\u2019", "&#x2019;")
    t = t.replace("\u2018", "&#x2018;")
    t = t.replace("\u201C", "&#x201C;")
    t = t.replace("\u201D", "&#x201D;")
    t = t.replace("\u2013", "&#x2013;")
    t = t.replace("\u2014", "&#x2014;")
    t = t.replace("\u2212", "&#x2212;")
    return t


def _replace(doc: str, old: str, new: str, *, required: bool = True) -> str:
    if old not in doc:
        if required:
            raise ValueError(f"Placeholder not found in template: {old[:80]!r}")
        return doc
    if doc.count(old) > 1:
        raise ValueError(f"Placeholder not unique in template (found {doc.count(old)}x): {old[:80]!r}")
    return doc.replace(old, new, 1)


def apply_metadata(doc_xml_path: Path, manuscript: dict) -> None:
    """Path-based wrapper kept for the server build."""
    doc_xml_path.write_text(
        apply_metadata_str(doc_xml_path.read_text(encoding="utf-8"), manuscript),
        encoding="utf-8",
    )


def apply_metadata_str(doc: str, manuscript: dict) -> str:
    """Apply every metadata placeholder edit and return the new document.xml."""

    # ---- Title area ----
    mtype = manuscript.get("manuscript_type") or "Research Article"
    doc = _replace(
        doc,
        '<w:t xml:space="preserve"> Manuscript Type </w:t>',
        f'<w:t>{_esc(mtype)}</w:t>',
    )
    doc = _replace(
        doc,
        '<w:t>(Choose the correct type on JFDA webpage)</w:t>',
        '<w:t></w:t>',
    )
    doc = _replace(
        doc,
        '<w:t>Titles</w:t>',
        f'<w:t>{_esc(manuscript.get("title", ""))}</w:t>',
    )

    # Authors line: "First Last, First Last, and First Last*"
    author_line = _format_author_line(manuscript.get("authors") or [])
    doc = _replace(
        doc,
        '<w:t xml:space="preserve">First name Last name, First name Last name, and First name Last name* </w:t>',
        f'<w:t xml:space="preserve">{_esc(author_line)} </w:t>',
    )

    # ---- Author affiliation blocks (3 slots) ----
    # The template has exactly 3 author blocks. paraIds identify them.
    authors = list(manuscript.get("authors") or [])
    while len(authors) < 3:
        authors.append(None)  # pad to 3 for blanking
    authors = authors[:3]

    slot_paraids = [
        # (position_paraId, country_paraId, email_paraId)
        ("0F69CD01", "7702F83A", "21D2D89C"),
        ("02B1FA2D", "040E75C5", "77D6F29B"),
        ("5BB3D9F3", "18D29920", "72F9AC20"),
    ]
    for i, (pos_id, country_id, email_id) in enumerate(slot_paraids):
        doc = _apply_author_slot(doc, authors[i], pos_id=pos_id,
                                 country_id=country_id, email_id=email_id)

    # ---- Abstract / Keywords / JEL ----
    abstract = manuscript.get("abstract", "")
    doc = _replace(
        doc,
        _ABSTRACT_PLACEHOLDER,
        f'<w:t>{_esc(abstract)}</w:t>',
    )

    keywords = manuscript.get("keywords") or []
    kw_text = " " + "; ".join(keywords) if keywords else " "
    doc = _replace(
        doc,
        '<w:t xml:space="preserve"> keyword 1; keyword 2; keyword 3 (List no more than five keywords specific to the article)</w:t>',
        f'<w:t xml:space="preserve">{_esc(kw_text)}</w:t>',
    )

    jel = manuscript.get("jel_codes") or []
    jel_text = " " + ", ".join(jel) if jel else " "
    doc = _replace(
        doc,
        '<w:t xml:space="preserve"> Code 1, Code 2, Code 3 (List no more than 3 JEL codes)</w:t>',
        f'<w:t xml:space="preserve">{_esc(jel_text)}</w:t>',
    )
    return doc

def _format_author_line(authors: Iterable[dict]) -> str:
    names = []
    for a in authors:
        if not a:
            continue
        n = a.get("name", "").strip()
        if a.get("corresponding"):
            n += "*"
        names.append(n)
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _apply_author_slot(doc: str, author: dict | None, *,
                       pos_id: str, country_id: str, email_id: str) -> str:
    """Fill (or blank) one author block using its paraId context."""
    if author:
        pos_org = f"{author.get('name', '')} is {author.get('position', '')}, {author.get('organisation', '')}, "
        country = f"{author.get('country', '')}."
        email_text = f"Email: {author.get('email', '')}"
    else:
        pos_org, country, email_text = "", "", ""

    # Position/organisation run (has xml:space="preserve")
    doc = _replace(
        doc,
        _author_pos_placeholder(pos_id),
        _author_pos_replacement(pos_id, pos_org),
    )
    # Country run
    doc = _replace(
        doc,
        _author_country_placeholder(country_id),
        _author_country_replacement(country_id, country),
    )
    # Email run
    doc = _replace(
        doc,
        _author_email_placeholder(email_id),
        _author_email_replacement(email_id, email_text),
    )
    return doc


# Exact placeholder templates (large enough to be unique via paraId)

def _author_pos_placeholder(para_id: str) -> str:
    if para_id == "0F69CD01":
        attrs = 'w14:paraId="0F69CD01" w14:textId="2404B6DD" w:rsidR="004F0536" w:rsidRPr="005F3424" w:rsidRDefault="003D35E0" w:rsidP="00C87C70"'
    elif para_id == "02B1FA2D":
        attrs = 'w14:paraId="02B1FA2D" w14:textId="25C9AF82" w:rsidR="004F0536" w:rsidRPr="005F3424" w:rsidRDefault="003D35E0" w:rsidP="00C87C70"'
    elif para_id == "5BB3D9F3":
        attrs = 'w14:paraId="5BB3D9F3" w14:textId="5F65061F" w:rsidR="004F0536" w:rsidRPr="005F3424" w:rsidRDefault="003D35E0" w:rsidP="00C87C70"'
    else:
        raise ValueError(para_id)
    return (
        f'          <w:p {attrs}>\n'
        '            <w:pPr>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '            </w:pPr>\n'
        '            <w:r>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '              <w:t xml:space="preserve">AAA AAA is Position, Organisation, </w:t>\n'
        '            </w:r>'
    )


def _author_pos_replacement(para_id: str, text: str) -> str:
    if para_id == "0F69CD01":
        attrs = 'w14:paraId="0F69CD01" w14:textId="2404B6DD" w:rsidR="004F0536" w:rsidRPr="005F3424" w:rsidRDefault="003D35E0" w:rsidP="00C87C70"'
    elif para_id == "02B1FA2D":
        attrs = 'w14:paraId="02B1FA2D" w14:textId="25C9AF82" w:rsidR="004F0536" w:rsidRPr="005F3424" w:rsidRDefault="003D35E0" w:rsidP="00C87C70"'
    elif para_id == "5BB3D9F3":
        attrs = 'w14:paraId="5BB3D9F3" w14:textId="5F65061F" w:rsidR="004F0536" w:rsidRPr="005F3424" w:rsidRDefault="003D35E0" w:rsidP="00C87C70"'
    else:
        raise ValueError(para_id)
    return (
        f'          <w:p {attrs}>\n'
        '            <w:pPr>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '            </w:pPr>\n'
        '            <w:r>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        f'              <w:t xml:space="preserve">{_esc(text)}</w:t>\n'
        '            </w:r>'
    )


def _author_country_placeholder(para_id: str) -> str:
    if para_id == "7702F83A":
        attrs = 'w14:paraId="7702F83A" w14:textId="03D6F912" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C02946" w:rsidP="00C87C70"'
    elif para_id == "040E75C5":
        attrs = 'w14:paraId="040E75C5" w14:textId="01274D2F" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C02946" w:rsidP="00C87C70"'
    elif para_id == "18D29920":
        attrs = 'w14:paraId="18D29920" w14:textId="72B5DDE0" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C02946" w:rsidP="00C87C70"'
    else:
        raise ValueError(para_id)
    return (
        f'          <w:p {attrs}>\n'
        '            <w:pPr>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '            </w:pPr>\n'
        '            <w:r>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '              <w:t>Country.</w:t>\n'
        '            </w:r>'
    )


def _author_country_replacement(para_id: str, text: str) -> str:
    if para_id == "7702F83A":
        attrs = 'w14:paraId="7702F83A" w14:textId="03D6F912" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C02946" w:rsidP="00C87C70"'
    elif para_id == "040E75C5":
        attrs = 'w14:paraId="040E75C5" w14:textId="01274D2F" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C02946" w:rsidP="00C87C70"'
    elif para_id == "18D29920":
        attrs = 'w14:paraId="18D29920" w14:textId="72B5DDE0" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C02946" w:rsidP="00C87C70"'
    else:
        raise ValueError(para_id)
    return (
        f'          <w:p {attrs}>\n'
        '            <w:pPr>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '            </w:pPr>\n'
        '            <w:r>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        f'              <w:t>{_esc(text)}</w:t>\n'
        '            </w:r>'
    )


def _author_email_placeholder(para_id: str) -> str:
    if para_id == "21D2D89C":
        attrs = 'w14:paraId="21D2D89C" w14:textId="77777777" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C87C70" w:rsidP="00C87C70"'
    elif para_id == "77D6F29B":
        attrs = 'w14:paraId="77D6F29B" w14:textId="77777777" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C87C70" w:rsidP="00C87C70"'
    elif para_id == "72F9AC20":
        attrs = 'w14:paraId="72F9AC20" w14:textId="77777777" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C87C70" w:rsidP="00C87C70"'
    else:
        raise ValueError(para_id)
    return (
        f'          <w:p {attrs}>\n'
        '            <w:pPr>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '            </w:pPr>\n'
        '            <w:r>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '              <w:t>Email: XXXX</w:t>\n'
        '            </w:r>\n'
        '          </w:p>'
    )


def _author_email_replacement(para_id: str, text: str) -> str:
    if para_id == "21D2D89C":
        attrs = 'w14:paraId="21D2D89C" w14:textId="77777777" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C87C70" w:rsidP="00C87C70"'
    elif para_id == "77D6F29B":
        attrs = 'w14:paraId="77D6F29B" w14:textId="77777777" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C87C70" w:rsidP="00C87C70"'
    elif para_id == "72F9AC20":
        attrs = 'w14:paraId="72F9AC20" w14:textId="77777777" w:rsidR="00C87C70" w:rsidRPr="005F3424" w:rsidRDefault="00C87C70" w:rsidP="00C87C70"'
    else:
        raise ValueError(para_id)
    return (
        f'          <w:p {attrs}>\n'
        '            <w:pPr>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        '            </w:pPr>\n'
        '            <w:r>\n'
        '              <w:rPr>\n'
        '                <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n'
        '                <w:sz w:val="16"/>\n'
        '                <w:szCs w:val="16"/>\n'
        '              </w:rPr>\n'
        f'              <w:t>{_esc(text)}</w:t>\n'
        '            </w:r>\n'
        '          </w:p>'
    )


_ABSTRACT_PLACEHOLDER = (
    '<w:t>The abstract should be written as a single paragraph and must not exceed 200 '
    'words. It should follow the logic of a structured abstract but without section '
    'headings. Begin by placing the research question in a broad context and clearly '
    'stating the purpose of the study. Then, briefly describe the main methods or '
    'treatments used, including any preregistration numbers, as well as species and '
    'strains if animals were involved. Next, summarize the key results of the study, '
    'highlighting the most important findings. Finally, present the main conclusions '
    'or interpretations, indicating the broader implications of the research. The '
    'abstract must provide an accurate and objective summary of the article. It '
    'should not include results that are not presented or supported in the main text, '
    'nor should it exaggerate the conclusions. The purpose, principal results, and '
    'major conclusions should be stated clearly and concisely. Abstracts must be '
    'able to stand alone, as they are often presented separately from the main '
    'article. References should generally be avoided; if necessary, cite only the '
    'author(s) and year(s). Non-standard or uncommon abbreviations should also be '
    'avoided, but if essential, they must be defined at first mention.</w:t>'
)
