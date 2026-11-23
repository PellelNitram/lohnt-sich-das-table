import csv
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_site as b  # noqa: E402

TODAY = date(2026, 11, 23)
HEADER = ",".join(b.REQUIRED_COLUMNS)


def sheet(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def line(profession="Koch", gross="3000", net="2000", approved="2026-02-01", video="https://youtu.be/abc",
         video_date="2026-01-15", gproof="", nproof="", submitted="2026-01-01"):
    """One sheet row in REQUIRED_COLUMNS order."""
    return ",".join([submitted, video, video_date, profession, gross, net, gproof, nproof, approved])


class ApprovalGate(unittest.TestCase):
    def test_row_without_approval_date_is_never_published(self):
        entries, stats = b.load_entries(sheet(line("Bot", "100", "90", approved="")), TODAY)
        self.assertEqual(entries, [])
        self.assertEqual(stats["unapproved"], 1)

    def test_garbage_in_approved_column_is_not_published(self):
        for bad in ("yes", "TRUE", "x", "2026-13-40", "2099-01-01"):
            entries, stats = b.load_entries(sheet(line("Bot", "100", "90", approved=bad)), TODAY)
            self.assertEqual(entries, [], bad)
            self.assertEqual(stats["invalid"], 1, bad)

    def test_approved_row_is_published(self):
        entries, _ = b.load_entries(sheet(line("Elektriker", "3000", "2100")), TODAY)
        self.assertEqual([e.profession for e in entries], ["Elektriker"])
        self.assertEqual(entries[0].approved_at, date(2026, 2, 1))

    def test_german_date_format_is_accepted(self):
        entries, _ = b.load_entries(sheet(line("Elektriker", "3000", "2100", approved="01.02.2026")), TODAY)
        self.assertEqual(entries[0].approved_at, date(2026, 2, 1))


class Validation(unittest.TestCase):
    def parse(self, **overrides):
        row = {
            "submitted_at": "t",
            "video_url": "https://youtu.be/abc",
            "video_date": "2026-10-01",
            "profession": "Koch",
            "gross_monthly_income_eur": "3000",
            "net_monthly_income_eur": "2000",
            "gross_income_proof_url": "",
            "net_income_proof_url": "",
            "approved": "2026-11-01",
        }
        row.update(overrides)
        return b.parse_row(row, TODAY)

    def test_numbers(self):
        self.assertEqual(b.parse_number("3.500,50"), 3500.5)
        self.assertEqual(b.parse_number("3.500"), 3500)
        self.assertEqual(b.parse_number("3500 €"), 3500)
        self.assertIsNone(b.parse_number(""))
        for bad in ("abc", "nan", "inf", "1e5", "-5", "0", "2000000"):
            with self.assertRaises(b.RowError, msg=bad):
                b.parse_number(bad)

    def test_requires_some_income(self):
        with self.assertRaises(b.RowError):
            self.parse(gross_monthly_income_eur="", net_monthly_income_eur="")

    def test_net_above_gross_rejected(self):
        with self.assertRaises(b.RowError):
            self.parse(net_monthly_income_eur="4000")

    def test_video_url_must_be_https_youtube(self):
        self.assertEqual(self.parse(video_url="https://youtu.be/abc").video_url, "https://youtu.be/abc")
        for bad in ("", "javascript:alert(1)", "http://youtu.be/abc", "https://evil.example/x",
                    "https://youtube.com.evil.example/x", "https://youtube.com@evil.example/x"):
            with self.assertRaises(b.RowError, msg=bad):
                self.parse(video_url=bad)

    def test_missing_scheme_is_completed_with_https(self):
        self.assertEqual(self.parse(video_url="youtube.com/watch?v=abc").video_url, "https://youtube.com/watch?v=abc")
        self.assertEqual(self.parse(video_url="www.youtube.com/watch?v=abc").video_url,
                         "https://www.youtube.com/watch?v=abc")
        self.assertEqual(self.parse(video_url=" youtu.be/abc ").video_url, "https://youtu.be/abc")
        self.assertEqual(self.parse(net_income_proof_url="example.com/p").net_income_proof_url, "https://example.com/p")
        self.assertEqual(self.parse(net_income_proof_url="example.com:8443/p").net_income_proof_url,
                         "https://example.com:8443/p")

    def test_other_schemes_are_never_upgraded(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "mailto:a@b.de", "http://youtu.be/abc",
                    "ftp://youtu.be/abc", "//youtu.be/abc", "JavaScript:alert(1)"):
            with self.assertRaises(b.RowError, msg=bad):
                self.parse(video_url=bad)
            with self.assertRaises(b.RowError, msg=bad):
                self.parse(net_income_proof_url=bad)

    def test_video_url_error_messages_name_the_problem(self):
        for value, expected in (("https://google.com", "YouTube"), ("http://youtu.be/a", "https://"),
                                ("https://u:p@youtu.be/a", "credentials"), ("youtu.be/a b", "spaces")):
            with self.assertRaises(b.RowError) as ctx:
                self.parse(video_url=value)
            self.assertIn(expected, str(ctx.exception), value)

    def test_video_date_required_and_not_in_future(self):
        self.assertEqual(self.parse(video_date="15.10.2026").video_date, date(2026, 10, 15))
        for bad in ("", "soon", "2030-01-01"):
            with self.assertRaises(b.RowError, msg=bad):
                self.parse(video_date=bad)

    def test_proof_urls(self):
        e = self.parse(gross_income_proof_url="https://example.com/p?x=1", net_income_proof_url="")
        self.assertEqual(e.gross_income_proof_url, "https://example.com/p?x=1")
        self.assertIsNone(e.net_income_proof_url)
        for bad in ("javascript:alert(1)", "http://example.com/p", "https://user:pw@example.com/p",
                    "https://example.com/a b", "ftp://example.com", "https://" + "a" * 600):
            with self.assertRaises(b.RowError, msg=bad):
                self.parse(net_income_proof_url=bad)

    def test_profession_is_cleaned_and_limited(self):
        self.assertEqual(self.parse(profession="  Koch \n  Chef ").profession, "Koch Chef")
        with self.assertRaises(b.RowError):
            self.parse(profession="x" * 81)

    def test_missing_column_aborts_build(self):
        with self.assertRaises(SystemExit):
            b.load_entries("profession,approved\nKoch,2026-01-01\n", TODAY)

    def test_extra_columns_are_never_published(self):
        text = HEADER + ",email\n" + line("Koch") + ",secret@example.com\n"
        entries, _ = b.load_entries(text, TODAY)
        self.assertNotIn("secret@example.com", json.dumps(b.build_payload(entries, datetime.now(timezone.utc))))
        self.assertNotIn("secret@example.com", b.build_csv(entries))


class DotEnv(unittest.TestCase):
    def load(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(text, encoding="utf-8")
            return b.load_dotenv(path)

    def test_url_with_ampersands_unquoted_and_quoted(self):
        url = "https://example.com/pub?gid=1&single=true&output=csv"
        self.assertEqual(self.load(f"SHEET_CSV_URL={url}\n")["SHEET_CSV_URL"], url)
        self.assertEqual(self.load(f"SHEET_CSV_URL='{url}'\n")["SHEET_CSV_URL"], url)
        self.assertEqual(self.load(f'SHEET_CSV_URL="{url}"\n')["SHEET_CSV_URL"], url)

    def test_comments_blank_lines_and_export(self):
        self.assertEqual(self.load("# c\n\nexport A=1\nB = 2\nnoequals\n"), {"A": "1", "B": "2"})

    def test_missing_file(self):
        self.assertEqual(b.load_dotenv(Path("/nonexistent/.env")), {})


class Seo(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        for k in ("SITE_URL", "BLOG_URL", "ABOUT_VIDEO_URL"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_site_url_from_env_and_cname(self):
        os.environ["SITE_URL"] = "https://example.org/"
        self.assertEqual(b.site_url(), "https://example.org")
        os.environ["SITE_URL"] = "http://example.org"  # not https -> ignored
        expected = (ROOT / "CNAME").read_text().strip() if (ROOT / "CNAME").exists() else None
        self.assertEqual(b.site_url(), f"https://{expected}" if expected else "")

    def test_meta_tags_with_and_without_base(self):
        with_base = b.render_meta_tags("https://example.org")
        for needle in ('property="og:image" content="https://example.org/og-image.png"',
                       'property="og:url" content="https://example.org/"',
                       'rel="canonical" href="https://example.org/"', "summary_large_image"):
            self.assertIn(needle, with_base)
        without = b.render_meta_tags("")
        self.assertNotIn("og:image", without)
        self.assertIn('name="twitter:card" content="summary"', without)

    def test_robots_and_sitemap(self):
        self.assertIn("Sitemap: https://example.org/sitemap.xml", b.render_robots("https://example.org"))
        self.assertNotIn("Sitemap", b.render_robots(""))
        xml = b.render_sitemap("https://example.org", date(2026, 11, 23))
        self.assertIn("<loc>https://example.org/</loc>", xml)
        self.assertIn("<lastmod>2026-11-23</lastmod>", xml)

    def test_about_video_section(self):
        self.assertEqual(b._about_section(""), "")
        self.assertIn("https://youtu.be/abc", b._about_section("youtu.be/abc"))
        for bad in ("https://evil.example/x", "javascript:alert(1)", "http://youtu.be/a"):
            self.assertEqual(b._about_section(bad), "", bad)

    def test_page_has_optional_links_only_when_configured(self):
        now = datetime.now(timezone.utc)
        page = b.render_index([], now, sample=False)
        self.assertNotIn("Mehr auf meinem Blog", page)
        self.assertNotIn("Wie es dazu kam", page)
        os.environ["BLOG_URL"] = "https://blog.example.org/"
        os.environ["ABOUT_VIDEO_URL"] = "https://youtu.be/abc"
        page = b.render_index([], now, sample=False)
        self.assertIn('href="https://blog.example.org/"', page)
        self.assertIn("Wie es dazu kam", page)

    def test_build_writes_robots_sitemap_and_og_image(self):
        os.environ["SITE_URL"] = "https://example.org"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            b.build(str(ROOT / "data" / "sample.csv"), out, sample=True)
            self.assertIn("Sitemap: https://example.org/sitemap.xml", (out / "robots.txt").read_text())
            self.assertIn("<loc>https://example.org/</loc>", (out / "sitemap.xml").read_text())
            self.assertTrue((out / "og-image.png").read_bytes().startswith(b"\x89PNG"))
            self.assertIn('og:image" content="https://example.org/og-image.png"', (out / "index.html").read_text())


class Output(unittest.TestCase):
    def test_html_is_escaped(self):
        entries, _ = b.load_entries(sheet(line('"<script>alert(1)</script>"')), TODAY)
        rows = b.render_rows(entries)
        self.assertNotIn("<script>", rows)
        self.assertIn("&lt;script&gt;", rows)

    def test_csv_formula_injection_is_neutralised(self):
        entries, _ = b.load_entries(sheet(line("=HYPERLINK(1)")), TODAY)
        rows = list(csv.reader(io.StringIO(b.build_csv(entries))))
        self.assertEqual(rows[1][b.FIELD_NAMES.index("profession")], "'=HYPERLINK(1)")

    def test_full_build_from_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            b.build(str(ROOT / "data" / "sample.csv"), out, sample=True)
            data = json.loads((out / "api/v1/data.json").read_text())
            self.assertEqual(data["count"], len(data["entries"]))
            self.assertEqual(data["count"], 4)  # 6 rows: 1 unapproved, 1 invalid
            for entry in data["entries"]:
                self.assertEqual(sorted(entry), sorted(b.FIELD_NAMES))
            self.assertEqual((out / "api/v1/schema.json").read_bytes(), b.SCHEMA_PATH.read_bytes())
            self.assertEqual([e["video_date"] for e in data["entries"]],
                             sorted((e["video_date"] for e in data["entries"]), reverse=True))
            self.assertTrue((out / "index.html").exists())
            self.assertNotIn("Noch nicht geprüft", (out / "index.html").read_text())

    def test_schema_describes_the_v1_data_format(self):
        self.assertEqual(b.SCHEMA_VERSION, "1")
        self.assertEqual(b.FIELD_NAMES, [
            "id", "video_url", "video_date", "profession", "gross_monthly_income_eur",
            "net_monthly_income_eur", "gross_income_proof_url", "net_income_proof_url", "approved_at"])
        entry = b.SCHEMA["$defs"]["entry"]
        self.assertEqual(sorted(entry["required"]), sorted(b.FIELD_NAMES))
        self.assertFalse(entry["additionalProperties"])

    def test_api_docs_mention_every_field(self):
        docs = (ROOT / "docs" / "api.md").read_text()
        for name in b.FIELD_NAMES:
            self.assertIn(f"`{name}`", docs)


if __name__ == "__main__":
    unittest.main()
