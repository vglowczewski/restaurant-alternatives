"""Refresh a FIXED set of Yelp business IDs and delete expired cache entries.

Usage:
    python refresh.py ID [ID ...]
    python refresh.py --ids-file ids.txt      (one Yelp ID per line; '#' comments)
    python refresh.py --purge-only            (only delete expired entries)

No bulk discovery and no scheduling: only the IDs you pass are refreshed.
A failed refresh leaves the old entry untouched; it still expires after 24 hours.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import data_store as ds
from yelp_client import YelpCallCapReached, YelpClient, YelpError, YelpSettings


def refresh(conn, yelp_ids: list[str], client: YelpClient | None = None) -> dict:
    """Returns {"refreshed": [...], "failed": {id: reason}, "expired_deleted": n}."""
    report = {"refreshed": [], "failed": {}, "expired_deleted": 0}
    ids = list(dict.fromkeys(i.strip() for i in yelp_ids if i and i.strip()))
    if ids:
        client = client or YelpClient(conn, YelpSettings.load())
        if not client.settings.usable:
            report["failed"] = {i: "Yelp key missing or Yelp disabled" for i in ids}
        else:
            for i, yelp_id in enumerate(ids):
                try:
                    rec = client.fetch_details(yelp_id)
                except YelpError as e:
                    report["failed"][yelp_id] = str(e)
                    if e.fatal or isinstance(e, YelpCallCapReached):
                        for rest in ids[i + 1:]:
                            report["failed"][rest] = f"skipped: {e}"
                        break
                    continue
                if rec:
                    report["refreshed"].append(yelp_id)
                else:
                    report["failed"][yelp_id] = "not found"
    now = client.now() if client else None
    report["expired_deleted"] = ds.delete_expired(conn, now)
    return report


def _read_ids(path: str) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.split("#", 1)[0].strip() for ln in lines if ln.split("#", 1)[0].strip()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ids", nargs="*", help="Yelp business IDs to refresh")
    p.add_argument("--ids-file", help="file with one Yelp business ID per line")
    p.add_argument("--purge-only", action="store_true", help="only delete expired entries")
    p.add_argument("--db", default=str(ds.DEFAULT_DB_PATH), help="SQLite path")
    args = p.parse_args(argv)

    ids = [] if args.purge_only else list(args.ids) + (_read_ids(args.ids_file) if args.ids_file else [])
    conn = ds.connect(args.db)
    report = refresh(conn, ids)
    print(f"refreshed: {len(report['refreshed'])}, failed: {len(report['failed'])}, "
          f"expired deleted: {report['expired_deleted']}")
    for yelp_id, reason in report["failed"].items():
        print(f"  failed {yelp_id}: {reason}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
