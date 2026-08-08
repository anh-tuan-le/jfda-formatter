"""
Direct .docx XML parser for JFDA manuscripts.

Reads the document.xml inside a .docx zip directly, extracting
structured content without relying on pandoc. This avoids all
wrapping, escaping, and table-formatting issues that pandoc
introduces.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
}


def parse_docx_direct(path: Path) -> dict:
    """Parse a .docx file directly from its XML, returning the standard
    manuscript dict."""
    with zipfile.ZipFile(path) as z:
        doc_xml = z.read('word/document.xml')

    root = ET.fromstring(doc_xml)
    body = root.find('w:body', NS)
    if body is None:
        raise ValueError("No w:body found in document.xml")

    # Flatten ALL content into a linear element list.
    # Some manuscripts put body content inside the template table; some put
    # it outside. We handle both by walking direct children and inlining
    # table-cell content when the table looks like the JFDA metadata/body table.
    elements = []
    for child in body:
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if tag == 'p':
            elements.append(_parse_paragraph(child))
        elif tag == 'tbl':
            _flatten_table(child, elements)
        # Skip sectPr, etc.

    return _build_manuscript(elements)


def _flatten_table(tbl, elements: list) -> None:
    """Decide how to handle a <w:tbl>.

    The JFDA template uses a 2-column borderless table for the metadata area
    (left: authors/article info; right: abstract/keywords/JEL). Some authors
    type their entire manuscript body into the right cell of this table.

    Strategy:
    - If the table has exactly 2 columns and ≥20 paragraphs in cell 1,
      treat it as the metadata+body table: inline all paragraphs from both
      cells (left first, then right) as if they were direct body children.
    - For any other table (data tables inside the body), parse as a
      structured table item with headers + rows.
    """
    rows = tbl.findall('w:tr', NS)
    if not rows:
        elements.append(_parse_table(tbl))
        return

    first_row = rows[0]
    cells = first_row.findall('w:tc', NS)

    # Detect the JFDA metadata table: 2 cells where left starts with "Authors"
    # and/or right starts with "Abstract"
    if len(cells) == 2:
        left_paras = cells[0].findall('w:p', NS)
        right_paras = cells[1].findall('w:p', NS)

        # Check content signatures
        left_texts = [_get_text(p).strip().lower() for p in left_paras[:5]]
        right_texts = [_get_text(p).strip().lower() for p in right_paras[:5]]
        has_authors = 'authors' in left_texts
        has_abstract = 'abstract' in right_texts

        # Also check if the right cell contains numbered section headings
        # (body-in-table manuscripts)
        has_sections = False
        for p in right_paras[:60]:
            text = _get_text(p).strip()
            if SECTION_RE.match(text):
                has_sections = True
                break

        if has_authors or has_abstract or has_sections:
            # This is the metadata (and possibly body) table.
            # Walk each cell's DIRECT CHILDREN to preserve nested tables
            # and drawings (not just <w:p> elements).
            for p in left_paras:
                pp = _parse_paragraph(p)
                pp['_from_table'] = True
                pp['_cell'] = 'left'
                elements.append(pp)

            # Right cell: walk direct children to catch nested <w:tbl> and <w:p>
            for child in cells[1]:
                child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if child_tag == 'p':
                    pp = _parse_paragraph(child)
                    pp['_from_table'] = True
                    pp['_cell'] = 'right'
                    elements.append(pp)
                elif child_tag == 'tbl':
                    # Data table nested inside the cell — parse as structured table
                    elements.append(_parse_table(child))

            # Process remaining rows (usually empty in the template)
            for row in rows[1:]:
                for cell in row.findall('w:tc', NS):
                    for p in cell.findall('w:p', NS):
                        text = _get_text(p).strip()
                        if text:
                            pp = _parse_paragraph(p)
                            pp['_from_table'] = True
                            elements.append(pp)
            return

    # Not the metadata table — it's a data table in the body
    elements.append(_parse_table(tbl))


# ---------------------------------------------------------------------------
# Paragraph parsing
# ---------------------------------------------------------------------------

def _get_text(elem) -> str:
    """Recursively extract all text from an element, including math elements."""
    parts = []
    # Regular text runs
    for r in elem.findall('.//w:r', NS):
        for t in r.findall('w:t', NS):
            if t.text:
                parts.append(t.text)
    # Math elements (Office Math ML)
    MATH_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
    for m_elem in elem.iter(f'{{{MATH_NS}}}t'):
        if m_elem.text:
            parts.append(m_elem.text)
    return ''.join(parts)


def _get_style(elem) -> str:
    """The paragraph's style id, e.g. 'Heading1', 'BodyText', 'ListParagraph'."""
    ppr = elem.find('w:pPr', NS)
    if ppr is not None:
        pstyle = ppr.find('w:pStyle', NS)
        if pstyle is not None:
            return (pstyle.get(f'{{{NS["w"]}}}val') or '').strip()
    return ''


def _style_level(style: str):
    """Heading depth implied by a style id, or None.

    'Title' -> 0, 'Heading1'/'heading 1' -> 1, 'Heading2' -> 2, ...
    Converter exports (SSRN, Google Docs, pandoc) carry structure ONLY in
    these style ids, with no numbering and no explicit font size, which is
    why bold+size detection alone misses them.
    """
    s = (style or '').lower().replace('-', '').replace('_', '').replace(' ', '')
    if s in ('title', 'subtitle'):
        return 0
    m = re.match(r'^heading(\d)$', s)
    if m:
        return int(m.group(1))
    return None


def _get_align(elem) -> str:
    ppr = elem.find('w:pPr', NS)
    if ppr is not None:
        jc = ppr.find('w:jc', NS)
        if jc is not None:
            return (jc.get(f'{{{NS["w"]}}}val') or '').strip()
    return ''


def _is_bold(elem) -> bool:
    """Check if the element has bold formatting, either via inline <w:b>
    or via a heading style (Heading1-6 are implicitly bold)."""
    # Check paragraph-level style for heading
    ppr = elem.find('w:pPr', NS)
    if ppr is not None:
        pstyle = ppr.find('w:pStyle', NS)
        if pstyle is not None:
            style_val = pstyle.get(f'{{{NS["w"]}}}val', '').lower()
            if style_val.startswith('heading'):
                return True
        rpr = ppr.find('w:rPr', NS)
        if rpr is not None and rpr.find('w:b', NS) is not None:
            return True
    # Check first run
    for r in elem.findall('w:r', NS):
        rpr = r.find('w:rPr', NS)
        if rpr is not None and rpr.find('w:b', NS) is not None:
            return True
        break
    return False


def _is_italic(elem) -> bool:
    for r in elem.findall('w:r', NS):
        rpr = r.find('w:rPr', NS)
        if rpr is not None and rpr.find('w:i', NS) is not None:
            return True
        break
    return False


def _get_font_size(elem) -> int:
    """Get font size in half-points from paragraph or first run."""
    ppr = elem.find('w:pPr', NS)
    if ppr is not None:
        rpr = ppr.find('w:rPr', NS)
        if rpr is not None:
            sz = rpr.find('w:sz', NS)
            if sz is not None:
                val = sz.get(f'{{{NS["w"]}}}val')
                if val:
                    return int(val)
    for r in elem.findall('w:r', NS):
        rpr = r.find('w:rPr', NS)
        if rpr is not None:
            sz = rpr.find('w:sz', NS)
            if sz is not None:
                val = sz.get(f'{{{NS["w"]}}}val')
                if val:
                    return int(val)
        break
    return 0


def _has_drawing(elem) -> bool:
    """Check if paragraph contains an image/drawing."""
    return elem.find('.//w:drawing', NS) is not None or \
           elem.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline') is not None


def _has_math(elem) -> bool:
    """Check if paragraph contains Office Math elements."""
    MATH_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
    return elem.find(f'.//{{{MATH_NS}}}oMath') is not None or \
           elem.find(f'.//{{{MATH_NS}}}oMathPara') is not None


def _parse_paragraph(p) -> dict:
    text = _get_text(p)
    has_math_content = _has_math(p)
    raw_math_xml = None
    if has_math_content:
        # Preserve the raw OMML XML for equation paragraphs so the builder
        # can inject it directly into document.xml (renders as real equations
        # in Word, not plain text).
        raw_math_xml = _extract_math_xml(p)
    style = _get_style(p)
    return {
        '_type': 'paragraph',
        'text': text,
        'bold': _is_bold(p),
        'italic': _is_italic(p),
        'font_size': _get_font_size(p),
        'has_drawing': _has_drawing(p),
        'has_math': has_math_content,
        'raw_math_xml': raw_math_xml,
        'style': style,
        'style_level': _style_level(style),
        'align': _get_align(p),
    }


def _extract_math_xml(p) -> str:
    """Extract the raw XML of all <m:oMathPara> and <m:oMath> elements
    inside a paragraph, preserving their full structure for re-injection.

    The output uses m: and w: namespace prefixes to match the JFDA template's
    document.xml conventions.
    """
    MATH_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
    WORD_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    parts = []
    for child in p:
        child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if child_tag in ('oMathPara', 'oMath'):
            raw = ET.tostring(child, encoding='unicode')
            # Replace namespace prefixes: ns0: → m:, ns1: → w:
            # ET.tostring assigns auto-generated prefixes (ns0, ns1, etc.)
            raw = raw.replace(f'xmlns:ns0="{MATH_NS}"', '')
            raw = raw.replace(f'xmlns:ns1="{WORD_NS}"', '')
            raw = raw.replace('ns0:', 'm:')
            raw = raw.replace('ns1:', 'w:')
            # Add the proper namespace declarations
            if '<m:oMathPara' in raw:
                raw = raw.replace('<m:oMathPara',
                    f'<m:oMathPara xmlns:m="{MATH_NS}" xmlns:w="{WORD_NS}"', 1)
            elif '<m:oMath' in raw:
                raw = raw.replace('<m:oMath',
                    f'<m:oMath xmlns:m="{MATH_NS}" xmlns:w="{WORD_NS}"', 1)
            parts.append(raw.strip())
    if parts:
        return ''.join(parts)
    # Fallback: search deeper
    for elem in p.iter():
        elem_tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        if elem_tag in ('oMathPara', 'oMath'):
            raw = ET.tostring(elem, encoding='unicode')
            raw = raw.replace(f'xmlns:ns0="{MATH_NS}"', '')
            raw = raw.replace(f'xmlns:ns1="{WORD_NS}"', '')
            raw = raw.replace('ns0:', 'm:')
            raw = raw.replace('ns1:', 'w:')
            if '<m:oMathPara' in raw:
                raw = raw.replace('<m:oMathPara',
                    f'<m:oMathPara xmlns:m="{MATH_NS}" xmlns:w="{WORD_NS}"', 1)
            elif '<m:oMath' in raw:
                raw = raw.replace('<m:oMath',
                    f'<m:oMath xmlns:m="{MATH_NS}" xmlns:w="{WORD_NS}"', 1)
            parts.append(raw.strip())
    return ''.join(parts) if parts else None


def _parse_table(tbl) -> dict:
    """Parse a <w:tbl> into headers + rows."""
    rows = []
    for tr in tbl.findall('w:tr', NS):
        cells = []
        for tc in tr.findall('w:tc', NS):
            cell_text = _get_text(tc).strip()
            cells.append(cell_text)
        if cells:
            rows.append(cells)

    if len(rows) < 2:
        return {'_type': 'table', 'headers': rows[0] if rows else [], 'rows': []}

    return {
        '_type': 'table',
        'headers': rows[0],
        'rows': rows[1:],
    }


# ---------------------------------------------------------------------------
# Manuscript builder
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

# Detect section headings like "1. Introduction", "2.1 Sub", "I. Introduction", "II. Methods"
SECTION_RE = re.compile(r'^(\d+(?:\.\d+)*\.?|[IVXLC]+\.)\s+(.+)$')

# Map Roman numerals to Arabic for depth detection
ROMAN_MAP = {'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6,'VII':7,'VIII':8,'IX':9,'X':10,
             'XI':11,'XII':12,'XIII':13,'XIV':14,'XV':15}

def _parse_section_num(num_str: str) -> tuple:
    """Parse a section number string into (depth, numeric_value).
    Returns (1, 5) for '5', (2, 1) for '5.1', (1, 3) for 'III.'
    """
    num = num_str.rstrip('.')
    # Roman numeral?
    if num.upper() in ROMAN_MAP:
        return (1, ROMAN_MAP[num.upper()])
    # Arabic with dots
    parts = num.split('.')
    try:
        return (len(parts), int(parts[0]))
    except ValueError:
        return (1, 0)

# Detect figure captions: "Figure 1. Caption" or "Fig 1. Caption"
FIGURE_CAPTION_RE = re.compile(r'^(?:Figure|Fig\.?)\s+(\d+)([a-z]?)\.\s*(.*)$', re.IGNORECASE)

# Detect author-inserted placeholder lines: "[Figure 1 — Insert figure here]"
FIGURE_PLACEHOLDER_RE = re.compile(
    r'^\[?\s*Figure\s+\d+[a-z]?\s*[\u2014\-—:].*(insert|placeholder|here)\s*\]?\s*$',
    re.IGNORECASE,
)

# Detect table captions: "Table 1. Caption", "Table 1 — Caption", "Table 1: Caption"
TABLE_CAPTION_RE = re.compile(r'^Table\s+(\d+)\s*[.\u2014\u2013\-:]\s*(.*)$', re.IGNORECASE)

# Front-matter labels that are never a title and never a body section.
FRONT_LABELS = {
    'abstract', 'summary', 'keywords', 'keyword', 'key words',
    'jel', 'jel classification', 'jel codes', 'authors', 'author',
    'article information', 'citation', 'corresponding author',
    'manuscript type', 'contents', 'table of contents',
}

# Headings that mean "the body starts here", used when a document has no
# section numbering at all (converter exports).
BODY_START_WORDS = (
    'introduction', 'background', 'literature review', 'related work',
    'main part', 'main body', 'data and methodology', 'methodology',
    'method', 'methods', 'materials and methods', 'theoretical framework',
)

EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
NAME_RE = re.compile(r"([A-Z][\w\u2019'\-]+(?:\s+[A-Z][\w\u2019'\-]+){1,4})")
POSITION_WORDS = (
    'lecturer', 'professor', 'student', 'researcher', 'research fellow',
    'associate', 'assistant', 'doctor', 'phd', 'ph.d', 'head', 'director',
    'senior', 'analyst', 'manager', 'dean', 'engineer', 'consultant',
    'candidate', 'specialist', 'architect', 'economist', 'independent',
)
ORG_WORDS = (
    'university', 'universiti', 'institute', 'college', 'school',
    'department', 'faculty', 'academy', 'bank', 'centre', 'center',
    'laboratory', 'company', 'ltd', 'llc', 'inc', 'corporation',
    'agency', 'ministry', 'foundation', 'polytechnic',
)


def _looks_like_body_start(text: str) -> bool:
    t = text.lower().strip().rstrip('.:')
    return any(t == w or t.startswith(w) for w in BODY_START_WORDS)


def _build_manuscript(elements: list[dict]) -> dict:
    """Walk the flat element list and assemble a structured manuscript."""

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

    # Phase 1: Find the first section heading.
    # Try Roman "I." first, then Arabic "1." — must be bold.
    intro_idx = None
    for i, el in enumerate(elements):
        if el['_type'] != 'paragraph':
            continue
        text = el['text'].strip()
        if re.match(r'^I\.\s+', text) and el.get('bold'):
            intro_idx = i
            break
    if intro_idx is None:
        for i, el in enumerate(elements):
            if el['_type'] != 'paragraph':
                continue
            text = el['text'].strip()
            m = SECTION_RE.match(text)
            if m and el.get('bold'):
                depth, num_val = _parse_section_num(m.group(1))
                if depth == 1 and num_val == 1:
                    intro_idx = i
                    break

    if intro_idx is None:
        # Style-based structure (Heading1/Heading2 with no numbering) — the
        # shape produced by SSRN/pandoc/Google Docs exports.
        headings_seen = 0
        for i, el in enumerate(elements):
            if el['_type'] != 'paragraph':
                continue
            lvl = el.get('style_level')
            text = el['text'].strip()
            if lvl is None or not text:
                continue
            key = text.lower().rstrip('.: ')
            if key in FRONT_LABELS or BACK_MATTER_KEYS.get(key):
                continue
            if _looks_like_body_start(key):
                intro_idx = i
                break
            headings_seen += 1
            if headings_seen > 1:
                # The first such heading is the title; the next one starts the body.
                intro_idx = i
                break

    if intro_idx is None:
        # No numbered sections found — try looking for "Introduction" keyword
        for i, el in enumerate(elements):
            if el['_type'] != 'paragraph':
                continue
            if 'introduction' in el['text'].strip().lower():
                intro_idx = i
                break
        if intro_idx is None:
            intro_idx = len(elements)  # No body found

    # Extract metadata from pre-body elements
    _extract_metadata_from_elements(elements[:intro_idx], out)

    # Phase 2: Build sections from intro_idx onwards
    body_elements = [el for el in elements[intro_idx:] if el['_type'] in ('paragraph', 'table')]
    _extract_body(body_elements, out)

    _autonumber_sections(out)

    return out


def _autonumber_sections(out: dict) -> None:
    """The JFDA template numbers its sections (1., 2., 2.1). Documents that
    arrive with unnumbered headings get numbered here so the output matches."""
    secs = out.get('sections') or []
    if not secs:
        return
    if any(re.match(r'^(\d+[.)]|[IVXLC]+\.)\s', s.get('heading', '')) for s in secs):
        return
    for n, sec in enumerate(secs, 1):
        sec['heading'] = f"{n}. {sec['heading']}"
        for m, sub in enumerate(sec.get('subsections') or [], 1):
            sub['heading'] = f"{n}.{m} {sub['heading']}"
            for k, ss in enumerate(sub.get('subsections') or [], 1):
                ss['heading'] = f"{n}.{m}.{k} {ss['heading']}"


def _extract_metadata_from_elements(elems: list[dict], out: dict) -> None:
    """Extract title, authors, abstract, keywords, JEL from the pre-body
    element list. Handles both direct <w:p> elements and inlined table-cell
    paragraphs."""

    i = 0
    while i < len(elems):
        el = elems[i]
        if el['_type'] != 'paragraph':
            i += 1
            continue

        text = el['text'].strip()
        low = text.lower()

        # Skip empty
        if not text:
            i += 1
            continue

        # Manuscript type
        if low.startswith('manuscript type') or re.match(r'^(research |review |short |case |perspective)', low):
            m = re.search(r'[–\-:]\s*(.+)', text)
            if m:
                out['manuscript_type'] = m.group(1).strip()
            elif not low.startswith('manuscript type'):
                out['manuscript_type'] = text
            i += 1
            continue

        # Title: a Title/Heading1 style paragraph before the abstract. Converter
        # exports carry no explicit font size, so bold+size checks miss it.
        if (not out['title'] and len(text) > 12
                and el.get('style_level') in (0, 1)
                and low.rstrip('.: ') not in FRONT_LABELS
                and not BACK_MATTER_KEYS.get(low.rstrip('.: '))
                and not _looks_like_body_start(low)):
            out['title'] = text
            i += 1
            continue

        # Title: largest bold text
        if el['bold'] and el['font_size'] >= 24 and len(text) > 20 and not out['title']:
            out['title'] = text
            i += 1
            continue

        # Author name from pre-table area (bold, 24pt, not a heading)
        if el['bold'] and el['font_size'] >= 20 and not el.get('_from_table') and not out['title']:
            out['title'] = text
            i += 1
            continue

        # Author name line (bold, below title, not in table)
        # Handles multiple formats:
        #   "Syed Matiur Rahman* and Hanoku Bathula"  (multiple names with 'and')
        #   "Makoto Shibata, FINOLAB, The FinTech Center of Tokyo, Japan."  (single author + affiliation)
        if el['bold'] and not el.get('_from_table') and out['title'] and not SECTION_RE.match(text):
            if len(text) < 200 and not low.startswith(('abstract', 'keywords', 'jel', 'article', 'citation', 'authors', 'manuscript', 'references')):
                if len(text.split()) >= 2:
                    out['_pretable_authors'] = out.get('_pretable_authors', [])

                    # Check if this is "Name1 and Name2" format (multi-author)
                    # vs "Name, Org, City, Country." format (single author + affiliation)
                    has_and = re.search(r'\band\b', text, re.IGNORECASE)
                    has_email = '@' in text
                    # Heuristic: if "and" separates what look like names (2-3 words each),
                    # it's multi-author. Otherwise treat as single author + affiliation.
                    if has_and and not has_email:
                        # Split by "and" — check if each part looks like a name
                        and_parts = re.split(r'\s+and\s+', text, flags=re.IGNORECASE)
                        all_look_like_names = all(
                            len(p.strip().rstrip('*').split()) <= 4 and
                            not any(w.lower() in ('university','institute','center','centre','department','school','lab','japan','usa','uk','new','zealand')
                                   for w in p.strip().split())
                            for p in and_parts
                        )
                        if all_look_like_names:
                            # Multi-author: "Name1, Name2, and Name3"
                            names = [n.strip().rstrip('*') for n in re.split(r',\s*(?:and\s+)?|\s+and\s+', text) if n.strip()]
                            for nm in names:
                                nm_clean = nm.strip().rstrip('*').rstrip('.')
                                if nm_clean and len(nm_clean) > 2:
                                    out['_pretable_authors'].append({
                                        'name': nm_clean,
                                        'corresponding': '*' in nm,
                                    })
                            i += 1
                            continue

                    # Single author with affiliation: "Name, Org, City, Country."
                    # First comma-separated part is the name, rest is affiliation
                    parts = [p.strip().rstrip('.') for p in text.split(',')]
                    author_name = parts[0].strip().rstrip('*')
                    is_corr = '*' in parts[0]
                    org = ', '.join(parts[1:-1]).strip() if len(parts) > 2 else (parts[1].strip() if len(parts) > 1 else '')
                    country = parts[-1].strip().rstrip('.') if len(parts) > 2 else ''

                    out['_pretable_authors'].append({
                        'name': author_name,
                        'corresponding': is_corr,
                        'organisation': org,
                        'country': country,
                    })
                    i += 1
                    continue

        # "Authors" heading — start of author affiliation block
        if low == 'authors':
            i += 1
            _parse_author_block(elems, i, out)
            # Skip past the author block
            while i < len(elems) and elems[i]['_type'] == 'paragraph':
                t = elems[i]['text'].strip().lower()
                if t in ('abstract',) or t.startswith('article information'):
                    break
                if SECTION_RE.match(elems[i]['text'].strip()):
                    break
                i += 1
            continue

        # Abstract
        if low in ('abstract', 'abstract:', 'summary'):
            i += 1
            parts = []
            while i < len(elems) and elems[i]['_type'] == 'paragraph':
                at = elems[i]['text'].strip()
                al = at.lower()
                if not at:
                    i += 1
                    continue
                if al.startswith('keyword') or al.startswith('jel'):
                    break
                if elems[i].get('style_level') is not None:
                    break
                if SECTION_RE.match(at):
                    break
                parts.append(at)
                i += 1
            out['abstract'] = ' '.join(parts)
            continue

        # Keywords — either "Keywords: a; b" on one line, or a "Keywords"
        # heading followed by the list on the next paragraph.
        if low.startswith('keyword') or low.startswith('key words'):
            kw_text = re.sub(r'^key\s*words?\s*:?\s*', '', text, flags=re.IGNORECASE).strip()
            i += 1
            if not kw_text:
                while i < len(elems) and elems[i]['_type'] == 'paragraph':
                    nt = elems[i]['text'].strip()
                    if not nt:
                        i += 1
                        continue
                    if elems[i].get('style_level') is not None or nt.lower().startswith('jel'):
                        break
                    kw_text = nt
                    i += 1
                    break
            if ';' in kw_text:
                out['keywords'] = [k.strip() for k in kw_text.split(';') if k.strip()]
            else:
                out['keywords'] = [k.strip() for k in kw_text.split(',') if k.strip()]
            continue

        # JEL — same two shapes as keywords
        if low.startswith('jel'):
            jel_text = re.sub(r'^jel\s*(?:classification|codes?)?\s*:?\s*', '', text, flags=re.IGNORECASE).strip()
            i += 1
            if not jel_text:
                while i < len(elems) and elems[i]['_type'] == 'paragraph':
                    nt = elems[i]['text'].strip()
                    if not nt:
                        i += 1
                        continue
                    if elems[i].get('style_level') is not None:
                        break
                    jel_text = nt
                    i += 1
                    break
            codes = [c.strip() for c in re.split(r'[;,]', jel_text) if c.strip()]
            # Filter: valid JEL codes are letter + digits (e.g. A10, G00, F40)
            out['jel_codes'] = [c for c in codes if re.match(r'^[A-Z]\d+$', c, re.IGNORECASE)]
            continue

        # Skip known non-content items
        if low in ('article information', 'citation:', 'citation',
                    '* corresponding author.', '* corresponding author',
                    'to be added by editorial staff during production',
                    'to be added by editorial staff during production.'):
            i += 1
            continue

        # Skip date lines
        if re.match(r'^(received|revised|accepted|published)\s*:', low):
            i += 1
            continue

        i += 1

    # Merge pre-table author names with table-extracted affiliations.
    # The JFDA template puts the author names in a bold line ABOVE the table,
    # and the affiliations/emails INSIDE the table. If we found both, merge them.
    pretable = out.pop('_pretable_authors', [])
    if pretable and out['authors']:
        # If pre-table has N names and table has N entries (or fewer),
        # enrich the table entries with the pre-table names.
        for j, pt in enumerate(pretable):
            if j < len(out['authors']):
                a = out['authors'][j]
                # Always prefer the pre-table name — it's the real author name
                # from the bold line above the table
                a['name'] = pt['name']
                if pt.get('corresponding'):
                    a['corresponding'] = True
            else:
                out['authors'].append({
                    'name': pt['name'],
                    'position': 'researcher',
                    'organisation': '',
                    'country': '',
                    'email': '',
                    'corresponding': pt.get('corresponding', False),
                })
    elif pretable and not out['authors']:
        # Only pre-table names exist (no table-based affiliations)
        for pt in pretable:
            out['authors'].append({
                'name': pt['name'],
                'position': 'researcher',
                'organisation': pt.get('organisation', ''),
                'country': pt.get('country', ''),
                'email': pt.get('email', ''),
                'corresponding': pt.get('corresponding', False),
            })

    # Last resort: no author block at all (converter exports scatter names,
    # positions and emails across a few unlabelled lines). Recover what we
    # can by anchoring on the email addresses.
    if not out['authors']:
        _authors_from_front_lines(elems, out)

    # Ensure at least one corresponding author
    if out['authors'] and not any(a['corresponding'] for a in out['authors']):
        out['authors'][0]['corresponding'] = True


def _pick_name(text: str, prefer_last: bool) -> str:
    """Best name-shaped phrase in a fragment, ignoring role/organisation words."""
    cands = []
    for m in NAME_RE.finditer(text or ''):
        cand = m.group(1).strip()
        low = cand.lower()
        if any(w in low for w in ORG_WORDS) or any(w in low for w in POSITION_WORDS):
            continue
        if low.startswith(('email', 'e-mail', 'abstract', 'keywords')):
            continue
        if len(cand.split()) < 2:
            continue
        cands.append(cand)
    if not cands:
        return ''
    return cands[-1] if prefer_last else cands[0]


def _pick_phrase(text: str, words: tuple) -> str:
    """The comma-delimited chunk that mentions one of `words`."""
    for chunk in re.split(r'[,;\u2022|]', text or ''):
        c = chunk.strip(' .')
        if c and any(w in c.lower() for w in words):
            return c
    return ''


def _authors_from_front_lines(elems: list[dict], out: dict) -> None:
    lines = []
    seen_title = False
    for el in elems:
        if el['_type'] != 'paragraph':
            continue
        t = el['text'].strip()
        if not t:
            continue
        key = t.lower().rstrip('.: ')
        if el.get('style_level') is not None or key in FRONT_LABELS or BACK_MATTER_KEYS.get(key):
            # The author lines always sit between the title and the first
            # front-matter heading (Abstract/Keywords) — stop there so the
            # abstract text can't contaminate the affiliation fields.
            if seen_title:
                break
            seen_title = True
            continue
        if out.get('title') and t == out['title']:
            seen_title = True
            continue
        if re.match(r'^(received|revised|accepted|published)\s*:', key):
            continue
        lines.append(t)

    blob = ' '.join(lines)
    emails = EMAIL_RE.findall(blob)
    if not emails:
        return

    parts = EMAIL_RE.split(blob)
    for idx, email in enumerate(emails):
        before = parts[idx] if idx < len(parts) else ''
        after = parts[idx + 1] if idx + 1 < len(parts) else ''
        name = _pick_name(before, prefer_last=True) or _pick_name(after, prefer_last=False)
        position = _pick_phrase(before, POSITION_WORDS)
        org = _pick_phrase(before, ORG_WORDS)
        if name:
            position = position.replace(name, '').strip(' ,.')
            org = org.replace(name, '').strip(' ,.')
        country = ''
        tail = [c.strip(' .') for c in re.split(r'[,;]', before) if c.strip(' .')]
        if tail and len(tail[-1].split()) <= 3 and not EMAIL_RE.search(tail[-1]):
            if tail[-1].lower() not in (org.lower(), position.lower()) and tail[-1] != name:
                country = tail[-1]
        out['authors'].append({
            'name': name or 'Unknown',
            'position': position or 'researcher',
            'organisation': org,
            'country': country,
            'email': email,
            'corresponding': idx == 0,
        })


def _parse_author_block(elems: list[dict], start: int, out: dict) -> None:
    """Parse author affiliation entries from the left table cell.

    Handles both bold-name-first format and plain-text format:
      Bold: "**Syed Matiur Rahman***" → name
      Plain: "Lead Architect , CCHCS," → position+org
    """
    i = start
    current_name = None
    current_org = ""
    current_country = ""
    current_email = ""
    current_corr = False

    while i < len(elems) and elems[i]['_type'] == 'paragraph':
        text = elems[i]['text'].strip()
        low = text.lower()

        if not text:
            # Empty line = end of current author block
            if current_name or current_email:
                _flush_author(out, current_name, current_org, current_country, current_email, current_corr)
                current_name = current_org = current_country = current_email = ""
                current_corr = False
            i += 1
            continue

        # Stop conditions
        if low.startswith('* corresponding'):
            i += 1
            continue
        if low in ('article information', 'abstract') or low.startswith('article information'):
            break
        if SECTION_RE.match(text):
            break

        # Email line
        if low.startswith('email:') or low.startswith('e-mail:'):
            current_email = re.sub(r'^e?-?mail:\s*', '', text, flags=re.IGNORECASE).strip()
            i += 1
            continue

        # Bold = author name
        if elems[i]['bold'] and low not in ('authors', 'article information', 'citation:', 'citation'):
            # Flush previous author if any
            if current_name or current_email:
                _flush_author(out, current_name, current_org, current_country, current_email, current_corr)
                current_org = current_country = current_email = ""
            current_name = text.rstrip('*').strip()
            current_corr = '*' in text
            i += 1
            continue

        # Non-bold, non-email = affiliation line (org, country)
        if not current_org:
            parts = [x.strip().rstrip('.') for x in text.split(',')]
            if len(parts) >= 2:
                current_country = parts[-1]
                current_org = ', '.join(parts[:-1])
            else:
                current_org = text.rstrip('.')
        elif not current_country:
            current_country = text.rstrip('.')

        i += 1

    # Flush last author
    if current_name or current_email:
        _flush_author(out, current_name, current_org, current_country, current_email, current_corr)


def _flush_author(out, name, org, country, email, corr):
    """Add an author to the output dict."""
    if not name and not email:
        return
    # If no bold name was found, try to extract name from the org field
    # (some manuscripts have "FirstName LastName, Org, Country")
    if not name and org:
        parts = org.split(',')
        if len(parts) >= 2:
            name = parts[0].strip()
            org = ', '.join(parts[1:]).strip()
    out['authors'].append({
        'name': name or 'Unknown',
        'position': 'researcher',
        'organisation': org,
        'country': country,
        'email': email,
        'corresponding': corr,
    })


def _split_caption(text: str) -> tuple:
    """Split a caption that has body text run onto it.

    Converter exports produce lines like
      "Table 1: Dynamics of minimum charter capital requirements If we look at…"
    where a new sentence starts with no punctuation before it. Only applied to
    long, unpunctuated captions so real captions are left alone.
    """
    t = (text or '').strip()
    if len(t) <= 90 or t.endswith('.'):
        return t, ''
    m = re.search(r'(?<=[a-z\)\]])\s+(?=[A-Z][a-z]{2,})', t[30:])
    if not m:
        return t, ''
    cut = 30 + m.start()
    return t[:cut].strip(), t[cut:].strip()


def _extract_body(paras: list[dict], out: dict) -> None:
    """Extract sections, tables, figures, back matter, references."""

    sections = out['sections']
    back_matter = out['back_matter']
    references = out['references']

    current_root = None
    current_sub = None
    current_subsub = None
    current_target = None  # "section" | "back_matter:key" | "references"

    pending_table_caption = None  # (label, title)

    i = 0
    while i < len(paras):
        p = paras[i]

        # Tables from XML
        if p['_type'] == 'table':
            target = current_subsub or current_sub or current_root
            if target is not None and current_target == 'section':
                label = ""
                title = ""
                if pending_table_caption:
                    label, title = pending_table_caption
                    pending_table_caption = None
                target.setdefault('content', []).append({
                    'type': 'table',
                    'caption_label': label,
                    'caption_title': title,
                    'headers': p['headers'],
                    'rows': p['rows'],
                    'source': '',
                })
            i += 1
            continue

        text = p['text'].strip()
        if not text:
            i += 1
            continue

        # Style-based heading (Heading1/Heading2/...) with no numbering.
        # Converter exports carry all their structure here; without this the
        # whole body is dropped because no section is ever opened.
        lvl = p.get('style_level')
        if lvl is not None and not SECTION_RE.match(text) and len(text) < 200:
            key = text.lower().rstrip('.: ').strip()
            bm_key = BACK_MATTER_KEYS.get(key)
            if bm_key:
                current_target = 'references' if bm_key == 'references' else f'back_matter:{bm_key}'
                current_root = current_sub = current_subsub = None
                i += 1
                continue
            if key in FRONT_LABELS:
                i += 1
                continue
            current_target = 'section'
            depth = 1 if lvl <= 1 else min(lvl, 3)
            if depth == 1 or current_root is None:
                current_root = {'heading': text, 'paragraphs': [], 'subsections': [], 'content': []}
                current_sub = current_subsub = None
                sections.append(current_root)
            elif depth == 2:
                current_sub = {'heading': text, 'paragraphs': [], 'subsections': [], 'content': []}
                current_subsub = None
                current_root['subsections'].append(current_sub)
            else:
                if current_sub is None:
                    current_sub = {'heading': text, 'paragraphs': [], 'subsections': [], 'content': []}
                    current_subsub = None
                    current_root['subsections'].append(current_sub)
                else:
                    current_subsub = {'heading': text, 'paragraphs': [], 'content': []}
                    current_sub.setdefault('subsections', []).append(current_subsub)
            i += 1
            continue

        # Check for section heading.
        # ONLY bold paragraphs can create section headings. Non-bold numbered
        # lines like "1. Event Ingestion Layer" or "6. Feedback and Retraining"
        # inside a section are sub-step labels — they stay as body paragraphs.
        m = SECTION_RE.match(text)
        if m and p['bold']:
            num = m.group(1).rstrip('.')
            heading_text = text
            depth, num_val = _parse_section_num(m.group(1))

            # Check if it's back matter
            title_part = m.group(2).strip()
            bm_key = BACK_MATTER_KEYS.get(title_part.lower())
            if bm_key:
                if bm_key == 'references':
                    current_target = 'references'
                    current_root = current_sub = current_subsub = None
                else:
                    current_target = f'back_matter:{bm_key}'
                    current_root = current_sub = current_subsub = None
                i += 1
                continue

            current_target = 'section'
            # Detect if the document uses Roman numerals for top-level sections.
            # If so, Arabic-numbered bold items (1. Liquidity Risk) inside a Roman
            # section should be sub-sections, not new top-level sections.
            uses_roman = any(
                re.match(r'^[IVXLC]+\.', s['heading']) for s in sections
            )
            is_roman = bool(re.match(r'^[IVXLC]+$', num))
            is_arabic_inside_roman = (not is_roman and uses_roman and
                                      current_root is not None and depth == 1)

            if depth == 1 and not is_arabic_inside_roman:
                current_root = {'heading': heading_text, 'paragraphs': [], 'subsections': [], 'content': []}
                current_sub = current_subsub = None
                sections.append(current_root)
            elif (depth == 2 or is_arabic_inside_roman) and current_root is not None:
                current_sub = {'heading': heading_text, 'paragraphs': [], 'subsections': [], 'content': []}
                current_subsub = None
                current_root['subsections'].append(current_sub)
            elif depth >= 3 and current_sub is not None:
                current_subsub = {'heading': heading_text, 'paragraphs': [], 'content': []}
                current_sub['subsections'].append(current_subsub)
            elif depth >= 3 and current_root is not None:
                current_sub = {'heading': heading_text, 'paragraphs': [], 'subsections': [], 'content': []}
                current_subsub = None
                current_root['subsections'].append(current_sub)
            else:
                current_root = {'heading': heading_text, 'paragraphs': [], 'subsections': [], 'content': []}
                current_sub = current_subsub = None
                sections.append(current_root)
            i += 1
            continue

        # Skip author-inserted placeholder lines like "[Figure 1 — Insert figure here]"
        # These are redundant — the actual figure item is created from the
        # bold "Figure N. Caption" paragraph that follows.
        if FIGURE_PLACEHOLDER_RE.match(text):
            i += 1
            continue

        # Check for back-matter heading (non-numbered).
        # These may or may not be bold depending on the manuscript.
        bm_key_text = text.lower().rstrip('.:').strip()
        bm_key = BACK_MATTER_KEYS.get(bm_key_text)
        if bm_key and len(text) < 60:
            if bm_key == 'references':
                current_target = 'references'
            else:
                current_target = f'back_matter:{bm_key}'
            current_root = current_sub = current_subsub = None
            i += 1
            continue

        # Table caption: "Table 1. Title". It may sit before OR after its
        # table, and converter exports often glue the following sentence onto
        # it, so the caption is split from the body text here.
        tm = TABLE_CAPTION_RE.match(text)
        if tm:
            label = f"Table {tm.group(1)}."
            title, tail = _split_caption(tm.group(2).strip())
            target = current_subsub or current_sub or current_root
            attached = False
            if target is not None:
                content = target.get('content', [])
                if content and content[-1].get('type') == 'table' and not content[-1].get('caption_title'):
                    content[-1]['caption_label'] = label
                    content[-1]['caption_title'] = title
                    attached = True
            if not attached:
                pending_table_caption = (label, title)
            if tail and target is not None and current_target == 'section':
                target.setdefault('paragraphs', []).append(tail)
            i += 1
            continue

        # Figure caption: "Figure 1. Caption"
        fm = FIGURE_CAPTION_RE.match(text)
        if fm:
            fig_idx = int(fm.group(1))
            fig_sub = fm.group(2) or ""
            fig_caption = fm.group(3).strip().rstrip('.')
            # Check next line for source
            source = ""
            if i + 1 < len(paras) and paras[i+1]['_type'] == 'paragraph':
                nt = paras[i+1]['text'].strip()
                if nt.lower().startswith('source:'):
                    source = nt
                    i += 1
            target = current_subsub or current_sub or current_root
            if target is not None and current_target == 'section':
                target.setdefault('content', []).append({
                    'type': 'figure',
                    'label': f'Figure {fig_idx}{fig_sub}',
                    'caption': fig_caption,
                    'source': source,
                    'index': fig_idx,
                    'sub': fig_sub,
                    '_placeholder': True,
                })
            i += 1
            continue

        # Check for italic source line after a table
        if p['italic'] and text.lower().startswith('source:'):
            # Attach to the most recent table in the current section
            target = current_subsub or current_sub or current_root
            if target is not None:
                content = target.get('content', [])
                if content and content[-1].get('type') == 'table':
                    content[-1]['source'] = text
            i += 1
            continue

        # Bold non-numbered paragraph inside a section: treat as sub-heading.
        # E.g. "Stablecoins as Systemic Financial Infrastructure" under "I. Introduction"
        if (p['bold'] and current_target == 'section' and current_root is not None
                and len(text) > 15 and len(text) < 150
                and not SECTION_RE.match(text)
                and not FIGURE_CAPTION_RE.match(text)
                and not TABLE_CAPTION_RE.match(text)
                and not text.lower().startswith(('source:', 'note:', 'fig'))
                and not BACK_MATTER_KEYS.get(text.lower().rstrip('.:').strip())):
            current_sub = {'heading': text, 'paragraphs': [], 'subsections': [], 'content': []}
            current_subsub = None
            current_root['subsections'].append(current_sub)
            i += 1
            continue

        # Regular body content
        if current_target == 'references':
            # Each non-empty paragraph is one reference
            references.append(text)
        elif current_target and current_target.startswith('back_matter:'):
            key = current_target.split(':', 1)[1]
            existing = back_matter.get(key, '')
            back_matter[key] = (existing + ' ' + text).strip() if existing else text
        elif current_target == 'section':
            target = current_subsub or current_sub or current_root
            if target is not None:
                # Standalone equation paragraphs (<m:oMathPara>) — preserve raw OMML.
                # Paragraphs with inline math (<m:oMath> mixed with text runs) should
                # be kept as regular paragraphs — the math is part of the sentence.
                is_standalone_eq = p.get('has_math') and p.get('raw_math_xml') and \
                    'oMathPara' in (p.get('raw_math_xml') or '')
                if is_standalone_eq:
                    target.setdefault('content', []).append({
                        'type': 'equation',
                        'text': text,
                        'raw_math_xml': p['raw_math_xml'],
                    })
                else:
                    target.setdefault('paragraphs', []).append(text)
        i += 1
