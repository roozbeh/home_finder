#!/usr/bin/env python3
"""Print or follow gallery repair progress without restarting the worker."""
import argparse
import time
from datetime import datetime, timezone
from pymongo import MongoClient
from fetch_details import MONGO_URI,MONGO_DB


def progress_line(stats,total):
    checked=stats.get('scanned',0)
    percent=min(100,100*checked/total) if total else 0
    return ('Backfill progress: {:,} / {:,} listings checked ({:.1f}%) | '
            'Repaired: {:,} | Failed: {:,} | Already complete: {:,} | Retry later: {:,}').format(
                checked,total,percent,stats.get('succeeded',0),stats.get('failed',0),
                stats.get('skipped',0),stats.get('cooldown',0))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--follow',action='store_true')
    parser.add_argument('--interval',type=int,default=30)
    args=parser.parse_args()
    if args.interval<1:parser.error('interval must be positive')
    # One shared client across polling iterations; no per-poll client creation.
    with MongoClient(MONGO_URI) as client:
        db=client[MONGO_DB]
        run=db.mls_runs.find_one({'type':'photo_gallery_backfill','scope':'all'},sort=[('started_at',-1)])
        if not run:raise SystemExit('No all-property gallery backfill found.')
        run_id=run['_id']
        # Older workers did not save a denominator. Count the full scan scope
        # once; keep it fixed so the displayed percentage is stable.
        total=run.get('total_scope') or db.mls_listings.count_documents({})
        while True:
            run=db.mls_runs.find_one({'_id':run_id})
            if not run:raise SystemExit('Backfill run record no longer exists.')
            print(datetime.now(timezone.utc).strftime('%H:%M:%S UTC'),progress_line(run,total),flush=True)
            if run.get('status')!='running' or run.get('finished_at'):
                print('Backfill status:',run.get('status'),flush=True);break
            if not args.follow:break
            time.sleep(args.interval)

if __name__=='__main__':main()
