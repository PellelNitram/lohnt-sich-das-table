# Lohnt sich das? – Tabelle

A public, community-sourced salary table (profession, gross and net monthly income) with an open static JSON/CSV API. Inspired by the "Lohnt sich das?" video format.

```
Google Form ──▶ Google Sheet ──▶ (manual: fill `approved`) ──▶ GitHub Actions ──▶ GitHub Pages
                                                              weekly + on demand   static site + /api/v1/
```

## How it works

1. People submit via a Google Form; answers land in a Google Sheet.
2. Every few weeks, the maintainer reviews new rows and enters a date in the `approved` column. **Rows without a valid, non-future date are never rendered** – this keeps bot submissions off the site.
3. A GitHub Actions workflow runs every Monday (and on demand) and builds `dist/` with `scripts/build_site.py` from the sheet's published CSV, then deploys to GitHub Pages.
4. Only whitelisted fields are published. The submission timestamp and any other form columns (e.g. e-mail) are dropped.

## Local development

```sh
make          # list targets
make serve    # build from data/sample.csv (fake data) and serve on http://localhost:8000
make test
```

To build from the real sheet locally, put `SHEET_CSV_URL=...` (and optionally `FORM_URL`, `FEEDBACK_FORM_URL`, `KOFI_URL`, `BLOG_URL`, `ABOUT_VIDEO_URL`, `GOATCOUNTER_URL`, `SITE_URL`) in a git-ignored `.env` and run `make serve-live`. The script reads `.env` itself, so no quoting is needed; real environment variables take precedence.

**Local and CI run the same commands.** The workflow runs `make test` and `make build-live`, nothing else. Locally, the same commands read `.env` instead of GitHub's secrets. If CI breaks, reproduce it with `make test && make build-live`.

## Sheet format

First row must contain these headers (extra columns are ignored):

`submitted_at`, `video_url`, `video_date`, `profession`, `gross_monthly_income_eur`, `net_monthly_income_eur`, `gross_income_proof_url`, `net_income_proof_url`, `approved`

Google Forms names its first column `Timestamp`/`Zeitstempel`; rename the header cells in the sheet. Renaming a header in the sheet does not change the Form.
`approved` takes `YYYY-MM-DD` or `DD.MM.YYYY`. Numbers may be written `3500`, `3.500` or `3.500,50`.

`video_url`, `video_date` and `profession` are required; at least one income is required; proof links are optional. Invalid approved rows (e.g. net above gross, non-YouTube video, `http://` or other non-https links; links without a scheme get `https://` added) are skipped and listed as warnings in the build log.

## One-time setup

1. **Sheet**: File → Share → Publish to web → the answers tab → CSV. Copy the URL. Anyone with this link can read the sheet, so keep it out of the repo.
2. **Repo secrets/variables** (Settings → Secrets and variables → Actions):
   - secret `SHEET_CSV_URL` (required)
   - variables (all optional; each hides its feature when unset):
     - `FORM_URL`: submit button
     - `FEEDBACK_FORM_URL`: link to a free-text feedback form (section + footer)
     - `KOFI_URL`: Ko-fi support link (section + footer)
     - `BLOG_URL`: blog link in header and footer
     - `ABOUT_VIDEO_URL`: YouTube link for the "Wie es dazu kam" section
     - `GOATCOUNTER_URL`: visitor counter, e.g. `https://<name>.goatcounter.com/count`
     - `SITE_URL`: absolute site URL for the sitemap and Open Graph tags; defaults to the domain in `CNAME`
3. **Pages**: Settings → Pages → Source: *GitHub Actions*.
4. **Custom domain**: add a file named `CNAME` containing only the domain (e.g. `example.org`) to the repo root, create the DNS `CNAME` record pointing to `<user>.github.io`, then enable *Enforce HTTPS* in Pages settings. The build copies `CNAME` into `dist/`.

## API

Data version 1. Defined in [schema/schema.json](schema/schema.json), documented in [docs/api.md](docs/api.md). Endpoints: `/api/v1/data.json`, `/api/v1/data.csv`, `/api/v1/schema.json`.

## Layout

```
scripts/build_site.py   build pipeline (stdlib only)
src/                    page template and static assets (incl. og-image.png for link previews)
schema/schema.json      JSON Schema incl. the data version (source of truth for the API)
data/sample.csv         fake data for local builds
tests/                  unit tests (approval gate, validation, escaping, API shape)
docs/api.md             API documentation
.github/workflows/      weekly build + deploy
```
