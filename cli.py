#!/usr/bin/env python3
import argparse
import logging
from difflib import SequenceMatcher
from typing import Optional, Sequence, Tuple

from dotenv import load_dotenv

from db_conn import get_conn

CANONICAL_NO_SHOW = "No show"
CANONICAL_LATE_CANCEL = "Less than 24 hours cancellation"
NO_SHOW_COMPACT = "noshow"


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def _looks_like_no_show(text: str) -> bool:
    """Detects 'no show' even with typos or extra text."""
    lowered = text.lower()
    if "no show" in lowered:
        return True
    compact = "".join(ch for ch in lowered if ch.isalpha())
    similarity = SequenceMatcher(None, compact, NO_SHOW_COMPACT).ratio()
    return similarity >= 0.8


def normalize_cancellation(raw_value: Optional[str]) -> Optional[str]:
    if raw_value is None:
        return None
    cleaned = raw_value.strip()
    if cleaned == "":
        return None
    if cleaned in (CANONICAL_NO_SHOW, CANONICAL_LATE_CANCEL):
        return cleaned
    if _looks_like_no_show(cleaned):
        return CANONICAL_NO_SHOW
    if "24" in cleaned:
        return CANONICAL_LATE_CANCEL
    return None


def fetch_class_rows(conn) -> Sequence[Tuple[int, Optional[str]]]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, cancellation FROM class")
        return cur.fetchall()


def apply_updates(conn, updates, dry_run: bool) -> None:
    with conn.cursor() as cur:
        for new_value, class_id in updates:
            cur.execute(
                "UPDATE class SET cancellation = %s WHERE id = %s",
                (new_value, class_id),
            )
    if dry_run:
        conn.rollback()
        logging.info("DRY RUN: rolled back %s updates", len(updates))
    else:
        conn.commit()
        logging.info("Committed %s updates", len(updates))


def normalize_all(conn, dry_run: bool) -> dict:
    rows = fetch_class_rows(conn)
    stats = {
        "rows_scanned": len(rows),
        "updated": 0,
        "to_no_show": 0,
        "to_late_cancel": 0,
        "to_null": 0,
    }
    updates = []

    for class_id, raw_value in rows:
        new_value = normalize_cancellation(raw_value)
        if new_value == raw_value:
            continue
        stats["updated"] += 1
        if new_value == CANONICAL_NO_SHOW:
            stats["to_no_show"] += 1
        elif new_value == CANONICAL_LATE_CANCEL:
            stats["to_late_cancel"] += 1
        else:
            stats["to_null"] += 1
        updates.append((new_value, class_id))

    if updates:
        apply_updates(conn, updates, dry_run=dry_run)
    else:
        logging.info("No changes needed; all rows already normalized.")
    return stats


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Normalize class.cancellation values into three allowed states."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and log, but do not write changes to the database.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging for troubleshooting."
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    logging.info("Starting normalization dry_run=%s", args.dry_run)
    if args.dry_run:
        logging.info("DRY RUN: no database writes will be persisted.")

    conn = get_conn()
    try:
        stats = normalize_all(conn, dry_run=args.dry_run)
    finally:
        conn.close()

    logging.info(
        "Done. rows_scanned=%s updated=%s -> [no_show:%s, <24h:%s, null:%s]",
        stats["rows_scanned"],
        stats["updated"],
        stats["to_no_show"],
        stats["to_late_cancel"],
        stats["to_null"],
    )


if __name__ == "__main__":
    main()
