# Publishing the browser formatter

Everything in this `site` folder is the finished website. It's already
assembled — `index.html` is the formatter, `about.html` describes it, and the
`web/` and `_ds/` folders are what they load.

You need a GitHub account. Nothing else, no card, no hosting bill.

---

## Step 1 — try it locally first

Don't publish something you haven't seen working. The page loads files with
`fetch`, which browsers block when you open an HTML file directly from disk, so
you need a local server for one minute:

```powershell
cd "C:\Users\letua\Downloads\site"
python -m http.server 8000
```

Open **http://localhost:8000** and drop a real manuscript in. Give it a few
seconds on first load — it's downloading the Python runtime.

Press Ctrl+C in PowerShell when you're done.

If a manuscript comes out wrong, tell me what it did before publishing —
fixing it now is easier than after editors have the link.

## Step 2 — create the repository

On github.com → **New repository**.

- Name: `jfda-formatter`
- **Public** — Pages on a private repo needs a paid plan
- Don't tick README, .gitignore or licence

## Step 3 — push the site

```powershell
$site = "C:\Users\letua\Downloads\site"
cd $site

git init
git add .
git commit -m "JFDA formatter"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/jfda-formatter.git
git push -u origin main
```

Replace `YOUR-USERNAME`. A browser window will ask you to sign in — GitHub
stopped accepting account passwords on the command line.

If git isn't installed: `winget install Git.Git`, then reopen PowerShell.

## Step 4 — turn Pages on

Repository → **Settings** → **Pages** in the left sidebar.

- Source: **Deploy from a branch**
- Branch: **main**, folder: **/ (root)**
- **Save**

Wait two or three minutes, then reload that Settings page. Your URL appears at
the top:

```
https://YOUR-USERNAME.github.io/jfda-formatter/
```

That's the link you send to editors.

## Step 5 — check the published version

Open the URL in a private window (so you're not seeing a cached copy) and run
one manuscript through. If the page loads but nothing happens when you drop a
file, press F12 and look at the Console tab — a missing file shows up as a 404
there and tells you exactly which one.

---

## Updating later

```powershell
cd C:\Users\letua\Downloads\site
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

**AI features need their own API key.** Not yet wired into this build — the
formatting, parsing and .docx export all work without one.

---

## Two things not yet carried over

The AI panel and the citation tools from the Docker version aren't in this
build yet. The Python for them is already here (`web/py/citation_converter.py`),
so it's UI work rather than logic — say the word and I'll add the panel back.

Your Docker version still runs locally and still has them.
