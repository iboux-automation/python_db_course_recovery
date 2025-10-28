#!/usr/bin/env python3
import argparse
import logging
import os

from dotenv import load_dotenv
from db_conn import get_conn
from logic_copy import orchestrate


def setup_logging(verbose: bool = False) -> None:
    """Configure root logger in INFO/DEBUG level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(message)s')


def main():
    """Entry point for command-line execution."""
    # Load environment variables from .env if present (local dev)
    load_dotenv()
    parser = argparse.ArgumentParser(description='Update public.backup_new_course from input paths (customer_type, company, language, 2-1, taas school)')
    parser.add_argument('--input', default='b2b_paths/b2b_paths.cleaned.csv', help='Input file with one path per line')
    parser.add_argument('--dry-run', action='store_true', help='Do not write to DB, only log actions')
    parser.add_argument('--verbose', action='store_true', help='Verbose logging')
    args = parser.parse_args()

    setup_logging(args.verbose)

    if not os.path.exists(args.input):
        logging.warning(f"Input file not found: {args.input}")

    logging.info(
        "Starting update run with input=%s dry_run=%s verbose=%s",
        args.input,
        args.dry_run,
        args.verbose,
    )
    if args.dry_run:
        logging.info("DRY RUN: no database writes will be performed")

    conn = get_conn()
    try:
        summary = orchestrate(conn, args.input, dry_run=args.dry_run)
        logging.info(
            "Done. Paths processed=%s, matched rows=%s, rows updated=%s",
            summary['paths_processed'], summary['matched_rows'], summary['rows_updated']
        )
    finally:
        conn.close()


if __name__ == '__main__':
    main()
