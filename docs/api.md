# API

Static files, no authentication, no rate limit. Rebuilt daily (03:00 UTC) together with the site.

**Data version: 1.** The machine-readable definition is [`schema/schema.json`](../schema/schema.json) (JSON Schema, draft 2020-12). It is the source of truth and is published unchanged as `/api/v1/schema.json`. The version is defined only there (`schema_version`); the build derives the `/api/v<version>/` path from it.

| Endpoint | Content |
| --- | --- |
| `/api/v1/data.json` | All published entries (JSON) |
| `/api/v1/data.csv` | Same entries as CSV, columns in the order of the entry fields below |
| `/api/v1/schema.json` | JSON Schema of `data.json` |

## `data.json`

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | string | Always `"1"` under `/api/v1/`. |
| `generated_at` | string (date-time, UTC) | When the site was built. |
| `count` | integer | Number of items in `entries`. |
| `entries` | array | Entries, newest `video_date` first. |

### Entry

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Stable 12-character identifier derived from the submission. |
| `video_url` | string (uri) | https link to the YouTube video the entry is taken from. |
| `video_date` | string (date) | Publication date of the video (`YYYY-MM-DD`). |
| `profession` | string | Profession title: the primary profession or job featured in the video (max. 80 characters). |
| `gross_monthly_income_eur` | number \| null | Gross monthly income in euros; `null` if not stated. |
| `net_monthly_income_eur` | number \| null | Net monthly income in euros; `null` if not stated. |
| `gross_income_proof_url` | string (uri) \| null | https link to evidence for the gross income; `null` if none given. |
| `net_income_proof_url` | string (uri) \| null | https link to evidence for the net income; `null` if none given. |
| `approved_at` | string (date) | Date (`YYYY-MM-DD`) the entry was manually approved for publication. |

Rules enforced by the build:

- At least one of the two income fields is set. If both are set, net is not larger than gross.
- Incomes are greater than 0 and at most 1,000,000.
- `video_url` must be a YouTube link (`youtube.com`, `www.youtube.com`, `m.youtube.com` or `youtu.be`). Proof URLs may point to any site. All links are published as `https://` links without credentials. A link submitted without a scheme (`youtu.be/abc`) gets `https://` prepended; `http://` and every other scheme (`javascript:`, `data:`, `ftp:` …) is rejected.
- `video_date` and `approved_at` are never in the future.
- Proof links are optional and are not checked for authenticity.

### Example

```json
{
  "schema_version": "1",
  "generated_at": "2026-11-23T03:00:41Z",
  "count": 1,
  "entries": [
    {
      "id": "3f2a9c1b7d4e",
      "video_url": "https://www.youtube.com/watch?v=EXAMPLE",
      "video_date": "2026-10-11",
      "profession": "Elektriker",
      "gross_monthly_income_eur": 3000,
      "net_monthly_income_eur": 2100,
      "gross_income_proof_url": null,
      "net_income_proof_url": "https://example.com/proof",
      "approved_at": "2026-11-02"
    }
  ]
}
```

## Versioning

- Adding optional fields is allowed within `v1`; clients must ignore unknown fields.
- Removing, renaming or changing the meaning of a field means bumping `schema_version` in `schema/schema.json`, which moves the API to `/api/v2/`. The old version is then no longer built; announce the change before doing it.

## Notes

- All data is self-reported and unverified.
- The submission timestamp and any other form columns are intentionally not published.
- `data.csv` prefixes values starting with `=`, `+`, `-` or `@` with `'` to prevent spreadsheet formula injection.

A test fails if this file stops mentioning a field of the schema.
