DB Course Data Recovery/Cleaning

Overview
- Now targets backup tables with prefix `backup_new_` (i.e., `public.backup_new_course`, `public.backup_new_class`, `public.backup_new_student_data`).
- Reads each path from `b2b_paths.cleaned.csv` (or a provided file via `--input`).
- Extracts the filename (after the last `/` and after the last `___`).
- Infers customer type from the path: contains `taas` OR any configured TAAS school keyword (see `taas_schools.py`) → `TAAS`; contains `B2B`/`Companies` → `B2B`; otherwise `B2C`.
- Finds matches in `public.backup_new_course` where `spreadsheet_name` equals that filename (exact match).
- Updates matched rows in `public.backup_new_course`:
  - Sets `customer_type` to `TAAS`, `B2B`, or `B2C` (uppercased).
  - Sets `company_name` only when a segment ending with `Companies` (case-insensitive) is present (e.g., `"Travis - Companies"`, `"Companies"`); it uses the segment immediately after it. Within that segment, if the exact delimiter ` - ` exists, only the substring after the last ` - ` is kept. Stored uppercased (e.g., `".../Ksenija - Companies___Ksenija - LAMA___..."` → `"LAMA"`). If no such segment exists, `company_name` is left NULL.
- Sets `course_language` to one of `IT`, `ES`, `EN`, `FR`, `DE` (uppercased). It first searches inside square brackets (e.g., `[DE - Babbel]`, `[ EN ]`) and uses the first code found there. If none are found in brackets, it falls back to scanning the whole path using letter‑boundary rules (e.g., `" EN ", "(FR)"`), avoiding matches embedded in words (so `"aDE "` is ignored, but `"a DE "` is valid). Codes with an underscore immediately before them are ignored (e.g., `"_IT"` does not match). If no code is found anywhere, stores `'-'` (uppercased to `'-'`) to satisfy NOT NULL schemas and logs the same.
  - Sets related `backup_new_student_data.is_2on1` to `true` if the full path contains the exact substring `"2-1"`; otherwise sets it to `false`.
  - If `customer_type` resolves to `TAAS`, sets `taas_school` (when the column exists) using a configurable mapping in `taas_schools.py` (e.g., path contains `"babbel"` → stored as `BABBEL`, `"hola"` → stored as `HOLA`). You can extend this list in that file.

Notes
- No rows are inserted; only existing rows in `public.backup_new_course` are updated if a filename match is found.
- If a path does not imply `taas` or `b2b`, the `customer_type` defaults to `B2C`.

Duplicate Handling
- When multiple `public.backup_new_course` rows share the same `spreadsheet_name` and ALL share the same `student_id`:
  - If some have related classes (>0), delete all duplicates that have 0 classes; keep those with classes.
  - If all have 0 related classes, keep only one (the first), delete the rest.
  - If only one course matches the `spreadsheet_name`, do nothing (even if it has 0 classes).
  - If matches have different `student_id` values, do nothing.
  - Log only deletions (same text in dry‑run and real): `duplicate: delete course id=<id> | 0 classes`.

Requirements
- Environment variable `DATABASE_PUBLIC_URL` must point to the PostgreSQL instance (Railway compatible).
- For local dev, put it in `.env` and it will be auto‑loaded.
- Python 3 with `psycopg2-binary` and `python-dotenv` (installed via `requirements.txt`).

Usage
- Dry run (no DB writes):
  `python cli.py --dry-run --verbose`
- Real run:
  `python cli.py`
- Specify a different input file:
  `python cli.py --input path/to/file.txt`

Join Tables Builder
- Builds union tables `course_join`, `class_join`, and `student_data_join` by merging existing tables with `*_taas` tables.
- Keeps original rows from `course`, `class`, and `student_data` by `id`. Only adds rows from `*_taas` whose `id` does not exist in the original tables.
- Sets `updated_at = NOW()` for the newly added rows from `*_taas` in the join tables (if the column exists).

Run:
- Recreate join tables (drop if exist, then rebuild):
  `python run_build_joins.py --verbose`
- Keep existing join tables structure (do not drop first):
  `python run_build_joins.py --no-recreate`

Railway
- Add a new Python service and connect this repo.
- Set env var `DATABASE_PUBLIC_URL` in Railway to your Postgres URL.
- The provided `Procfile` runs the updater: `python -u cli.py --verbose`.
- To run the join builder (`run_build_joins.py`), change the Procfile or override the start command with `python -u run_build_joins.py --verbose`.

Input File
- One path per line. Example line:
  `EX-STUDENTS1/Ex-Students - TaaS___ABA English Ex-Students___ABA English - NEW___Carla Sanches (ABA ENGLISH) GB 22917764`
- Extracted filename → `Carla Sanches (ABA ENGLISH) GB 22917764`

Log Format
- On match:
  - `* <pathname> | Match`
  - `duplicates: <N>` when more than one course matches and all share the same `student_id`.
  - optional duplicate deletion lines right after the pathname (only when a duplicate will be deleted under the rules above)
  - `backup_new_course: [customer_type:<TYPE>, company_name:<COMPANY>, course_language:<LANG>, taas_school:<SCHOOL>]`
  - `backup_new_student_data: [is_2on1:<True|False>]`
- On no match:
  - `* <pathname> | No Match`
