from __future__ import annotations

import argparse
import os
from pathlib import Path

from .audit import audit_mongo, write_audit_documents
from .market_history import load_closed_listings, write_history_outputs


def main() -> None:
    parser = argparse.ArgumentParser(prog="flip-finder")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="run a read-only MLS MongoDB audit")
    audit.add_argument("--source", choices=["mongo"], default="mongo")
    audit.add_argument("--database", default=os.getenv("MONGO_DB", "mls"))
    audit.add_argument("--collection", default=os.getenv("MONGO_COLLECTION", "mls_listings"))
    audit.add_argument("--output-dir", type=Path, default=Path("audit_artifacts"))
    audit.add_argument("--scan-limit", type=int, default=None, help="optional cap for a diagnostic sample")
    history = sub.add_parser("flip-history", help="read-only observed short-hold resale analysis")
    history.add_argument("--database", default=os.getenv("MONGO_DB", "mls"))
    history.add_argument("--collection", default=os.getenv("MONGO_COLLECTION", "mls_listings"))
    history.add_argument("--output-dir", type=Path, default=Path("outputs/market_history"))
    history.add_argument("--max-hold-days", type=int, default=365)
    args = parser.parse_args()
    if args.command == "audit":
        uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
        if not uri:
            parser.error("Set MONGODB_URI (or MONGO_URI) in the environment; it is never read from source control.")
        report = audit_mongo(uri, args.database, args.collection, args.scan_limit)
        audit_path, gaps_path = write_audit_documents(report, args.output_dir)
        print(f"Read-only audit complete: {audit_path}")
        print(f"Gap report: {gaps_path}")
    if args.command == "flip-history":
        uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
        if not uri:
            parser.error("Set MONGODB_URI (or MONGO_URI) in the environment; it is never read from source control.")
        listings = load_closed_listings(uri, args.database, args.collection)
        pairs, cities, subdivisions, report = write_history_outputs(listings, args.output_dir, args.max_hold_days)
        print(f"Read-only resale history complete: {report}")
        print(f"Pairs: {pairs}; city summary: {cities}; subdivision summary: {subdivisions}")


if __name__ == "__main__":
    main()
