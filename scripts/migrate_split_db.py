"""
Migrate data/market.duckdb → data/crypto/{exchange}.duckdb

Splits the monolithic DuckDB file into per-exchange files to eliminate
cross-process lock contention between crawlers.

Usage:
    python scripts/migrate_split_db.py              # dry-run (verify counts only)
    python scripts/migrate_split_db.py --execute    # actually migrate
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
from config.domains import get_db_path, OHLCV_SCHEMA

OLD_DB = Path(__file__).parent.parent / "data" / "market.duckdb"
EXCHANGES = ["binance", "okx", "bybit"]


def count_by_exchange(con):
    """Return {exchange: row_count} from the old DB."""
    rows = con.execute(
        "SELECT exchange, COUNT(*) as cnt FROM ohlcv_1m GROUP BY exchange ORDER BY exchange"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def migrate():
    parser = argparse.ArgumentParser(description="Split market.duckdb by exchange")
    parser.add_argument("--execute", action="store_true", help="Actually perform the migration (default: dry-run)")
    args = parser.parse_args()

    if not OLD_DB.exists():
        print("ERROR: {} does not exist".format(OLD_DB))
        sys.exit(1)

    # Open old DB read-only to get counts
    old_con = duckdb.connect(str(OLD_DB), read_only=True)
    counts = count_by_exchange(old_con)
    total = sum(counts.values())
    old_con.close()

    print("=== Source: {} ===".format(OLD_DB))
    print("Total rows: {:,}".format(total))
    for ex, cnt in sorted(counts.items()):
        print("  {}: {:,}".format(ex, cnt))
    print()

    if not args.execute:
        print("Dry-run mode. Pass --execute to perform the migration.")
        return

    # Migrate each exchange
    migrated_total = 0
    for ex in EXCHANGES:
        if ex not in counts:
            print("Skipping {} (no data in source)".format(ex))
            continue

        target_path = get_db_path("crypto", ex)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        print("Migrating {} ({:,} rows) → {} ...".format(ex, counts[ex], target_path))

        # Create target DB and schema
        target_con = duckdb.connect(str(target_path))
        target_con.execute(OHLCV_SCHEMA)

        # Attach old DB and copy data
        target_con.execute("ATTACH '{}' AS old (READ_ONLY)".format(str(OLD_DB)))
        target_con.execute(
            "INSERT OR IGNORE INTO ohlcv_1m SELECT * FROM old.ohlcv_1m WHERE exchange='{}'".format(ex)
        )
        target_con.execute("DETACH old")

        # Verify
        result = target_con.execute("SELECT COUNT(*) FROM ohlcv_1m").fetchone()
        new_count = result[0]
        target_con.close()

        status = "OK" if new_count == counts[ex] else "MISMATCH (expected {:,})".format(counts[ex])
        print("  {} → {:,} rows [{}]".format(ex, new_count, status))
        migrated_total += new_count

    print()
    print("=== Migration complete ===")
    print("Source total:   {:,}".format(total))
    print("Migrated total: {:,}".format(migrated_total))

    if migrated_total == total:
        print("All rows migrated successfully.")
        print()
        print("Next steps:")
        print("  1. Verify API: curl localhost:8402/v1/coverage")
        print("  2. Start crawlers: python crawler/crypto_crawler.py --exchange binance --mode backfill --days 1")
        print("  3. Once stable, delete the old file: rm data/market.duckdb data/market.duckdb.wal")
    else:
        print("WARNING: Row count mismatch! Do NOT delete the old file.")


if __name__ == "__main__":
    migrate()
