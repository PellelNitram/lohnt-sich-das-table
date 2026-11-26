#!/usr/bin/env python3
"""Build the static site and the static JSON/CSV API into ./dist.

Pipeline:  Google Form -> Google Sheet -> (published CSV) -> this script -> dist/

Only rows whose `approved` column holds a valid, non-future date are published.
Everything else (including all properties not listed in schema/schema.json) is dropped, because
this repository and the generated site are public.

Standard library only, so it runs anywhere with Python >= 3.10.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DIST = ROOT / "dist"

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_INCOME_EUR = 1_000_000
MAX_PROFESSION_LEN = 80
MAX_URL_LEN = 500
ALLOWED_VIDEO_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

# Column headers the Google Sheet must have (first row). Extra columns, such as
# e-mail addresses collected by a Form, are ignored and never published.
REQUIRED_COLUMNS = (
    "submitted_at",
    "video_url",
    "video_date",
    "profession",
    "gross_monthly_income_eur",
    "net_monthly_income_eur",
    "gross_income_proof_url",
    "net_income_proof_url",
    "approved",
)

# schema/schema.json is the single source of truth for the public API payload.
# It is published as /api/v<version>/schema.json and drives the CSV columns and the field
# table on the website. docs/api.md must stay in sync (see tests).
SCHEMA_PATH = ROOT / "schema" / "schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SCHEMA_VERSION = SCHEMA["properties"]["schema_version"]["const"]
ENTRY_PROPS = SCHEMA["$defs"]["entry"]["properties"]
FIELD_NAMES = list(ENTRY_PROPS)


class RowError(ValueError):
    """An approved row that is malformed and therefore skipped."""


@dataclass(frozen=True)
class Entry:
    id: str
    video_url: str
    video_date: date
    profession: str
    gross_monthly_income_eur: float | int | None
    net_monthly_income_eur: float | int | None
    gross_income_proof_url: str | None
    net_income_proof_url: str | None
    approved_at: date

    def to_dict(self) -> dict:
        d = {name: getattr(self, name) for name in FIELD_NAMES}
        d["video_date"] = self.video_date.isoformat()
        d["approved_at"] = self.approved_at.isoformat()
        return d


# --------------------------------------------------------------------------- parsing

_PLAIN_NUMBER = re.compile(r"^\d+(\.\d+)?$")
_DE_THOUSANDS = re.compile(r"^\d{1,3}(\.\d{3})+$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$")
_DE_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def parse_number(raw: str) -> float | int | None:
    """Parse '3500', '3500.5', '3.500', '3.500,50' or '3500 €'. Empty -> None."""
    s = raw.replace("€", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif _DE_THOUSANDS.match(s):
        s = s.replace(".", "")
    if not _PLAIN_NUMBER.match(s):
        raise RowError(f"not a number: {raw!r}")
    value = float(s)
    if not math.isfinite(value) or not 0 < value <= MAX_INCOME_EUR:
        raise RowError(f"income out of range: {raw!r}")
    value = round(value, 2)
    return int(value) if value.is_integer() else value


def parse_date(raw: str) -> date:
    s = raw.strip()
    try:
        if m := _ISO_DATE.match(s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _DE_DATE.match(s):
            return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        pass
    raise RowError(f"not a valid date: {raw!r}")


def clean_text(raw: str) -> str:
    return " ".join(_CONTROL_CHARS.sub(" ", raw).split())


# "scheme:" at the start, but not "host:1234" (a port).
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:(?!\d)", re.IGNORECASE)


def _clean_https_url(raw: str, label: str) -> str | None:
    s = raw.strip()
    if not s:
        return None
    if len(s) > MAX_URL_LEN or any(c.isspace() or ord(c) < 32 for c in s):
        raise RowError(f"{label} is not a valid link (too long or contains spaces)")
    if "://" not in s and not _HAS_SCHEME.match(s):
        s = "https://" + s  # "youtube.com/watch?v=x" -> https; other schemes are never upgraded
    m = re.match(r"^https://([^/?#]+)(?:[/?#].*)?$", s)
    if not m:
        raise RowError(f"{label} must be an https:// link")
    if "@" in m[1]:
        raise RowError(f"{label} must not contain credentials")
    return s


def clean_video_url(raw: str) -> str:
    s = _clean_https_url(raw, "video_url")
    if s is None:
        raise RowError("video_url is empty")
    host = re.match(r"^https://([^/?#:]+)", s)[1].lower()
    if host not in ALLOWED_VIDEO_HOSTS:
        raise RowError("video_url must be a YouTube link (youtube.com or youtu.be)")
    return s


def clean_proof_url(raw: str, label: str) -> str | None:
    return _clean_https_url(raw, label)


def parse_row(row: dict[str, str], today: date) -> Entry | None:
    """Return an Entry, None if not approved, or raise RowError if malformed."""
    approved_raw = (row.get("approved") or "").strip()
    if not approved_raw:
        return None  # not approved -> never rendered, never inspected further
    approved_at = parse_date(approved_raw)
    if approved_at > today:
        raise RowError("approval date is in the future")

    profession = clean_text(row.get("profession") or "")
    if not profession:
        raise RowError("profession is empty")
    if len(profession) > MAX_PROFESSION_LEN:
        raise RowError(f"profession longer than {MAX_PROFESSION_LEN} characters")

    video_url = clean_video_url(row.get("video_url") or "")
    video_date = parse_date(row.get("video_date") or "")
    if video_date > today:
        raise RowError("video_date is in the future")

    gross = parse_number(row.get("gross_monthly_income_eur") or "")
    net = parse_number(row.get("net_monthly_income_eur") or "")
    if gross is None and net is None:
        raise RowError("neither gross nor net income given")
    if gross is not None and net is not None and net > gross:
        raise RowError("net income exceeds gross income")

    submitted_at = (row.get("submitted_at") or "").strip()
    digest = hashlib.sha1(
        f"{submitted_at}|{video_url}|{profession}|{gross}|{net}".encode()
    ).hexdigest()
    return Entry(
        id=digest[:12],
        video_url=video_url,
        video_date=video_date,
        profession=profession,
        gross_monthly_income_eur=gross,
        net_monthly_income_eur=net,
        gross_income_proof_url=clean_proof_url(row.get("gross_income_proof_url") or "", "gross_income_proof_url"),
        net_income_proof_url=clean_proof_url(row.get("net_income_proof_url") or "", "net_income_proof_url"),
        approved_at=approved_at,
    )


def load_entries(csv_text: str, today: date) -> tuple[list[Entry], dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise SystemExit(f"error: sheet is missing required column(s): {', '.join(missing)}")
    reader.fieldnames = headers

    entries: dict[str, Entry] = {}
    stats = {"rows": 0, "unapproved": 0, "invalid": 0, "duplicate": 0}
    for line_no, row in enumerate(reader, start=2):
        stats["rows"] += 1
        try:
            entry = parse_row(row, today)
        except RowError as exc:
            stats["invalid"] += 1
            print(f"warning: sheet row {line_no} skipped: {exc}", file=sys.stderr)
            continue
        if entry is None:
            stats["unapproved"] += 1
        elif entry.id in entries:
            stats["duplicate"] += 1
        else:
            entries[entry.id] = entry

    ordered = sorted(entries.values(), key=lambda e: (e.video_date, e.id), reverse=True)
    return ordered, stats


def read_source(source: str) -> str:
    if source.startswith("http://"):
        raise SystemExit("error: refusing to download data over plain http")
    if source.startswith("https://"):
        req = urllib.request.Request(source, headers={"User-Agent": "lohnt-sich-das-table-build"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(MAX_DOWNLOAD_BYTES + 1)
        except urllib.error.HTTPError as exc:  # message deliberately omits the URL
            raise SystemExit(f"error: downloading the sheet failed (HTTP {exc.code})")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SystemExit(f"error: downloading the sheet failed ({type(exc).__name__})")
    else:
        raw = Path(source).read_bytes()
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise SystemExit("error: sheet is larger than the 5 MB limit")
    return raw.decode("utf-8-sig")


# --------------------------------------------------------------------------- output

def build_payload(entries: list[Entry], now: datetime) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(entries),
        "entries": [e.to_dict() for e in entries],
    }


def _csv_safe(value) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s  # spreadsheet formula injection


def build_csv(entries: list[Entry]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(FIELD_NAMES)
    for e in entries:
        d = e.to_dict()
        writer.writerow([_csv_safe(d[name]) for name in FIELD_NAMES])
    return buf.getvalue()


def fmt_eur(value: float | int | None) -> str:
    if value is None:
        return "–"
    return f"{value:,.0f}".replace(",", ".") + " €"


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _https_or_empty(url: str) -> str:
    url = (url or "").strip()
    return url if url.startswith("https://") else ""


_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
PAGE_TITLE = "Lohnt sich das? – Gehaltstabelle"
PAGE_DESCRIPTION = (
    "Gehälter aus der Community, geprüft und frei zugänglich: Beruf, Brutto und Netto pro Monat "
    "– als Tabelle und als offene JSON-API."
)


def site_url() -> str:
    """Absolute site URL without trailing slash: $SITE_URL, else derived from ./CNAME, else ''."""
    env = _https_or_empty(os.environ.get("SITE_URL", "")).rstrip("/")
    if env:
        return env
    cname = ROOT / "CNAME"
    if cname.is_file():
        domain = cname.read_text(encoding="utf-8").strip().lower()
        if _DOMAIN.match(domain):
            return f"https://{domain}"
    return ""


def render_meta_tags(base: str) -> str:
    esc = lambda v: html.escape(v, quote=True)  # noqa: E731
    tags = [
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="de_DE">',
        '<meta property="og:site_name" content="Lohnt sich das?">',
        f'<meta property="og:title" content="{esc(PAGE_TITLE)}">',
        f'<meta property="og:description" content="{esc(PAGE_DESCRIPTION)}">',
        f'<meta name="twitter:card" content="{"summary_large_image" if base else "summary"}">',
        f'<meta name="twitter:title" content="{esc(PAGE_TITLE)}">',
        f'<meta name="twitter:description" content="{esc(PAGE_DESCRIPTION)}">',
    ]
    if base:  # og:image and og:url must be absolute
        tags += [
            f'<link rel="canonical" href="{esc(base)}/">',
            f'<meta property="og:url" content="{esc(base)}/">',
            f'<meta property="og:image" content="{esc(base)}/og-image.png">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            '<meta property="og:image:alt" content="Lohnt sich das? – Was verdient man wirklich?">',
            f'<meta name="twitter:image" content="{esc(base)}/og-image.png">',
        ]
    return "\n".join(tags)


def render_robots(base: str) -> str:
    lines = ["User-agent: *", "Allow: /"]
    if base:
        lines += ["", f"Sitemap: {base}/sitemap.xml"]
    return "\n".join(lines) + "\n"


def render_sitemap(base: str, lastmod: date) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url>\n    <loc>{html.escape(base)}/</loc>\n    <lastmod>{lastmod.isoformat()}</lastmod>\n  </url>\n"
        "</urlset>\n"
    )


# Every environment variable the build reads. A test checks that the CI workflow
# forwards each of them, so local and CI builds see the same configuration.
ENV_VARS = (
    "SHEET_CSV_URL",
    "SITE_URL",
    "FORM_URL",
    "FEEDBACK_FORM_URL",
    "BLOG_URL",
    "ABOUT_VIDEO_URL",
    "KOFI_URL",
    "GOATCOUNTER_URL",
)

UTM_SOURCE = "lohntsichdas"
UTM_MEDIUM = "referral"
UTM_CAMPAIGN = "salary-table"


def with_utm(url: str, content: str) -> str:
    """Append UTM parameters so the blog's analytics can attribute visits to this site.

    Existing query parameters and the fragment are kept. A URL that already carries
    utm_* parameters is left untouched, so BLOG_URL can override the defaults.
    """
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key.startswith("utm_") for key, _ in query):
        return url
    query += [
        ("utm_source", UTM_SOURCE),
        ("utm_medium", UTM_MEDIUM),
        ("utm_campaign", UTM_CAMPAIGN),
        ("utm_content", content),
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _about_video_url(raw_url: str) -> str:
    raw_url = (raw_url or "").strip()
    if not raw_url:
        return ""
    try:
        return clean_video_url(raw_url)
    except RowError as exc:
        print(f"warning: ABOUT_VIDEO_URL ignored: {exc}", file=sys.stderr)
        return ""


def _about_section(url: str) -> str:
    if not url:
        return ""
    return (
        '<section class="prose" id="hintergrund"><div class="wrap">'
        "<h2>Wie es dazu kam</h2>"
        f"<p>Warum es diese Seite gibt, erkläre ich in einem Video: {_link(url, 'Video ansehen ↗')}</p>"
        "</div></section>"
    )


def _hero_links(about_url: str, feedback_url: str, kofi_url: str, blog_url: str) -> str:
    """Secondary links right under the submit button, so they are visible on first view."""
    esc = html.escape
    links = []
    if about_url:
        links.append(_link(about_url, "▶ Wie es dazu kam"))
    if feedback_url:
        links.append(f'<a href="{esc(feedback_url, quote=True)}" target="_blank" rel="noopener">Feedback geben ↗</a>')
    if kofi_url:
        links.append(f'<a href="{esc(kofi_url, quote=True)}" target="_blank" rel="noopener">☕ Ko-fi ↗</a>')
    if blog_url:
        links.append(f'<a href="{esc(with_utm(blog_url, "hero"), quote=True)}">Blog ↗</a>')
    if not links:
        return ""
    return '<p class="hero-links">' + '<span aria-hidden="true"> · </span>'.join(links) + "</p>"


def _feedback_section(feedback_url: str, kofi_url: str) -> str:
    if not (feedback_url or kofi_url):
        return ""
    esc = html.escape
    parts = []
    if feedback_url:
        parts.append(
            "<p>Idee, Kritik oder Fehler gefunden? "
            f'<a class="btn" href="{esc(feedback_url, quote=True)}" target="_blank" rel="noopener">Feedback geben ↗</a></p>'
        )
    if kofi_url:
        parts.append(
            "<p>Die Seite ist kostenlos und werbefrei. Wenn sie dir hilft, freue ich mich über einen Kaffee: "
            f'<a class="btn btn-secondary" href="{esc(kofi_url, quote=True)}" target="_blank" rel="noopener">☕ Ko-fi ↗</a></p>'
        )
    return (
        '<section class="prose" id="feedback"><div class="wrap">'
        "<h2>Feedback &amp; Unterstützung</h2>" + "".join(parts) + "</div></section>"
    )


def _footer_links(feedback_url: str, kofi_url: str, blog_url: str) -> str:
    esc = html.escape
    links = []
    if feedback_url:
        links.append(f'<a href="{esc(feedback_url, quote=True)}" target="_blank" rel="noopener">Feedback ↗</a>')
    if kofi_url:
        links.append(f'<a href="{esc(kofi_url, quote=True)}" target="_blank" rel="noopener">Ko-fi ↗</a>')
    if blog_url:
        links.append(f'<a href="{esc(with_utm(blog_url, "footer"), quote=True)}">Mehr auf meinem Blog ↗</a>')
    return "".join(f" · {link}" for link in links)


def _link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}" rel="noopener nofollow ugc" target="_blank">{label}</a>'


def _income_cell(value: float | int | None, proof_url: str | None) -> str:
    proof = f' <small>{_link(proof_url, "Beleg ↗")}</small>' if proof_url else ""
    return f'<td class="num">{fmt_eur(value)}{proof}</td>'


def render_rows(entries: list[Entry]) -> str:
    if not entries:
        return '<tr class="empty"><td colspan="5">Noch keine geprüften Einträge.</td></tr>'
    esc = html.escape
    rows = []
    for e in entries:
        rows.append(
            f'<tr data-profession="{esc(e.profession.lower(), quote=True)}"'
            f' data-gross="{e.gross_monthly_income_eur or ""}"'
            f' data-net="{e.net_monthly_income_eur or ""}"'
            f' data-date="{e.video_date.isoformat()}">'
            f'<td class="beruf">{esc(e.profession)}</td>'
            f"{_income_cell(e.gross_monthly_income_eur, e.gross_income_proof_url)}"
            f"{_income_cell(e.net_monthly_income_eur, e.net_income_proof_url)}"
            f"<td>{_link(e.video_url, 'Video ↗')}</td>"
            f'<td class="num">{fmt_date(e.video_date)}</td></tr>'
        )
    return "\n".join(rows)


def _type_label(prop: dict) -> str:
    t = prop["type"]
    label = " | ".join(t) if isinstance(t, list) else t
    return f"{label} ({prop['format']})" if "format" in prop else label


def render_field_table() -> str:
    esc = html.escape
    rows = []
    for name, prop in ENTRY_PROPS.items():
        rows.append(
            f"<tr><td><code>{esc(name)}</code></td><td>{esc(_type_label(prop))}</td>"
            f"<td>{esc(prop['description'])}</td></tr>"
        )
    return "\n".join(rows)


def chart_data_json(entries: list[Entry]) -> str:
    """Minimal data for the charts, safe to embed in a <script type="application/json">."""
    data = [
        {"p": e.profession, "g": e.gross_monthly_income_eur, "n": e.net_monthly_income_eur}
        for e in entries
    ]
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # Neutralise anything that could close the script element or start markup.
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_index(entries: list[Entry], now: datetime, *, sample: bool) -> str:
    esc = html.escape
    last_update = max((e.approved_at for e in entries), default=None)

    form_url = _https_or_empty(os.environ.get("FORM_URL", ""))
    blog_url = _https_or_empty(os.environ.get("BLOG_URL", ""))
    gc_url = _https_or_empty(os.environ.get("GOATCOUNTER_URL", ""))

    submit_button = (
        f'<a class="btn" href="{esc(form_url, quote=True)}" target="_blank" rel="noopener">Eigenen Eintrag einreichen ↗</a>'
        if form_url
        else ""
    )
    blog_link = (
        f'<a class="link" href="{esc(with_utm(blog_url, "header"), quote=True)}">Blog ↗</a>'
        if blog_url
        else ""
    )
    goatcounter = (
        f'<script data-goatcounter="{esc(gc_url, quote=True)}" async src="//gc.zgo.at/count.js"></script>'
        if gc_url
        else ""
    )
    feedback_url = _https_or_empty(os.environ.get("FEEDBACK_FORM_URL", ""))
    kofi_url = _https_or_empty(os.environ.get("KOFI_URL", ""))
    about_url = _about_video_url(os.environ.get("ABOUT_VIDEO_URL", ""))
    sample_banner = (
        '<p class="note"><strong>Beispieldaten.</strong> Dieser Build nutzt <code>data/sample.csv</code> '
        "und enthält erfundene Zahlen.</p>"
        if sample
        else ""
    )

    template = Template((SRC / "index.html").read_text(encoding="utf-8"))
    return template.substitute(
        rows=render_rows(entries),
        field_table=render_field_table(),
        count=len(entries),
        last_update=fmt_date(last_update) if last_update else "–",
        built_at=now.strftime("%d.%m.%Y"),
        submit_button=submit_button,
        blog_link=blog_link,
        goatcounter=goatcounter,
        sample_banner=sample_banner,
        schema_version=SCHEMA_VERSION,
        meta_tags=render_meta_tags(site_url()),
        about_section=_about_section(about_url),
        hero_links=_hero_links(about_url, feedback_url, kofi_url, blog_url),
        chart_data=chart_data_json(entries),
        footer_links=_footer_links(feedback_url, kofi_url, blog_url),
        feedback_section=_feedback_section(feedback_url, kofi_url),
        page_title=html.escape(PAGE_TITLE),
        page_description=html.escape(PAGE_DESCRIPTION, quote=True),
    )


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build(source: str, out: Path, *, sample: bool) -> None:
    now = datetime.now(timezone.utc)
    entries, stats = load_entries(read_source(source), now.date())

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for static in (SRC / "static").iterdir():
        shutil.copy2(static, out / static.name)
    if (ROOT / "CNAME").exists():
        shutil.copy2(ROOT / "CNAME", out / "CNAME")
    write(out / ".nojekyll", "")

    write(out / "index.html", render_index(entries, now, sample=sample))
    base = site_url()
    write(out / "robots.txt", render_robots(base))
    if base:
        write(out / "sitemap.xml", render_sitemap(base, max((e.approved_at for e in entries), default=now.date())))
    api = out / "api" / f"v{SCHEMA_VERSION}"  # the version lives only in the schema file
    write(
        api / "data.json",
        json.dumps(build_payload(entries, now), ensure_ascii=False, indent=2) + "\n",
    )
    write(api / "data.csv", build_csv(entries))
    shutil.copy2(SCHEMA_PATH, api / "schema.json")

    print(
        f"built {out}: {len(entries)} published of {stats['rows']} rows "
        f"({stats['unapproved']} unapproved, {stats['invalid']} invalid, "
        f"{stats['duplicate']} duplicate)"
    )


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. No shell involved, so `&` etc. in URLs are safe unquoted."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def main() -> None:
    # Local runs read ./.env; CI sets real environment variables. Real env vars win.
    for key, value in load_dotenv(ROOT / ".env").items():
        os.environ.setdefault(key, value)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        default=os.environ.get("SHEET_CSV_URL"),
        help="CSV file or https URL (default: $SHEET_CSV_URL)",
    )
    parser.add_argument("--sample", action="store_true", help="build from data/sample.csv and mark the page as sample data")
    parser.add_argument("--out", type=Path, default=DIST)
    args = parser.parse_args()

    source = str(ROOT / "data" / "sample.csv") if args.sample else args.source
    if not source:
        raise SystemExit("error: no data source. Set SHEET_CSV_URL or pass --sample / --source.")
    build(source, args.out, sample=args.sample)


if __name__ == "__main__":
    main()
