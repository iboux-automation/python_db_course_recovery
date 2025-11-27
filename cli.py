#!/usr/bin/env python3
import argparse
import logging
from difflib import SequenceMatcher
from typing import Optional, Sequence, Tuple

from dotenv import load_dotenv
from psycopg2.extras import execute_batch

from db_conn import get_conn

CANONICAL_NO_SHOW = "No show"
CANONICAL_LATE_CANCEL = "Less than 24 hours cancellation"
NO_SHOW_COMPACT = "noshow"
PROGRESS_LOG_EVERY = 500
UPDATE_BATCH_SIZE = 1000
UPDATE_LOG_EVERY = 5000


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


def derive_from_comments(comments: Optional[str]) -> Optional[str]:
    if not comments:
        return None
    lowered = comments.lower()
    if "no show" in lowered:
        return CANONICAL_NO_SHOW
    if "24 hours" in lowered:
        return CANONICAL_LATE_CANCEL
    return None


def fetch_class_rows(conn) -> Sequence[Tuple[int, Optional[str], Optional[str]]]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, cancellation, class_comments FROM class")
        return cur.fetchall()


def apply_updates(conn, updates, dry_run: bool) -> None:
    if dry_run:
        logging.info("DRY RUN: skipping %s update statements (no DB writes)", len(updates))
        return

    total = len(updates)
    if total == 0:
        logging.info("No DB updates to apply.")
        return

    logging.info("Applying %s updates in batches of %s", total, UPDATE_BATCH_SIZE)
    with conn.cursor() as cur:
        for start in range(0, total, UPDATE_BATCH_SIZE):
            batch = updates[start : start + UPDATE_BATCH_SIZE]
            execute_batch(
                cur,
                "UPDATE class SET cancellation = %s WHERE id = %s",
                batch,
                page_size=UPDATE_BATCH_SIZE,
            )
            applied = start + len(batch)
            if applied % UPDATE_LOG_EVERY == 0 or applied == total:
                pct = (applied / total) * 100
                logging.info("update progress: applied=%s/%s (%.1f%%)", applied, total, pct)
    conn.commit()
    logging.info("Committed %s updates", total)


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

    for idx, (class_id, raw_value, comments) in enumerate(rows, start=1):
        new_value = normalize_cancellation(raw_value)
        source = "cancellation"
        if new_value is None:
            new_value = derive_from_comments(comments)
            if new_value is not None:
                source = "class_comments"

        if new_value == raw_value:
            continue
        stats["updated"] += 1
        if new_value == CANONICAL_NO_SHOW:
            stats["to_no_show"] += 1
        elif new_value == CANONICAL_LATE_CANCEL:
            stats["to_late_cancel"] += 1
        else:
            stats["to_null"] += 1
        logging.debug(
            "update class id=%s: cancellation %r -> %r (source=%s)",
            class_id,
            raw_value,
            new_value,
            source,
        )
        updates.append((new_value, class_id))
        if idx % PROGRESS_LOG_EVERY == 0:
            logging.info(
                "progress: scanned=%s updated=%s [no_show=%s <24h=%s null=%s]",
                idx,
                stats["updated"],
                stats["to_no_show"],
                stats["to_late_cancel"],
                stats["to_null"],
            )

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
