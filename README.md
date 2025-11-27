Class Cancellation Normalizer

What it does
- Scans every row in the `class` table and normalizes `cancellation` so it only ever holds three values: `NULL`, `No show`, or `Less than 24 hours cancellation`.
- Handles messy text: trims whitespace, tolerates case differences, hyphens, extra words, and minor typos for “no show”.
- If `cancellation` normalizes to `NULL`, it inspects `class_comments` (lowercased) and infers `No show` when it contains “no show”, or `Less than 24 hours cancellation` when it contains “24 hours”.
- Gives a quick summary of how many rows were changed and to which target value.

Setup
- Python 3.10+.
- Env var `DATABASE_PUBLIC_URL` must point to the Postgres instance (e.g. `postgres://...`). A `.env` file is loaded automatically for local runs.
- Install deps: `pip install -r requirements.txt`.

Usage
- Dry run (no writes): `python cli.py --dry-run`
- Apply changes: `python cli.py`
- Optional `--verbose` for debug-level logging (includes per-row update logs; can be very chatty).
- Procfile defaults to dry run: `worker: python -u cli.py --dry-run`. Remove `--dry-run` there when you want to apply changes.

Normalization rules
- Keep as-is when already exactly `No show` or `Less than 24 hours cancellation`.
- Otherwise, if the text clearly looks like “no show” (case-insensitive, allows hyphens/extra words, or small typos detected via similarity), set to `No show`.
- Otherwise, if the text contains `24` anywhere, set to `Less than 24 hours cancellation`.
- Otherwise, if `cancellation` would be `NULL`, look at `class_comments` (lowercased): if it contains “no show” → `No show`; else if it contains “24 hours” → `Less than 24 hours cancellation`.
- All other content (including `-`, empty strings, or unrelated text) becomes `NULL`.
