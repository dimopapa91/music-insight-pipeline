#!/usr/bin/env python3
"""One-off cleanup: remove em/en dashes from stored AI insights.

The `searches.claude_insight` column holds AI-generated artist insights created
before the prompts were told to avoid em dashes. This script rewrites those
dashes to commas so existing content reads consistently. New content is already
clean at generation time, so this only needs to run once.

Safety:
* Dry run by default. Prints how many rows would change and shows samples.
  Pass --apply to actually write.
* Before writing, it saves the original (id, claude_insight) of every affected
  row to a timestamped JSON backup file next to this script.
* Idempotent: rows with no dash are skipped, so re-running is safe.
* Uses DATABASE_URL exactly like the app (run via `railway run` in production).

Usage:
    python scripts/strip_emdashes.py            # dry run, no changes
    python scripts/strip_emdashes.py --apply    # perform the update
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import psycopg2

try:
    from dotenv import load_dotenv
    load_dotenv()  # pick up DATABASE_URL from a local .env if present
except ImportError:
    pass

DASH_RE = re.compile(r"\s*[—–]\s*")  # em (—) or en (–) dash


def clean(text):
    """Replace em/en dashes with a comma, tidy spacing. Hyphens are left alone."""
    out = DASH_RE.sub(", ", text)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out


def get_connection():
    url = os.getenv("DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    # Local dev fallback, matching db.py
    return psycopg2.connect(dbname="music_insights", user=os.getenv("USER"))


def main():
    apply = "--apply" in sys.argv[1:]
    conn = get_connection()
    conn.autocommit = False
    changed = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, claude_insight FROM searches "
                "WHERE claude_insight LIKE %s OR claude_insight LIKE %s",
                ("%—%", "%–%"),
            )
            for row_id, insight in cur.fetchall():
                new = clean(insight)
                if new != insight:
                    changed.append((row_id, insight, new))

        print(f"Rows containing an em/en dash to fix: {len(changed)}")
        for row_id, old, new in changed[:3]:
            snippet_old = old[:160].replace("\n", " ")
            snippet_new = new[:160].replace("\n", " ")
            print(f"\n  id={row_id}\n   before: ...{snippet_old}...\n   after:  ...{snippet_new}...")

        if not changed:
            print("\nNothing to do. All stored insights are already clean.")
            return

        if not apply:
            print("\nDry run only. Re-run with --apply to write these changes.")
            return

        # Backup before writing.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   f"strip_emdashes_backup_{stamp}.json")
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump([{"id": r, "claude_insight": o} for r, o, _ in changed],
                      f, ensure_ascii=False, indent=2)
        print(f"\nBacked up {len(changed)} original rows to:\n  {backup_path}")

        with conn.cursor() as cur:
            for row_id, _old, new in changed:
                cur.execute(
                    "UPDATE searches SET claude_insight = %s WHERE id = %s",
                    (new, row_id),
                )
        conn.commit()
        print(f"Updated {len(changed)} rows. Done.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
