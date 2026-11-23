# Instructions for coding agents

## Git

- Do **not** mention Claude Code or any other coding agent in git commit messages. No `Co-Authored-By` trailers for agents, no "Generated with …" lines.
- The same applies to pull request titles and descriptions.
- Commit only when explicitly asked.

## Project conventions

- The repository is **public**. Never commit secrets, the sheet URL, e-mail addresses or any raw form submissions.
- Only the fields in `schema/schema.json` may reach `dist/`. Rows without a valid `approved` date must never be rendered.
- Python code uses the standard library only. Run `make test` before finishing a change.
- The API payload is documented in `docs/api.md`; update it together with the schema file. The data version lives only in that file (`schema_version`); the API path is derived from it. Bump it only for breaking changes.
