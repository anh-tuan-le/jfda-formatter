# Publishing the browser formatter

Everything in this folder is the finished website: `index.html` is the
formatter, `about.html` describes it, and `web/` and `_ds/` are what they load.

Your GitHub account is **anh-tuan-le**, so the published address will be:

```
https://anh-tuan-le.github.io/jfda-formatter/
```

That is the link you send to editors. No card, no hosting bill.

---

## Step 1 — try it locally first

Don't publish something you haven't seen working. The page loads files with
`fetch`, which browsers block when you open an HTML file directly from disk, so
you need a local server for one minute:

```powershell
cd "C:\Users\letua\Downloads\jfda-site"
python -m http.server 8000
```

Open **http://localhost:8000** and drop a real manuscript in. Give it a few
seconds on first load — it's downloading the Python runtime.

Press Ctrl+C in PowerShell when you're done.

If a manuscript comes out wrong, say so before publishing — fixing it now is
easier than after editors have the link.

## Step 2 — create the repository

Go to **https://github.com/new**

- Owner: `anh-tuan-le`
- Repository name: `jfda-formatter`
- **Public** — Pages on a private repo needs a paid plan
- Don't tick README, .gitignore or licence

## Step 3 — push the site

```powershell
cd "C:\Users\letua\Downloads\jfda-site"

git init
git add .
git commit -m "JFDA manuscript formatter"
git branch -M main
git remote add origin https://github.com/anh-tuan-le/jfda-formatter.git
git push -u origin main
```

A browser window will ask you to sign in — GitHub stopped accepting account
passwords on the command line.

If git isn't installed: `winget install Git.Git`, then reopen PowerShell.

## Step 4 — turn Pages on

Go to **https://github.com/anh-tuan-le/jfda-formatter/settings/pages**

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/ (root)**
- **Save**

Wait two or three minutes, then reload that page. Your URL appears at the top.

## Step 5 — check the published version

Open **https://anh-tuan-le.github.io/jfda-formatter/** in a private window (so
you're not seeing a cached copy) and run one manuscript through.

If the page loads but nothing happens when you drop a file, press F12 and look
at the Console tab — a missing file shows up as a 404 there and names exactly
which one.

---

## Updating later

```powershell
cd "C:\Users\letua\Downloads\jfda-site"
git add .
git commit -m "what changed"
git push
```

Live in a minute or two. Ctrl+F5 to see it.

---

## What to tell your editors

**Nothing is uploaded.** The manuscript is parsed and rebuilt inside their own
browser — it never reaches a server, yours or GitHub's. For people handling
unpublished submissions this is the thing worth saying first, and unlike the
Docker version it's now literally true rather than a promise about deletion.

**First load takes a few seconds.** It's fetching the Python runtime, about
12 MB. After that it's cached and opens instantly.

**AI features need their own API key.** Each editor pastes their own key into
the AI panel; it's stored in their browser and calls go straight to
Anthropic/OpenAI/Google. You are not paying for their usage. Parsing,
formatting and .docx export all work without a key.

---

## Keeping this in step with your Docker version

`web/py/` holds copies of the server's Python modules. After changing one in
`jfda_app\app\`, copy it across:

```powershell
$src = "C:\Users\letua\Downloads\jfda_app\app"
$dst = "C:\Users\letua\Downloads\jfda-site\web\py"

Copy-Item "$src\docx_parser.py"        "$dst\"        -Force
Copy-Item "$src\parser.py"             "$dst\"        -Force
Copy-Item "$src\citation_converter.py" "$dst\"        -Force
Copy-Item "$src\figure_fusion.py"      "$dst\"        -Force
Copy-Item "$src\packer\builder.py"     "$dst\packer\" -Force
```

Two files must **never** be overwritten from the server copy, because they are
the browser replacements: `pack_browser.py` and `bridge.py`.

And `packer\metadata.py` in the browser copy has one addition —
`apply_metadata_str()`, a string-in string-out version of `apply_metadata()`.
If you re-copy that file from the server, re-add it or the build breaks. That's
why it isn't in the list above.
