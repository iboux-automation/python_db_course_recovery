#!/usr/bin/env python3
import argparse
import logging
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg2.extras
from dotenv import load_dotenv

from db_conn import get_conn
from tables_ops import fetch_table_columns
from extract_helpers import extract_filename


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(message)s')


def _read_input_lines(input_path: str) -> List[str]:
    with open(input_path, 'rb') as f:
        data = f.read()
    # Preserve diacritics as much as possible
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    # Fallback with replacement
    return data.decode('utf-8', errors='replace').splitlines()


def _table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name=%s
            """,
            (table,),
        )
        return cur.fetchone() is not None


def _get_courses_by_spreadsheet_name(conn, spreadsheet_name: str) -> List[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM public.backup_new_course WHERE spreadsheet_name = %s",
            (spreadsheet_name,),
        )
        return list(cur.fetchall())


def _get_duplicate_spreadsheet_names(conn) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT spreadsheet_name
            FROM public.backup_new_course
            GROUP BY spreadsheet_name
            HAVING COUNT(*) > 1
            ORDER BY spreadsheet_name
            """
        )
        return [r[0] for r in cur.fetchall()]


def _get_class_counts(conn, course_ids: Sequence[object]) -> Dict[str, int]:
    if not course_ids:
        return {}
    if not _table_exists(conn, 'backup_new_class'):
        return {str(cid): 0 for cid in course_ids}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT course_id, COUNT(*)
            FROM public.backup_new_class
            WHERE course_id = ANY(%s::uuid[])
            GROUP BY course_id
            """,
            (list(map(str, course_ids)),),
        )
        rows = cur.fetchall()
    counts = {str(cid): 0 for cid in course_ids}
    for cid, cnt in rows:
        counts[str(cid)] = int(cnt)
    return counts


def _attribute_score(row: dict, have_cols: Iterable[str]) -> int:
    cols = set(have_cols)
    score = 0
    # Consider presence of key business columns only if they exist in the table
    if 'customer_type' in cols and (row.get('customer_type') or '').strip():
        score += 1
    if 'company_name' in cols and (row.get('company_name') or '').strip():
        score += 1
    if 'course_language' in cols:
        lang = (row.get('course_language') or '').strip()
        # Treat '-' or empty as not filled
        if lang and lang != '-':
            score += 1
    if 'taas_school' in cols and (row.get('taas_school') or '').strip():
        score += 1
    return score


def _choose_course_to_keep(rows: List[dict], class_counts: Dict[str, int], have_cols: Iterable[str]) -> dict:
    # Pick by: higher attribute score, then higher class count, then smaller id (string)
    best_row = None
    best_score = -1
    best_classes = -1
    best_id_str = "~"  # something high; any real UUID string sorts before '~'
    for r in rows:
        rid_str = str(r.get('id'))
        score = _attribute_score(r, have_cols)
        cls = int(class_counts.get(rid_str, 0))
        if (
            best_row is None
            or score > best_score
            or (score == best_score and (cls > best_classes or (cls == best_classes and rid_str < best_id_str)))
        ):
            best_row = r
            best_score = score
            best_classes = cls
            best_id_str = rid_str
    return best_row


def _reassign_classes(conn, from_course_id: object, to_course_id: object, dry_run: bool = False) -> int:
    if dry_run:
        # Estimate count for logging
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM public.backup_new_class WHERE course_id = %s::uuid",
                (str(from_course_id),),
            )
            return int(cur.fetchone()[0])
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE public.backup_new_class SET course_id = %s::uuid WHERE course_id = %s::uuid",
            (str(to_course_id), str(from_course_id)),
        )
        moved = cur.rowcount or 0
    conn.commit()
    return int(moved)


def _delete_course(conn, course_id: object, dry_run: bool = False) -> None:
    if dry_run:
        return
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.backup_new_course WHERE id = %s::uuid", (str(course_id),))
    conn.commit()


def dedupe_for_spreadsheet(conn, spreadsheet_name: str, dry_run: bool = False) -> Dict[str, int]:
    rows = _get_courses_by_spreadsheet_name(conn, spreadsheet_name)
    if len(rows) <= 1:
        return {"groups": 1, "kept": len(rows), "deleted": 0, "reassigned_classes": 0}

    sids = {r.get('student_id') for r in rows}
    if len(sids) != 1:
        logging.info("* %s | duplicates found but multiple student_id values present; skipping", spreadsheet_name)
        return {"groups": 1, "kept": len(rows), "deleted": 0, "reassigned_classes": 0}

    # Count classes
    ids = [r.get('id') for r in rows]
    counts = _get_class_counts(conn, ids)

    # Split by presence of classes
    with_classes = [r for r in rows if counts.get(str(r.get('id')), 0) > 0]
    without_classes = [r for r in rows if counts.get(str(r.get('id')), 0) == 0]

    cols = fetch_table_columns(conn, 'backup_new_course')

    deleted = 0
    reassigned = 0

    logging.info("* %s | duplicates: %d", spreadsheet_name, len(rows) - 1)

    if len(with_classes) >= 2:
        # Consolidate: pick the best-filled, move all classes to it, delete others
        keep = _choose_course_to_keep(rows, counts, cols)
        keep_id = keep.get('id')
        logging.info("keep course id=%s (best attributes/classes)", keep_id)
        for r in rows:
            cid = r.get('id')
            if cid == keep_id:
                continue
            moved = _reassign_classes(conn, cid, keep_id, dry_run=dry_run)
            if moved:
                logging.info("reassign: move %s classes course id=%s -> id=%s", moved, cid, keep_id)
                reassigned += moved
            _delete_course(conn, cid, dry_run=dry_run)
            logging.info("duplicate: delete course id=%s (post-reassign)", cid)
            deleted += 1
        return {"groups": 1, "kept": 1, "deleted": deleted, "reassigned_classes": reassigned}

    if len(with_classes) == 1:
        # Keep the one with classes; remove the zero-class duplicates
        keep_id = with_classes[0].get('id')
        logging.info("keep course id=%s (has classes)", keep_id)
        for r in without_classes:
            cid = r.get('id')
            _delete_course(conn, cid, dry_run=dry_run)
            logging.info("duplicate: delete course id=%s | 0 classes", cid)
            deleted += 1
        return {"groups": 1, "kept": 1, "deleted": deleted, "reassigned_classes": reassigned}

    # No classes on any, keep the best-filled (or lowest id) and delete the rest
    keep = _choose_course_to_keep(rows, counts, cols)
    keep_id = keep.get('id')
    logging.info("keep course id=%s (best attributes; all 0 classes)", keep_id)
    for r in rows:
        cid = r.get('id')
        if cid == keep_id:
            continue
        _delete_course(conn, cid, dry_run=dry_run)
        logging.info("duplicate: delete course id=%s | 0 classes", cid)
        deleted += 1
    return {"groups": 1, "kept": 1, "deleted": deleted, "reassigned_classes": reassigned}


def _unique_spreadsheet_names_from_input(input_path: str) -> List[str]:
    names = []
    seen = set()
    for line in _read_input_lines(input_path):
        s = line.strip()
        if not s:
            continue
        name = extract_filename(s)
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def main():
    load_dotenv()

    p = argparse.ArgumentParser(description="Deduplicate backup_new_course by spreadsheet_name, consolidating classes to the best record")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--spreadsheet", help="Single spreadsheet_name to deduplicate")
    g.add_argument("--input", help="File with paths; extract spreadsheet_name per line and deduplicate for those names")
    g.add_argument("--all", action="store_true", help="Scan DB for all duplicate spreadsheet_name groups")
    p.add_argument("--dry-run", action="store_true", help="Log actions without writing to DB")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = p.parse_args()

    setup_logging(args.verbose)

    conn = get_conn()
    try:
        if args.spreadsheet:
            names = [args.spreadsheet]
        elif args.input:
            names = _unique_spreadsheet_names_from_input(args.input)
        elif args.all:
            names = _get_duplicate_spreadsheet_names(conn)
        else:
            p.error("Specify one of --spreadsheet, --input, or --all")
            return

        total_deleted = 0
        total_moved = 0
        processed = 0

        for name in names:
            summary = dedupe_for_spreadsheet(conn, name, dry_run=args.dry_run)
            total_deleted += summary.get("deleted", 0)
            total_moved += summary.get("reassigned_classes", 0)
            processed += 1

        logging.info(
            "Done. Groups processed=%s, courses deleted=%s, classes reassigned=%s",
            processed, total_deleted, total_moved
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
