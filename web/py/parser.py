"""
Parse an incoming manuscript file into the dict schema expected by
the packer.

Supported inputs:
  - .docx (via pandoc/extract-text -> markdown, then _parse_markdown)
  - .pdf  (via pdfminer.six)
  - .md / .txt (direct)

The parser is deliberately lenient: it extracts structural signals
(headings, paragraphs, tables, references), but the editor / reviewer
will always see the parsed result in the UI and can edit before export.
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path
from typing import Any


HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
NUMBERED_SECTION_RE = re.compile(r'^(\d+(?:\.\d+)*\.?)\s+(.+)$')


def parse_manuscript(path: str | Path) -> dict:
    """Parse any supported file type into a manuscript dict."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".docx":
        # Use the direct XML parser — no pandoc dependency, no wrapping
        # issues, no escaping artifacts.
        from .docx_parser import parse_docx_direct
        return parse_docx_direct(path)
    elif suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix in (".md", ".markdown", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported input type: {suffix}")

    return parse_markdown(text)


def _extract_docx(path: Path) -> str:
    """Use pandoc (via extract-text helper) to get markdown."""
    try:
        r = subprocess.run(
            ["extract-text", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback: pandoc directly — MUST use --wrap=none so the parser
    # sees full paragraphs instead of 72-column-wrapped fragments.
    try:
        r = subprocess.run(
            ["pandoc", "--from=docx", "--to=markdown", "--wrap=none", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError("Neither extract-text nor pandoc are available to parse .docx")


def _extract_pdf(path: Path) -> str:
    """Extract text from PDF via pdftotext."""
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError("pdftotext not available to parse PDF")


# ---------------------------------------------------------------------------
# Markdown-ish parser
# ---------------------------------------------------------------------------

def parse_markdown(text: str) -> dict:
    """
    Best-effort extraction of the JFDA manuscript structure from
    markdown-ish text.

    Heuristics:
      - Title:       first h1 or first **bold** line of substantial length
      - Authors:     lines containing "@" emails and role descriptors
      - Abstract:    block after a line starting with "Abstract" (case-insensitive)
      - Keywords:    line starting with "Keywords:"
      - JEL:         line starting with "JEL Classification" / "JEL:"
      - Sections:    any h1/h2 with leading number (1., 2., 2.1 etc.)
      - Back matter: sections with well-known headings
      - References:  after a "References" heading, one per paragraph
    """
    out = {
        "manuscript_type": "Research Article",
        "title": "",
        "authors": [],
        "abstract": "",
        "keywords": [],
        "jel_codes": [],
        "sections": [],
        "back_matter": {},
        "references": [],
    }

    # Normalise line endings and strip trailing whitespace
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]

    # --- Title ---
    title = _find_title(lines)
    if title:
        out["title"] = title

    # --- Abstract ---
    abstract, abs_end = _find_abstract(lines)
    if abstract:
        out["abstract"] = abstract

    # --- Keywords ---
    kws = _find_keywords(lines)
    if kws:
        out["keywords"] = kws

    # --- JEL ---
    jel = _find_jel(lines)
    if jel:
        out["jel_codes"] = jel

    # --- Authors ---
    authors = _find_authors(lines)
    if authors:
        out["authors"] = authors

    # --- Sections + back matter + references ---
    sections, back_matter, references = _split_sections(lines)
    if sections:
        out["sections"] = sections
    if back_matter:
        out["back_matter"] = back_matter
    if references:
        out["references"] = references

    return out


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_INLINE_BOLD = re.compile(r'\*\*(.+?)\*\*')
_INLINE_ITALIC = re.compile(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)')


def _strip_md(text: str) -> str:
    """Strip simple markdown inline markers from extracted text."""
    text = _INLINE_BOLD.sub(r'\1', text)
    text = _INLINE_ITALIC.sub(r'\1', text)
    # Common residue from pandoc conversion
    text = text.replace("\u00a0", " ")
    # Markdown links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    return text.strip()


def _find_title(lines: list[str]) -> str:
    """
    Pick the actual title from the top of the manuscript.

    Strategy: scan the first ~40 lines, reject candidates that are
    running-header-like (contain `|`), numbered section headings
    (start with `N.` or `N.M.`), or too short. Prefer the longest
    remaining bold/h1 line.
    """
    def is_running_header(s: str) -> bool:
        return "|" in s

    def is_numbered_section(s: str) -> bool:
        return bool(re.match(r'^\d+(?:\.\d+)*\.?\s+', s))

    def is_metadata(s: str) -> bool:
        return bool(re.match(
            r'^(abstract|keywords?|jel|authors?|manuscript\s*type|article\s*information|citation)\b',
            s, re.IGNORECASE,
        ))

    candidates: list[str] = []

    for ln in lines[:40]:
        stripped = ln.strip()
        # h1 heading
        m = re.match(r'^#\s+(.+)$', stripped)
        if m:
            t = _strip_md(m.group(1))
            if (not is_running_header(t) and not is_numbered_section(t)
                    and not is_metadata(t) and len(t) >= 30):
                candidates.append(t)
            continue
        # Standalone bold line
        m = re.match(r'^\*\*(.+?)\*\*\s*$', stripped)
        if m:
            t = _strip_md(m.group(1))
            if (not is_running_header(t) and not is_numbered_section(t)
                    and not is_metadata(t) and len(t) >= 30):
                candidates.append(t)

    if candidates:
        # Prefer the longest candidate (titles are usually longer than
        # incidental bold lines)
        return max(candidates, key=len)
    return ""


def _find_abstract(lines: list[str]) -> tuple[str, int]:
    """Find abstract body. Returns (text, end_index) or ('', -1)."""
    for i, ln in enumerate(lines):
        # Match "**Abstract**" or "Abstract" as a heading
        stripped = ln.strip()
        if re.match(r'^(?:\*\*)?abstract(?:\*\*)?\s*:?\s*$', stripped, re.IGNORECASE):
            # Collect paragraphs until we hit Keywords, JEL, or a new section
            chunks = []
            j = i + 1
            while j < len(lines):
                n = lines[j].strip()
                if not n:
                    j += 1
                    continue
                # Stop conditions
                if re.match(r'^(?:\*\*)?(keywords?|jel|#|\d+\.)', n, re.IGNORECASE):
                    break
                chunks.append(_strip_md(lines[j]))
                j += 1
            return (" ".join(chunks).strip(), j)
        # Abstract can also appear inline inside a table cell (pandoc output
        # of the metadata table). Try regex on the whole line.
        m = re.search(
            r'\*\*\s*Abstract\s*\*\*\s+(.+?)(?=\*\*\s*Keywords?|\*\*\s*JEL|\*\*\s*Citation|\||$)',
            ln, re.DOTALL | re.IGNORECASE,
        )
        if m:
            return (_strip_md(m.group(1)).strip(), i)
    return ("", -1)


def _find_keywords(lines: list[str]) -> list[str]:
    """
    Find keywords. Handles several real-world formats:
      **Keywords: **kw1; kw2; kw3**JEL Classification: **...
      **Keywords:** kw1; kw2; kw3
      Keywords: kw1; kw2; kw3   (plain line)
    """
    for ln in lines:
        # Bold-wrapped label (flexible whitespace around colon and closing **)
        m = re.search(
            r'\*\*\s*Keywords?\s*:?\s*\*\*\s*(.+?)(?=\*\*\s*JEL|\*\*\s*Citation|\||\r|\n|$)',
            ln,
        )
        if not m:
            m = re.match(r'^\s*Keywords?\s*:\s*(.+)$', ln, re.IGNORECASE)
        if m:
            kws_text = _strip_md(m.group(1)).strip().rstrip(".;,")
            # Semi-colons primary; commas as fallback only when no semi-colons
            if ";" in kws_text:
                parts = [k.strip() for k in kws_text.split(";")]
            else:
                parts = [k.strip() for k in kws_text.split(",")]
            parts = [k for k in parts if k]
            if parts:
                return parts
    return []


def _find_jel(lines: list[str]) -> list[str]:
    """
    Find JEL classification codes. Handles the same variety of bold /
    plain / table-cell formats as keywords.
    """
    for ln in lines:
        # Bold-wrapped label, end at next bold marker, pipe (table cell end), or EOL
        m = re.search(
            r'\*\*\s*JEL[^*]*?:?\s*\*\*\s*(.+?)(?=\*\*|\||\r|\n|$)',
            ln, re.IGNORECASE,
        )
        if not m:
            m = re.match(r'^\s*JEL\s+(?:Classification)?\s*:?\s*(.+)$', ln, re.IGNORECASE)
        if m:
            codes = _strip_md(m.group(1)).strip().rstrip(".;,")
            parts = [c.strip() for c in re.split(r'[;,]', codes)]
            # JEL codes are short alphanumeric (like G21, O16); drop garbage
            parts = [c for c in parts if c and len(c) <= 8 and re.match(r'^[A-Z]\d+$', c, re.IGNORECASE)]
            if parts:
                return parts
    return []


def _find_authors(lines: list[str]) -> list[dict]:
    """
    Extract authors from the manuscript.

    Looks for runs of: **Name** ... Email: xxx@yyy.zzz
    Handles both single-line (pandoc table-cell) and multi-line layouts.
    Uses re.finditer so multiple authors packed into one line are all found.
    """
    authors: list[dict] = []
    seen_emails: set[str] = set()

    author_re = re.compile(
        r'\*\*\s*([^*\n]+?)\s*\*\*(\*?)'     # **Name**  with optional trailing * (corresponding)
        r'([^*\n]*?)'                        # free text up to email
        r'(?:Email|E-mail|email)\s*:?\s*'    # Email:
        r'([\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,})',  # the email
        re.IGNORECASE,
    )

    full_text = "\n".join(lines)

    for m in author_re.finditer(full_text):
        raw_name = _strip_md(m.group(1)).strip().rstrip(".,")
        had_star = m.group(2) == "*"
        middle = m.group(3).strip().rstrip(",. ").strip()
        email = m.group(4).strip().rstrip(".,*")

        # Corresponding: trailing * after closing bold OR a '*' still on the raw name
        is_corresponding = had_star or raw_name.endswith("*")
        name = raw_name.rstrip("*").strip()

        # Reject non-author matches (e.g. **Email:** label as a heading)
        if not name or name.lower() in ("authors", "author", "corresponding author", "email"):
            continue

        if email in seen_emails:
            continue
        seen_emails.add(email)

        # Parse the middle (affiliation) part
        position, organisation, country = _parse_affiliation(middle)

        authors.append({
            "name": name,
            "position": position or "researcher",
            "organisation": organisation or "",
            "country": country or "",
            "email": email,
            "corresponding": is_corresponding,
        })

    # Guarantee at least one corresponding author if any were found
    if authors and not any(a["corresponding"] for a in authors):
        # Look for Name* in the top of the document
        for a in authors:
            if any(re.search(re.escape(a["name"]) + r'\s*\*', ln) for ln in lines[:60]):
                a["corresponding"] = True
                break
        else:
            authors[0]["corresponding"] = True

    return authors


def _parse_affiliation(middle: str) -> tuple[str, str, str]:
    """Best-effort split of affiliation text into (position, organisation, country).

    Handles patterns like:
        is a Lecturer, University of Foo, Auckland, New Zealand
        Auckland Institute of Studies, Auckland, New Zealand
        Lecturer, Dept. of X, Organisation, City, Country
    """
    if not middle:
        return ("", "", "")

    position = organisation = country = ""

    # Capture an "is a/an X" phrase as the position, if present
    is_m = re.match(r'(?:is\s+(?:an?\s+)?|,\s*)?(?:is\s+(?:an?\s+)?)?([^,]+?)(?=,|$)', middle)
    rest = middle
    # Simpler: detect "is (a) POSITION," prefix
    m = re.match(r'(?:is\s+(?:an?\s+)?)([^,]+?)(?:,|$)(.*)', middle, re.IGNORECASE)
    if m:
        position = m.group(1).strip()
        rest = m.group(2).strip().lstrip(",").strip()

    # Split remainder by comma; last = country, second-last = organisation
    parts = [p.strip() for p in rest.split(",") if p.strip()]
    if len(parts) >= 2:
        # Last element = country; everything else joined = organisation
        country = parts[-1].rstrip(".")
        organisation = ", ".join(parts[:-1])
    elif len(parts) == 1:
        organisation = parts[0].rstrip(".")

    return (position.strip(), organisation.strip(), country.strip())


# ---------------------------------------------------------------------------
# Section splitter
# ---------------------------------------------------------------------------

BACK_MATTER_KEYS = {
    "data availability statement": "data_availability",
    "data availability": "data_availability",
    "funding": "funding",
    "acknowledgements": "acknowledgements",
    "acknowledgments": "acknowledgements",
    "declaration of the use of generative ai": "gen_ai",
    "generative ai": "gen_ai",
    "conflicts of interest": "conflicts",
    "conflict of interest": "conflicts",
    "references": "references",
}


def _split_sections(lines: list[str]) -> tuple[list[dict], dict, list[str]]:
    """
    Walk the document linearly, opening a new section on each numbered
    or h1/h2 heading. References section is collected separately.
    """
    sections: list[dict] = []
    back_matter: dict[str, str] = {}
    references: list[str] = []

    current_root: dict | None = None
    current_sub: dict | None = None
    current_subsub: dict | None = None

    current_paragraphs: list[str] = []
    current_target: str | None = None  # "section" | "back_matter:key" | "references"
    current_back_key: str | None = None

    def flush_paragraphs():
        """
        Convert accumulated lines into paragraphs.

        In markdown, paragraphs are separated by blank lines. Consecutive
        non-blank lines belong to the same paragraph and should be joined
        with a space. This is critical because pandoc may wrap text at
        72 columns (the default), producing multiple lines per paragraph.
        """
        nonlocal current_paragraphs
        if not current_paragraphs:
            return

        # Group consecutive non-blank lines into paragraph blocks,
        # using blank lines as separators.
        paragraphs: list[str] = []
        buf: list[str] = []
        for line in current_paragraphs:
            s = line.strip()
            if s:
                buf.append(s)
            else:
                if buf:
                    paragraphs.append(" ".join(buf))
                    buf = []
        if buf:
            paragraphs.append(" ".join(buf))

        if not paragraphs:
            current_paragraphs = []
            return

        if current_target == "references":
            for p in paragraphs:
                references.append(p)
        elif current_target and current_target.startswith("back_matter:"):
            key = current_target.split(":", 1)[1]
            back_matter[key] = back_matter.get(key, "")
            existing = back_matter[key]
            new = "\n\n".join(paragraphs)
            back_matter[key] = (existing + "\n\n" + new).strip() if existing else new
        elif current_target == "section":
            target = current_subsub or current_sub or current_root
            if target is not None:
                target.setdefault("paragraphs", []).extend(paragraphs)
        current_paragraphs = []

    def looks_like_heading(line: str) -> tuple[str | None, str | None]:
        """Return (level, text) or (None, None)."""
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            return (m.group(1), _strip_md(m.group(2)))
        # Bold standalone line that looks like a section
        m = re.match(r'^\*\*(.+?)\*\*\s*$', line.strip())
        if m:
            txt = _strip_md(m.group(1))
            # Must look like a section: "1. Title", "2.1 Title", or a back-matter key
            if re.match(r'^\d+\.', txt) or txt.lower() in BACK_MATTER_KEYS:
                return ("##", txt)
        return (None, None)

    def classify_section(text: str) -> str:
        low = text.lower().strip().rstrip(".:").strip()
        if low in BACK_MATTER_KEYS:
            key = BACK_MATTER_KEYS[low]
            return "references" if key == "references" else f"back_matter:{key}"
        return "section"

    def heading_depth(text: str) -> int:
        """1 for '1. Intro', 2 for '2.1 Sub', 3 for '4.1.1 Subsub', else 1."""
        m = re.match(r'^(\d+(?:\.\d+)*)', text)
        if not m:
            return 1
        return m.group(1).count(".") + 1

    i = 0
    in_table = False
    table_lines: list[str] = []
    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()

        # Detect markdown pipe table start
        if stripped.startswith("|") and "|" in stripped[1:]:
            # Collect consecutive table lines
            in_table = True
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            # Convert & attach
            tbl_item = _convert_md_table(table_lines)
            if tbl_item:
                flush_paragraphs()
                target = current_subsub or current_sub or current_root
                if target is not None and current_target == "section":
                    target.setdefault("content", []).append(tbl_item)
            in_table = False
            continue

        level, text = looks_like_heading(ln)
        if level and text:
            # Close out previous paragraphs
            flush_paragraphs()

            target = classify_section(text)
            if target == "references":
                current_target = "references"
                current_root = current_sub = current_subsub = None
                i += 1
                continue
            if target.startswith("back_matter:"):
                current_target = target
                current_root = current_sub = current_subsub = None
                i += 1
                continue

            # Regular numbered/structured section
            depth = heading_depth(text)
            if depth == 1:
                current_root = {"heading": text, "paragraphs": [], "subsections": [], "content": []}
                current_sub = current_subsub = None
                sections.append(current_root)
            elif depth == 2 and current_root is not None:
                current_sub = {"heading": text, "paragraphs": [], "subsections": [], "content": []}
                current_subsub = None
                current_root["subsections"].append(current_sub)
            elif depth >= 3 and current_sub is not None:
                current_subsub = {"heading": text, "paragraphs": [], "content": []}
                current_sub["subsections"].append(current_subsub)
            elif depth >= 3 and current_root is not None:
                # No current_sub but depth 3+ — treat as sub
                current_sub = {"heading": text, "paragraphs": [], "subsections": [], "content": []}
                current_subsub = None
                current_root["subsections"].append(current_sub)
            else:
                # Orphan (no parent root yet)
                current_root = {"heading": text, "paragraphs": [], "subsections": [], "content": []}
                current_sub = current_subsub = None
                sections.append(current_root)

            current_target = "section"
            i += 1
            continue

        # Body content
        if stripped:
            # Figure placeholder/callout lines like "[Figure 1 — Insert figure here]"
            mfig = re.match(
                r'^[\[\(]?\s*Figure\s+(\d+)([a-z]?)\s*(?:\u2014|--|\-|:)?[^\]\)]*[\]\)]?\s*$',
                stripped, re.IGNORECASE,
            )
            if mfig:
                idx = int(mfig.group(1))
                sub = (mfig.group(2) or "").lower()
                flush_paragraphs()

                # Grab an optional following caption ("**Figure 1. …**") and
                # italic source line ("*Source: …*") before moving on.
                caption = ""
                source = ""
                j = i + 1
                # Skip one blank line
                while j < len(lines) and not lines[j].strip():
                    j += 1
                # Caption
                if j < len(lines):
                    cap_m = re.match(
                        r'^\*\*\s*Figure\s+\d+[a-z]?\.\s*\*\*\s*(.+?)\s*$',
                        lines[j].strip(),
                    )
                    if not cap_m:
                        cap_m = re.match(
                            r'^\*\*\s*Figure\s+\d+[a-z]?\.\s*(.+?)\s*\*\*\s*$',
                            lines[j].strip(),
                        )
                    if cap_m:
                        caption = _strip_md(cap_m.group(1)).strip().rstrip(".")
                        j += 1
                        while j < len(lines) and not lines[j].strip():
                            j += 1
                # Source
                if j < len(lines):
                    src_m = re.match(r'^\*\s*(Source:.+?)\s*\*\s*$', lines[j].strip())
                    if src_m:
                        source = _strip_md(src_m.group(1)).strip().rstrip(".")
                        j += 1

                target = current_subsub or current_sub or current_root
                if target is not None and current_target == "section":
                    target.setdefault("content", []).append({
                        "type": "figure",
                        "label": f"Figure {idx}{sub}",
                        "caption": caption,
                        "source": source,
                        "index": idx,
                        "sub": sub,
                        "_placeholder": True,
                    })
                i = j
                continue
            current_paragraphs.append(_strip_md(ln))
        else:
            # Blank line — acts as a paragraph separator.
            # Append an empty string so flush_paragraphs() can detect the
            # boundary between consecutive text paragraphs.
            current_paragraphs.append("")
        i += 1

    flush_paragraphs()

    return sections, back_matter, references


def _convert_md_table(table_lines: list[str]) -> dict | None:
    """Convert pandoc-style pipe table into a `type:table` item."""
    rows = []
    for ln in table_lines:
        # Skip separator rows like |---|---|
        if re.match(r'^\|[\s\-:\|]+\|?\s*$', ln):
            continue
        # Split by pipe, drop leading/trailing empty cells
        parts = [p.strip() for p in ln.strip().strip("|").split("|")]
        if parts:
            rows.append([_strip_md(p) for p in parts])
    if len(rows) < 2:
        return None
    headers = rows[0]
    data = rows[1:]
    # Drop trailing empty columns present in some pandoc outputs
    while headers and not headers[-1]:
        headers.pop()
        for r in data:
            if r:
                r.pop() if len(r) > len(headers) else None
    # Pad short rows
    w = len(headers)
    data = [r + [""] * (w - len(r)) for r in data]
    return {
        "type": "table",
        "caption_label": "",
        "caption_title": "",
        "headers": headers,
        "rows": data,
        "source": "",
    }
