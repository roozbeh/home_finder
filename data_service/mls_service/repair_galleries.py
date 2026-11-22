#!/usr/bin/env python3
"""Resumable gallery repair; prioritize demo listings, then drain the full backlog."""
import argparse
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from gallery_progress import progress_line
from fetch_details import (MONGO_URI,MONGO_DB,FLIPDB_DATABASE,DETAILS_URL_TPL,
    fetch_gallery_photos,get_active_session,build_session,assert_authenticated_response,MLSAuthenticationError)


def valid_url(url):
    return isinstance(url,str) and url.startswith('https://') and bool(re.search(
        r'/HD_[^?]+\.jpe?g|/bigphoto/[^?]+\.jpe?g|media\.mlslmedia\.com/property/|media\.crmls\.org/media[sz]/[0-9a-f-]{36}\.jpe?g',url,re.I))


def expected_count(doc):
    try:return int((doc.get('details') or {}).get('photocount') or 0)
    except (TypeError,ValueError):return 0


def needs_repair(doc):
    photos=doc.get('photos') or []
    good=sum(valid_url(url) for url in set(photos) if isinstance(url,str))
    return good==0 or good<expected_count(doc)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope',choices=['demo','all'],default='all')
    parser.add_argument('--limit',type=int,default=0,help='maximum attempts; 0 drains all eligible listings')
    parser.add_argument('--delay',type=float,default=1,help='minimum delay between listings')
    parser.add_argument('--audit-only',action='store_true')
    args=parser.parse_args()
    if args.limit<0 or args.delay<0:parser.error('limit and delay must be nonnegative')
    # One shared client for this sequential worker; default driver pool settings.
    with MongoClient(MONGO_URI) as client:
        db=client[MONGO_DB];coll=db.mls_listings;result=client[FLIPDB_DATABASE]
        priority=set()
        for insight in result.active_renovation_insights.find({}, {'listing_id':1,'comps.listing_id':1}):
            priority.add(insight.get('listing_id'))
            priority.update(c.get('listing_id') for c in insight.get('comps',[]))
        pair_keys=result.renovation_insights.distinct('pair_key')
        for pair in result.flipdb.find({'pair_key':{'$in':pair_keys}},{'buy_mls':1,'sell_mls':1}):
            priority.update([pair.get('buy_mls'),pair.get('sell_mls')])
        priority=sorted(mid for mid in priority if isinstance(mid,str))
        queries=[{'LISTING_ID':{'$in':priority}}]
        if args.scope=='all':queries.append({'LISTING_ID':{'$nin':priority}})
        total_scope=sum(coll.count_documents(query) for query in queries)
        owner=str(uuid.uuid4());now=datetime.now(timezone.utc);run_id=None
        stats={'scanned':0,'processed':0,'succeeded':0,'failed':0,'skipped':0,'cooldown':0}
        status='running'
        if not args.audit_only:
            try:
                db.gallery_repair_locks.update_one({'_id':'global','$or':[{'expires_at':{'$lt':now}},{'owner':owner}]},
                    {'$set':{'owner':owner,'expires_at':now+timedelta(minutes=5)}},upsert=True)
            except DuplicateKeyError:
                print('Another gallery repair worker holds the lease; skipped.',flush=True)
                return
            run_id=db.mls_runs.insert_one({'type':'photo_gallery_backfill','scope':args.scope,'status':'running','started_at':now,'owner':owner,'total_scope':total_scope}).inserted_id
        print(progress_line(stats,total_scope),flush=True)
        try:
            http=None
            for query in queries:
                # Small cursor batches keep the server cursor active during API work.
                cursor=coll.find(query,{'LISTING_ID':1,'DCID':1,'photos':1,'details':1,'photo_gallery_repair':1}).batch_size(25)
                for doc in cursor:
                    stats['scanned']+=1
                    if not needs_repair(doc):stats['skipped']+=1;continue
                    next_retry=(doc.get('photo_gallery_repair') or {}).get('next_retry_at')
                    if next_retry and next_retry.replace(tzinfo=timezone.utc)>datetime.now(timezone.utc):
                        stats['cooldown']+=1;continue
                    if args.limit and stats['processed']>=args.limit:
                        status='batch_complete';break
                    stats['processed']+=1
                    mid=doc.get('LISTING_ID','unknown')
                    if args.audit_only:
                        print(mid,'needs repair',flush=True);continue
                    now=datetime.now(timezone.utc)
                    db.gallery_repair_locks.update_one({'_id':'global','owner':owner},{'$set':{'expires_at':now+timedelta(minutes=5)}})
                    try:
                        if http is None:
                            try:http=build_session(get_active_session()['cookies'])
                            except RuntimeError as exc:raise MLSAuthenticationError(str(exc)) from exc
                        dcid=doc.get('DCID')
                        if not dcid:raise ValueError('Missing MLS DCID; importer must enrich listing identity')
                        detail=doc.get('details') or {}
                        updates={}
                        if not detail:
                            response=http.get(DETAILS_URL_TPL.format(dcid=dcid),timeout=30)
                            assert_authenticated_response(response);response.raise_for_status()
                            payload=response.json().get('data') or []
                            if not payload:raise ValueError('MLS returned no listing details')
                            detail=payload[0];updates={'details':detail,'details_fetched':now.isoformat()}
                        photos=fetch_gallery_photos(http,dcid)
                        photos=list(dict.fromkeys(url for url in photos if valid_url(url)))
                        expected=expected_count({'details':detail})
                        if not photos:raise ValueError('MLS gallery returned no supported image references')
                        if expected and len(photos)!=expected:raise ValueError('Gallery count mismatch: {} of {}'.format(len(photos),expected))
                        with http.get(photos[0],timeout=20,stream=True) as image:
                            image.raise_for_status()
                            first=next(image.iter_content(32),b'')
                            if not image.headers.get('content-type','').startswith('image/') or not first.startswith((b'\xff\xd8',b'\x89PNG',b'RIFF',b'GIF8')):
                                raise ValueError('Recovered primary does not return image bytes')
                        updates.update(photos=photos,photo_gallery_fetched=now.isoformat(),photo_gallery_repair={'status':'complete','checked_at':now,'count':len(photos)})
                        coll.update_one({'_id':doc['_id']},{'$set':updates})
                        stats['succeeded']+=1;print('OK',mid,len(photos),'photos',flush=True)
                    except MLSAuthenticationError:
                        status='blocked_auth';raise
                    except Exception as exc:
                        stats['failed']+=1
                        coll.update_one({'_id':doc['_id']},{'$set':{'photo_gallery_repair':{'status':'failed','checked_at':now,'error':str(exc)[:500],'next_retry_at':now+timedelta(hours=24)}}})
                        print('FAILED',mid,type(exc).__name__,str(exc)[:160],flush=True)
                    db.mls_runs.update_one({'_id':run_id},{'$set':{**stats,'updated_at':datetime.now(timezone.utc)}})
                    print(progress_line(stats,total_scope),flush=True)
                    time.sleep(args.delay)
                if status=='batch_complete':break
            if status=='running':status='complete' if not stats['failed'] else 'complete_with_failures'
        except Exception as exc:
            if status!='blocked_auth':status='failed'
            if run_id:db.mls_runs.update_one({'_id':run_id},{'$set':{'last_error':str(exc)[:500]}})
            raise
        finally:
            if run_id:
                db.mls_runs.update_one({'_id':run_id},{'$set':{**stats,'status':status,'finished_at':datetime.now(timezone.utc)}})
                db.gallery_repair_locks.delete_one({'_id':'global','owner':owner})
            print(status,stats,flush=True)
            print(progress_line(stats,total_scope),flush=True)

if __name__=='__main__':main()
