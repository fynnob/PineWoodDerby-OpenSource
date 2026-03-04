# GitHub Pages — Manual Deploy Guide

This guide walks you through hosting the Pinewood Derby frontend on GitHub Pages
**without** using the auto-deploy wizard flow.

You only need this if:
- You skipped entering a GitHub token in the wizard, **or**
- You want to maintain the Pages site yourself, **or**
- The auto-deploy step failed

---

## What gets hosted

The `frontend/` folder contains all the static HTML/CSS/JS.  
GitHub Pages will serve it at:
```
https://<your-username>.github.io/<repo-name>/
```

---

## Step 1 — Create a GitHub repository

1. Go to <https://github.com/new>
2. Name it whatever you like (e.g. `my-derby-2026`)
3. Set it to **Public**
4. Leave "Add a README" **unchecked** — you'll push files yourself
5. Click **Create repository**

---

## Step 2 — Generate `frontend/config.js`

Run this on your Pi / PC (where the repo is cloned):

```bash
python3 setup.py
```

Complete the wizard and choose **Supabase** as your backend.  
This generates `frontend/config.js` with your Supabase URL and anon key.

> **Note:** `frontend/config.js` is in `.gitignore` so it only lives locally —
> you need to copy it into the Pages repo yourself (Step 3).

---

## Step 3 — Push the frontend to your new repo

```bash
# From inside the PineWoodDerby-OpenSource folder:
cd /tmp
mkdir derby-pages && cd derby-pages

# Copy frontend files
cp -r /path/to/PineWoodDerby-OpenSource/frontend/. .

# Init and push
git init -b main
git add .
git commit -m "Deploy: Pinewood Derby frontend"
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

Replace `/path/to/PineWoodDerby-OpenSource` with the actual path on your machine.

---

## Step 4 — Enable GitHub Pages

1. Open your new repo on GitHub
2. Go to **Settings → Pages**
3. Under **Source**, select:
   - Branch: `main`
   - Folder: `/ (root)`
4. Click **Save**

GitHub will show a banner:
> *"Your site is ready to be published at `https://…github.io/…`"*

It usually goes live within **1–2 minutes**.

---

## Step 5 — Test it

Open your Pages URL:
```
https://<your-username>.github.io/<repo-name>/
```

You should see the Pinewood Derby home screen.  
If the admin pages can't reach the backend, double-check the `backend_url` in
`frontend/config.js` — it should be your Supabase project URL.

---

## Updating after config changes

If you re-run `python3 setup.py` and change your Supabase keys or URL,
re-copy `frontend/config.js` into your Pages repo and push:

```bash
cd /tmp/derby-pages
cp /path/to/PineWoodDerby-OpenSource/frontend/config.js .
git add config.js
git commit -m "chore: update config"
git push
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Blank page | Check browser console — likely a `config.js` 404 |
| API calls fail | Check `backend_url` in `config.js` points to Supabase |
| 404 on sub-pages | Make sure you pushed the full `frontend/` contents, not a subfolder |
| Pages not enabling | Repo must be **Public**; Pages requires a `main` branch |
