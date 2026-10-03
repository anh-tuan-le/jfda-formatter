/* JFDA browser runtime — boots Pyodide and exposes the same operations the
   Flask server used to. The Python modules it loads are the server's own
   files, unchanged, so behaviour stays in step between the two versions.

   Replaced shell tools:
     pandoc / direct docx XML  -> docx_parser.py, already pure Python
     pdftotext                 -> pdf.js, below
     ImageMagick identify      -> Image element, below
     ImageMagick convert       -> canvas.toBlob, below
     LibreOffice PDF preview   -> styled HTML from bridge.build_preview_html
*/

// Bump on every change to the Python modules. Without it the browser and the
// GitHub Pages CDN keep serving the previous copies after a push, so fixes
// appear to have no effect.
const BUILD = '2026-10-04-2';

const PYODIDE_VERSION = '0.26.4';
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const PDFJS_URL = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.6.82/build/';

const PY_FILES = [
  'docx_parser.py',
  'parser.py',
  'citation_converter.py',
  'figure_fusion.py',
  'pack_browser.py',
  'bridge.py',
  'packer/builder.py',
  'packer/metadata.py',
  'office/__init__.py',
  'office/merge_runs.py',
  'office/simplify_redlines.py',
];

// Canvas can decode these natively; anything else Word may reject.
const CANVAS_SAFE = /^(png|jpe?g|gif|bmp|webp)$/i;

function loadScript(src) {
  return new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = src; s.onload = res; s.onerror = () => rej(new Error('failed to load ' + src));
    document.head.appendChild(s);
  });
}

export class JFDA {
  constructor({ base = 'web/', onProgress = () => {} } = {}) {
    this.base = base.endsWith('/') ? base : base + '/';
    this.onProgress = onProgress;
    this.py = null;
    this.template = null;
    this._booting = null;
  }

  boot() {
    if (!this._booting) this._booting = this._boot();
    return this._booting;
  }

  async _boot() {
    this.onProgress('Loading Python runtime…');
    await loadScript(PYODIDE_URL + 'pyodide.js');
    this.py = await loadPyodide({ indexURL: PYODIDE_URL });

    this.onProgress('Loading formatter…');
    const dir = this.base + 'py/';
    this.py.FS.mkdirTree('/jfda/packer');
    this.py.FS.mkdirTree('/jfda/office');
    await Promise.all(PY_FILES.map(async (name) => {
      const r = await fetch(dir + name + '?v=' + BUILD);
      if (!r.ok) throw new Error(`missing ${dir}${name} (HTTP ${r.status})`);
      this.py.FS.writeFile('/jfda/' + name, await r.text());
    }));
    this.py.runPython('import sys; sys.path.insert(0, "/jfda")');
    this.bridge = this.py.pyimport('bridge');

    this.onProgress('Loading template…');
    const t = await fetch(this.base + 'template/template.docx?v=' + BUILD);
    if (!t.ok) throw new Error('template.docx not found');
    this.template = new Uint8Array(await t.arrayBuffer());
    if (this.template.length < 30000) {
      throw new Error('template.docx looks truncated — ' + this.template.length + ' bytes');
    }

    this.onProgress('');
    return this;
  }

  /* ---------------- input ---------------- */

  async parseFile(file) {
    await this.boot();
    const ext = (file.name.split('.').pop() || '').toLowerCase();

    if (ext === 'docx') {
      const buf = new Uint8Array(await file.arrayBuffer());
      const m = this.bridge.parse_docx(buf).toJs({ dict_converter: Object.fromEntries });
      m._figures = await this._figuresFromDocx(buf);
      return m;
    }
    if (ext === 'pdf') {
      const text = await this._pdfText(file);
      const m = this.bridge.parse_text(text).toJs({ dict_converter: Object.fromEntries });
      m._figures = await this._figuresFromPdf(file);
      return m;
    }
    if (ext === 'pptx') {
      const buf = new Uint8Array(await file.arrayBuffer());
      return this.bridge.parse_pptx(buf).toJs({ dict_converter: Object.fromEntries });
    }
    if (ext === 'json') {
      return this.bridge.parse_json(await file.text()).toJs({ dict_converter: Object.fromEntries });
    }
    if (ext === 'html' || ext === 'htm') {
      return this.bridge.parse_html(await file.text()).toJs({ dict_converter: Object.fromEntries });
    }
    // md, markdown, txt, csv and anything else text-shaped
    return this.bridge.parse_text(await file.text()).toJs({ dict_converter: Object.fromEntries });
  }

  /* pdftotext -layout replacement. pdf.js gives positioned items rather than
     lines, so we rebuild lines by y-coordinate and insert a gap where the x
     jump is wide enough to have been a column — that is what -layout did and
     what the markdown parser downstream expects. */
  async _pdfText(file) {
    const pdfjs = await this._pdfjs();
    const doc = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
    const pages = [];
    for (let p = 1; p <= doc.numPages; p++) {
      const content = await (await doc.getPage(p)).getTextContent();
      const rows = new Map();
      for (const it of content.items) {
        if (!it.str) continue;
        const y = Math.round(it.transform[5]);
        let bucket = null;
        for (const key of rows.keys()) { if (Math.abs(key - y) <= 2) { bucket = key; break; } }
        if (bucket === null) { bucket = y; rows.set(y, []); }
        rows.get(bucket).push({ x: it.transform[4], s: it.str, w: it.width });
      }
      const lines = [...rows.entries()]
        .sort((a, b) => b[0] - a[0])
        .map(([, items]) => {
          items.sort((a, b) => a.x - b.x);
          let out = '', prevEnd = null;
          for (const it of items) {
            if (prevEnd !== null) {
              const gap = it.x - prevEnd;
              if (gap > 12) out += '   ';
              else if (gap > 1.2 && !/\s$/.test(out)) out += ' ';
            }
            out += it.s;
            prevEnd = it.x + (it.w || 0);
          }
          return out.replace(/\s+$/, '');
        });
      pages.push(lines.join('\n'));
    }
    return pages.join('\n\n');
  }

  async _pdfjs() {
    if (this._pdfjsLib) return this._pdfjsLib;
    await loadScript(PDFJS_URL + 'pdf.min.mjs').catch(async () => {
      await loadScript(PDFJS_URL + 'pdf.min.js');
    });
    const lib = window.pdfjsLib || globalThis.pdfjsLib;
    if (!lib) throw new Error('pdf.js failed to load');
    lib.GlobalWorkerOptions.workerSrc = PDFJS_URL + 'pdf.worker.min.js';
    this._pdfjsLib = lib;
    return lib;
  }

  /* ---------------- figures ---------------- */

  async _figuresFromDocx(buf) {
    const raw = this.bridge.extract_docx_images(buf).toJs({ dict_converter: Object.fromEntries });
    const out = [];
    for (let i = 0; i < raw.length; i++) {
      const f = raw[i];
      const norm = await this._normaliseImage(new Uint8Array(f.data), f.ext);
      if (norm) out.push({ id: 'fig' + (i + 1), name: f.name, ...norm });
    }
    return out;
  }

  /* Rasterise each page that carries images. pdf.js can list embedded image
     objects but not reliably reconstruct them; rendering the page is the
     dependable route, and an editor can crop in Word. */
  async _figuresFromPdf(file) {
    const pdfjs = await this._pdfjs();
    const doc = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise;
    const out = [];
    for (let p = 1; p <= doc.numPages; p++) {
      const page = await doc.getPage(p);
      const ops = await page.getOperatorList();
      const hasImage = ops.fnArray.some(fn =>
        fn === pdfjs.OPS.paintImageXObject || fn === pdfjs.OPS.paintInlineImageXObject);
      if (!hasImage) continue;
      const viewport = page.getViewport({ scale: 2 });
      const canvas = document.createElement('canvas');
      canvas.width = viewport.width; canvas.height = viewport.height;
      await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
      const blob = await new Promise(r => canvas.toBlob(r, 'image/png'));
      out.push({
        id: 'pdffig' + p,
        name: `page-${p}.png`,
        ext: 'png',
        data: new Uint8Array(await blob.arrayBuffer()),
        width: canvas.width,
        height: canvas.height,
        page: p,
      });
    }
    return out;
  }

  /* ImageMagick replacement: measure, and convert exotic formats to PNG. */
  async _normaliseImage(bytes, ext) {
    ext = (ext || 'png').toLowerCase();
    if (!CANVAS_SAFE.test(ext)) return null;   // tiff/emf/wmf: browser can't decode
    const blob = new Blob([bytes]);
    const url = URL.createObjectURL(blob);
    try {
      const img = await new Promise((res, rej) => {
        const i = new Image();
        i.onload = () => res(i); i.onerror = () => rej(new Error('undecodable image'));
        i.src = url;
      });
      if (ext === 'png' || ext === 'jpg' || ext === 'jpeg') {
        return { ext: ext === 'jpeg' ? 'jpg' : ext, data: bytes,
                 width: img.naturalWidth, height: img.naturalHeight };
      }
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
      canvas.getContext('2d').drawImage(img, 0, 0);
      const png = await new Promise(r => canvas.toBlob(r, 'image/png'));
      return { ext: 'png', data: new Uint8Array(await png.arrayBuffer()),
               width: canvas.width, height: canvas.height };
    } catch {
      return null;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  /* ---------------- output ---------------- */

  async buildDocx(manuscript) {
    await this.boot();
    const payload = { ...manuscript };
    payload.back_matter = { ...(manuscript.back_matter || {}) };
    for (const k of ['data_availability', 'funding', 'acknowledgements', 'gen_ai', 'conflicts']) if (k in manuscript) payload.back_matter[k] = manuscript[k] || '';
    delete payload._figures;
    payload.figures = (manuscript.figures || []).map(f => ({
      ...f, data: f.data ? this._b64(f.data) : null,
    }));
    const bytes = this.bridge.build_docx(this.template, JSON.stringify(payload));
    return new Blob([bytes.toJs()], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
  }

  async previewHtml(manuscript) {
    await this.boot();
    const payload = { ...manuscript };
    payload.back_matter = { ...(manuscript.back_matter || {}) };
    for (const k of ['data_availability', 'funding', 'acknowledgements', 'gen_ai', 'conflicts']) if (k in manuscript) payload.back_matter[k] = manuscript[k] || '';
    delete payload._figures; delete payload.figures;
    return this.bridge.build_preview_html(JSON.stringify(payload));
  }

  _b64(u8) {
    let s = '';
    const chunk = 0x8000;
    for (let i = 0; i < u8.length; i += chunk) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + chunk));
    }
    return btoa(s);
  }
}
