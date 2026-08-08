"""
Fuse extracted figure images with the manuscript's figure placeholders.

The parser leaves `_placeholder: true` figure items inside sections
wherever a `[Figure N — Insert figure here]` callout was found.
This module assigns each extracted figure to its matching placeholder
(by inferred index), or appends the figure at the end of the manuscript
if no placeholder was found.
"""

from __future__ import annotations


def fuse_figures(manuscript: dict, figures: list[dict]) -> None:
    """
    Mutates `manuscript` in place:
      - For each figure with a matching placeholder (by `index`), copy
        the figure's `id` / `path` / dims onto the placeholder so the
        packer can attach an rId later.
      - Leftover figures (no placeholder) are appended as content to
        the last section of the manuscript.
    """
    if not figures:
        return

    # Index placeholder items by their `index` for quick lookup
    placeholders_by_idx: dict[int, dict] = {}
    for section in _iter_section_blocks(manuscript):
        for item in section.get("content", []):
            if item.get("type") == "figure" and item.get("_placeholder"):
                idx = item.get("index")
                if idx is not None and idx not in placeholders_by_idx:
                    placeholders_by_idx[idx] = item

    used_figure_ids: set[str] = set()
    for fig in figures:
        idx = fig.get("index")
        target = placeholders_by_idx.get(idx) if idx is not None else None
        if target is None:
            continue
        # Copy fields onto placeholder
        target["id"] = fig["id"]
        target["path"] = fig["path"]
        target["width"] = fig.get("width")
        target["height"] = fig.get("height")
        target["mime"] = fig.get("mime")
        target["skip_caption"] = fig.get("skip_caption", False)
        target["source"] = target.get("source") or fig.get("source", "")
        # If placeholder has no caption but fig has a label, fill from fig
        if not target.get("caption") and fig.get("label"):
            # Keep the label as-is; caption stays empty (the bold caption
            # line in the output will just read "Figure N.")
            pass
        # Prefer the placeholder's label (matches the text citation) but
        # ensure it's not empty
        if not target.get("label"):
            target["label"] = fig.get("label", "")
        used_figure_ids.add(fig["id"])

    # Any leftover figures: append at end of last section
    leftovers = [f for f in figures if f["id"] not in used_figure_ids]
    if leftovers:
        last = _last_section(manuscript)
        if last is not None:
            for f in leftovers:
                last.setdefault("content", []).append({
                    "type": "figure",
                    "id": f["id"],
                    "label": f.get("label", "Figure"),
                    "caption": "",
                    "source": f.get("source", ""),
                    "index": f.get("index"),
                    "sub": f.get("sub", ""),
                    "path": f["path"],
                    "width": f.get("width"),
                    "height": f.get("height"),
                    "mime": f.get("mime"),
                    "skip_caption": f.get("skip_caption", False),
                })


def _iter_section_blocks(manuscript: dict):
    """Yield every section-shaped dict (top, sub, subsub) in order."""
    for sec in manuscript.get("sections", []):
        yield sec
        for sub in sec.get("subsections", []):
            yield sub
            for subsub in sub.get("subsections", []):
                yield subsub


def _last_section(manuscript: dict) -> dict | None:
    sections = manuscript.get("sections")
    if not sections:
        return None
    return sections[-1]
