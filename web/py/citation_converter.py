"""
Convert numbered in-text citations [1], [2,3] to APA 7 format (Author, Year)
and clean up the reference list.
"""

from __future__ import annotations

import re


def convert_citations_to_apa7(manuscript: dict) -> None:
    """
    Mutate manuscript in-place:
    1. Strip [N] prefixes from references
    2. Build a number → (Author, Year) lookup
    3. Replace [N] citations in all body text with APA 7 format
    """
    refs = manuscript.get("references", [])
    if not refs:
        return

    # Step 1: Clean references and build lookup
    lookup: dict[int, str] = {}  # {1: "(Bolton & Hand, 2002)", ...}
    cleaned_refs: list[str] = []
    numbered_any = False

    for position, ref in enumerate(refs, start=1):
        ref = ref.strip()
        # Strip leading [N] or N.
        m = re.match(r'^\[?(\d+)\]?\s*\.?\s*', ref)
        if m:
            num = int(m.group(1))
            clean = ref[m.end():].strip()
            numbered_any = True
        else:
            # Word list-numbered bibliographies (ListParagraph + numbering.xml)
            # carry no number in the text at all — the list position IS the
            # citation number, so fall back to it.
            num = position
            clean = ref

        cleaned_refs.append(clean)

        if num is not None:
            # Parse APA-style author-year from the reference
            apa_cite = _extract_apa_cite(clean)
            if apa_cite:
                lookup[num] = apa_cite

    manuscript["references"] = sorted(cleaned_refs, key=lambda r: r.lower())

    # Step 2: Replace [N] citations in body text
    if not lookup:
        return

    manuscript["_citation_map"] = {
        str(n): cite for n, cite in sorted(lookup.items())
    }
    manuscript["_citation_numbering"] = "explicit" if numbered_any else "list-order"

    def replace_in_text(text: str) -> str:
        if not text or "[" not in text:
            return text
        return _replace_citations(text, lookup)

    # Walk all sections, subsections, back_matter
    for sec in manuscript.get("sections", []):
        sec["paragraphs"] = [replace_in_text(p) for p in sec.get("paragraphs", [])]
        for sub in sec.get("subsections", []):
            sub["paragraphs"] = [replace_in_text(p) for p in sub.get("paragraphs", [])]
            for subsub in sub.get("subsections", []):
                subsub["paragraphs"] = [replace_in_text(p) for p in subsub.get("paragraphs", [])]

    # Also replace in abstract
    if manuscript.get("abstract"):
        manuscript["abstract"] = replace_in_text(manuscript["abstract"])

    # Back matter
    for key, val in manuscript.get("back_matter", {}).items():
        if val:
            manuscript["back_matter"][key] = replace_in_text(val)


# A plausible publication year, not part of an identifier like PF-6079 or ZRQ-578.
YEAR_RE = re.compile(r'(?<![\w\-\u2013\u2014\u2116#])(1[5-9]\d{2}|20\d{2})(?![\w\-])')

# Legal / regulatory sources: APA cites these by short title + year, e.g.
#   "Decree of the President ... No. PF-6079 dated October 5, 2020 ..."
#     -> (Decree No. PF-6079, 2020)
LEGAL_KINDS = ('decree', 'law', 'resolution', 'order', 'code', 'regulation',
               'directive', 'act', 'ruling', 'statute')
DOC_NUM_RE = re.compile(r'\bNo\.?\s*([A-Z\u0410-\u042f]{1,5}[\-\u2013]?\s?[\d\-]+)', re.IGNORECASE)


def _shorten_corporate(name: str, max_words: int = 4) -> str:
    """Long institutional/title 'authors' are cut to their first few words so
    the in-text citation stays readable (APA allows a shortened form)."""
    words = [w for w in name.split() if w]
    if len(words) <= max_words:
        return ' '.join(words)
    return ' '.join(words[:max_words])


def _legal_cite(ref_text: str) -> str | None:
    """In-text citation for a legal instrument, or None if it isn't one."""
    low = ref_text.lower().lstrip('"\u201c ')
    kind = next((k for k in LEGAL_KINDS if low.startswith(k)), None)
    if not kind:
        return None
    years = YEAR_RE.findall(ref_text)
    year = years[-1] if years else 'n.d.'
    num = DOC_NUM_RE.search(ref_text)
    label = kind.capitalize()
    if num:
        return f"({label} No. {num.group(1).strip()}, {year})"
    # No document number: use the first few words of the title instead.
    return f"({_shorten_corporate(ref_text.strip().rstrip('.'), 5)}, {year})"


def _extract_apa_cite(ref_text: str) -> str | None:
    """
    Extract APA 7 in-text citation from a reference entry.

    Input:  "Bolton, R. J., & Hand, D. J. (2002). Statistical fraud detection..."
    Output: "(Bolton & Hand, 2002)"

    Input:  "Apache Software Foundation. (2021). Apache Kafka documentation..."
    Output: "(Apache Software Foundation, 2021)"

    Input:  "Decree of the President ... No. PF-6079 dated October 5, 2020 ..."
    Output: "(Decree No. PF-6079, 2020)"
    """
    ref_text = (ref_text or '').strip()
    if not ref_text:
        return None

    legal = _legal_cite(ref_text)
    if legal:
        return legal

    # Prefer a parenthesised year, which is where APA puts it.
    m = re.search(r'\((\d{4}[a-z]?)\)', ref_text)
    if m:
        year = m.group(1)
        author_part = ref_text[:m.start()].strip().rstrip('.(,')
    else:
        # Otherwise the first plausible year anywhere in the entry. Identifier
        # digits (PF-6079, ZRQ-578, No. 3229-2) are excluded by YEAR_RE, which
        # is what previously produced nonsense like "(... No. PF-, 6079)".
        m = YEAR_RE.search(ref_text)
        if not m:
            return None
        year = m.group(1)
        author_part = ref_text[:m.start()].strip().rstrip('.(,\u2013\u2014- ')
        # Guard: if the "author" ran into the title, keep only the leading part
        # up to the first sentence break.
        author_part = re.split(r'(?<=[a-z])\.\s+(?=[A-Z])|\s+//\s+', author_part)[0].strip()

    authors = _parse_author_names(author_part)
    if not authors:
        return None

    if len(authors) == 1:
        return f"({_shorten_corporate(authors[0])}, {year})"
    elif len(authors) == 2:
        return f"({authors[0]} & {authors[1]}, {year})"
    else:
        return f"({authors[0]} et al., {year})"


def _parse_author_names(author_str: str) -> list[str]:
    """
    Extract last names from an author string.

    "Bolton, R. J., & Hand, D. J." → ["Bolton", "Hand"]
    "Apache Software Foundation" → ["Apache Software Foundation"]
    """
    author_str = author_str.strip().rstrip(',.')
    if not author_str:
        return []

    # Check for corporate/organization author (no comma-separated initials)
    if ',' not in author_str:
        return [author_str.strip()]

    # Split by " & " or ", &" to separate authors
    # "Bolton, R. J., & Hand, D. J." → ["Bolton, R. J.", "Hand, D. J."]
    parts = re.split(r',?\s*&\s*', author_str)

    last_names = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Each part might be "LastName, Initials" or "LastName, Initials, LastName2, Initials2"
        # Split further by comma: "Bolton, R. J." → ["Bolton", "R. J."]
        sub = [s.strip() for s in part.split(',')]
        # First element after each comma pair is a last name, second is initials
        i = 0
        while i < len(sub):
            name = sub[i].strip().rstrip('.')
            if name and not re.match(r'^[A-Z]\.\s*[A-Z]?\.?$', name) and len(name) > 2:
                # This looks like a last name (not just initials)
                last_names.append(name)
                i += 2  # Skip the initials
            else:
                i += 1

    return last_names if last_names else [author_str.strip()]


def _replace_citations(text: str, lookup: dict[int, str]) -> str:
    """
    Replace [N] and [N,M,...] citation markers with APA 7 format.

    [1] → (Bolton & Hand, 2002)
    [1, 2] → (Bolton & Hand, 2002; Kreps, 2014)
    [6], [7] → (Author1, Year; Author2, Year)  — merged into one bracket
    """
    # First, merge consecutive [N], [M] or [N] [M] into [N,M]
    text = re.sub(r'\](\s*,?\s*)\[', lambda m: ',', text)

    def replacer(m):
        nums_str = m.group(1)
        nums = [int(n.strip()) for n in nums_str.split(',') if n.strip().isdigit()]
        cites = []
        for n in nums:
            if n in lookup:
                cite = lookup[n].strip('()')
                cites.append(cite)
            else:
                cites.append(f"[{n}]")

        if cites:
            return f"({'; '.join(cites)})"
        return m.group(0)

    # Match [N], [N,M], [N, M, ...] patterns
    result = re.sub(r'\[(\d+(?:\s*,\s*\d+)*)\]', replacer, text)

    # Also handle ranges like [1-3] → expand to [1,2,3] then replace
    def range_replacer(m):
        start, end = int(m.group(1)), int(m.group(2))
        nums = list(range(start, end + 1))
        cites = []
        for n in nums:
            if n in lookup:
                cite = lookup[n].strip('()')
                cites.append(cite)
            else:
                cites.append(f"[{n}]")
        return f"({'; '.join(cites)})"

    result = re.sub(r'\[(\d+)\s*[-–]\s*(\d+)\]', range_replacer, result)

    return result
