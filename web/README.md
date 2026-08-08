# JFDA Formatter — browser build

Runs the whole formatter in the browser. No server, no Docker, no Python
install. Publishable on GitHub Pages for free.

## How it works

The Python is not rewritten. `docx_parser.py`, `parser.py`,
`packer/builder.py`, `packer/metadata.py`, `citation_converter.py` and the
`office/` Word-XML helpers are the **same files the Flask server uses**,
running under Pyodide (CPython compiled to WebAssembly). That is what keeps the
Docker and browser versions in step — there is one implementation, not two.

Only the pieces that used to shell out to a Linux binary are replaced:

| Server | Browser |
| --- | --- |
| `pdftotext -layout` | pdf.js, with column gaps rebuilt from glyph positions |
| ImageMagick `identify` / `convert` | `Image` + `canvas.toBlob` |
| `unpack.py` / `pack.py` | in-memory `zipfile` (`pack_browser.py`) |
| LibreOffice → PDF preview | styled HTML from the same item stream |

Everything the template contains that we do not edit is copied across
byte-for-byte, so styles, numbering, fonts, headers and settings survive
exactly. Only `word/document.xml`, `word/_rels/document.xml.rels`,
`[Content_Types].xml` and any added `word/media/*` differ from the original.

## Files

```
JFDA Formatter.dc.html   the app
web/jfda-runtime.js      Pyodide boot + the browser replacements above
web/py/                  the server's Python modules, unchanged
web/py/pack_browser.py   in-memory replacement for packer/__init__.py
web/py/bridge.py         the API JavaScript calls
web/template/            template.docx
support.js               component runtime
_ds/                     design system
```

## Publishing to GitHub Pages

Copy into a `docs/` folder in a public repo:

- `JFDA Formatter.dc.html`, renamed **`index.html`**
- `support.js`
- `web/` (whole folder)
- `_ds/` (whole folder)

Then Settings → Pages → Deploy from a branch → `main`, folder `/docs`.

Pyodide itself loads from a CDN (about 12 MB, cached after first visit), so
none of it needs committing.

## Known limits

**First load takes a few seconds.** Pyodide has to download and start. It is
cached afterwards.

**Figures from PDFs are page renders, not cropped images.** pdf.js can list
embedded image objects but not reliably reconstruct them, so any page carrying
an image is rendered whole at 2× and offered as a figure. An editor crops in
Word. DOCX figures are extracted properly, as real embedded images.

**TIFF, EMF and WMF images are skipped.** Browsers cannot decode them, and
there is no ImageMagick. PNG, JPEG, GIF, BMP and WebP all work; the last three
are converted to PNG on the way in.

**Equations.** Word's native OMML survives DOCX → DOCX because the run XML is
carried through. Equations that arrive as images are treated as figures.
LaTeX typed as plain text stays plain text — converting it would need a
rendering step that is not built.

**The preview is HTML, not Word.** It follows the same ordering and numbering
rules as the .docx because it is generated from the same item stream, but line
breaks and page breaks will differ. Download the .docx to see the real thing.

## Keeping it in step with the Docker version

`web/py/` holds copies. After changing a module in `jfda_app/app/`, copy it
across:

```powershell
$src = "C:\Users\letua\Downloads\jfda_app\app"
$dst = "C:\Users\letua\Downloads\jfda-site\docs\web\py"

Copy-Item "$src\docx_parser.py"        "$dst\"        -Force
Copy-Item "$src\parser.py"             "$dst\"        -Force
Copy-Item "$src\citation_converter.py" "$dst\"        -Force
Copy-Item "$src\figure_fusion.py"      "$dst\"        -Force
Copy-Item "$src\packer\builder.py"     "$dst\packer\" -Force
Copy-Item "$src\packer\metadata.py"    "$dst\packer\" -Force
```

Two exceptions that must **not** be overwritten, because they are the browser
replacements: `pack_browser.py` and `bridge.py`. And `packer/metadata.py` in
the browser copy has one addition — `apply_metadata_str()`, a string-in
string-out version of `apply_metadata()`. If you re-copy that file from the
server, re-add it, or the build breaks.
