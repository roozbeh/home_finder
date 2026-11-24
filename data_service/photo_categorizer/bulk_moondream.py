"""Classify stored MLS galleries locally, active first, with Mongo checkpoints."""
import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from functools import partial
from queue import SimpleQueue
from threading import local
from time import perf_counter, sleep
from urllib.parse import urlsplit

import requests
from PIL import Image
from pymongo import MongoClient

from buyer_media import mls_photo_variant
from categorize_photos import FLIPDB_DATABASE, MONGO_DB, MONGO_URI, OLLAMA_URL, jpeg_source
from gallery_photo_labels import label_priority, photo_id_for, store_gallery_labels
from photo_inference import Inference, InferenceError

STAGES = (
    ('active', ('ACTV', 'NEW', 'BOMK', 'PCH')),
    ('sold', ('SLD',)),
)
FLIP_STAGE = 'flips'
REPORT_STAGES = ((FLIP_STAGE, None), *STAGES)
METHOD = 'moondream_caption_rules_v1'
LABEL_SOURCE = 'moondream-mls-backfill'
RESULT_COLLECTION = 'mls_photo_classifications'
JOB_COLLECTION = 'mls_photo_backfill_jobs'
PROGRESS_SECONDS = 15
PUBLIC_CHECK_DAYS = 90
RETRY_DELAYS = (2, 8)
PROMPT = 'Describe this photo in one sentence.'
PROJECTION = {'LISTING_ID': 1, 'SOURCE_MLS': 1, 'RAW_STATUS': 1, 'photos': 1,
              'details.PRIMARY_PHOTO_URI': 1, 'gallery_photo_labels': 1}
SAVE_BATCH_SIZE = 8
MEDIA_HOSTS = frozenset({'media.mlslmedia.com', 'media.crmls.org'})
MEDIA_SUFFIXES = ('.cdn-connectmls.com', '.rapmls.com')
LABELS = (
    ('floor_plan', r'\bfloor[ -]?plans?\b|\bblueprints?\b'),
    ('bathroom', r'\bbathroom\b|\bshower curtain\b|\bbathtub\b'),
    ('kitchen', r'\bkitchen\b|\bcooktop\b|\bdishwasher\b'),
    ('bedroom', r'\bbedroom\b|\bnightstand\b|\bbed with\b'),
    ('laundry', r'\blaundry\b|\bwasher and dryer\b'),
    ('garage', r'\bgarage\b|\bcarport\b'),
    ('living_room', r'\bliving room\b|\bfamily room\b|\bsofa\b|\bcouch\b'),
    ('dining_room', r'\bdining room\b|\bdining table\b'),
    ('amenities', r'\bfitness center\b|\bgym\b|\bfitness room\b'),
    ('exterior', r'\bexterior\b|\bfa[çc]ade\b|\bfront of (?:a |the )?(?:house|building)\b'),
    ('outdoor', r'\bpatio\b|\bbackyard\b|\bbalcony\b|\bdeck\b'),
    ('hallway', r'\bhallway\b|\bentryway\b|\bcorridor\b'),
)


def now():
    return datetime.now(timezone.utc)


def trusted_photo(raw):
    if not isinstance(raw, str):
        return False
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ''
        return (parts.scheme == 'https' and not parts.username and not parts.password
                and parts.port in (None, 443) and not parts.fragment
                and (host in MEDIA_HOSTS or host.endswith(MEDIA_SUFFIXES)))
    except ValueError:
        return False


def photo_references(listing):
    gallery = listing.get('photos')
    if not isinstance(gallery, list):
        gallery = []
    primary = (listing.get('details') or {}).get('PRIMARY_PHOTO_URI')
    raw = list(dict.fromkeys(value for value in (*gallery, primary) if isinstance(value, str) and value))
    urls = [value for value in raw if trusted_photo(value)]
    return urls, len(raw) - len(urls)


def classification_urls(urls):
    """Infer once per stable gallery photo, trying the full-size reference first."""
    photos = {}
    for url in urls:
        photo_id = photo_id_for(url)
        if photo_id is None:
            continue
        _, preferred = mls_photo_variant(url)
        variants = photos.setdefault(photo_id, [])
        if preferred:
            variants.insert(0, url)
        else:
            variants.append(url)
    return photos


def reusable_photo_ids(listing, photos, model):
    return {row['photo_id'] for row in listing.get('gallery_photo_labels') or ()
            if isinstance(row, dict) and row.get('category') and row.get('photo_id') in photos
            and (label_priority(row) > 2
                 or (row.get('model') == model and row.get('method') == METHOD))}


def infer_label(caption):
    if re.search(LABELS[0][1], caption, flags=re.I):
        return 'floor_plan'
    matches = [(match.start(), category) for category, pattern in LABELS[1:]
               if (match := re.search(pattern, caption, flags=re.I))]
    return min(matches)[1] if matches else 'other'


def caption_for(engine, image):
    reply = engine._local_request('/api/generate', {
        'model': engine.model, 'stream': False, 'keep_alive': '10m',
        'prompt': PROMPT, 'images': [image['data']], 'options': {'temperature': 0, 'num_predict': 64},
    }, timeout=engine.ollama_timeout)
    caption = str(reply.get('response') or '').strip()
    if not caption:
        raise ValueError('Empty caption')
    return caption[:240]


def photo_key(listing_id, url, model):
    material = json.dumps([str(listing_id), url, model, METHOD], separators=(',', ':'))
    return hashlib.sha256(material.encode()).hexdigest()


def read_image(http, url):
    with http.get(url, stream=True, allow_redirects=False, timeout=(8, 30)) as response:
        if 300 <= response.status_code < 400:
            raise ValueError('Redirected media')
        response.raise_for_status()
        if not response.headers.get('content-type', '').lower().startswith('image/'):
            raise ValueError('Response is not an image')
        chunks = bytearray()
        for part in response.iter_content(64 * 1024):
            chunks.extend(part)
            if len(chunks) > 12_000_000:
                raise ValueError('Image exceeds 12 MB')
        return jpeg_source(bytes(chunks))


def flip_listing_ids(source, pairs):
    ids = {
        listing_id for pair in pairs.find({}, {'_id': 0, 'buy_mls': 1, 'sell_mls': 1})
        for key in ('buy_mls', 'sell_mls')
        if isinstance((listing_id := pair.get(key)), str) and listing_id
    }
    if not ids:
        return []
    return [row['_id'] for row in source.find({'LISTING_ID': {'$in': sorted(ids)}}, {'_id': 1}).sort('_id', 1)]


def listing_totals(source, flip_ids):
    return {FLIP_STAGE: len(flip_ids),
            **{stage: source.count_documents({'RAW_STATUS': {'$in': list(statuses)}}, maxTimeMS=25000)
               for stage, statuses in STAGES}}


def public_check_timestamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            for pattern in ('%B %d %Y %I:%M%p', '%m/%d/%Y'):
                try:
                    value = datetime.strptime(value, pattern)
                    break
                except ValueError:
                    continue
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return None


def fresh_display_checks(source, query, days):
    checked = now()
    cutoff = checked - timedelta(days=days)
    return [row['_id'] for row in source.find(query, {'details_fetched': 1}).sort('_id', 1)
            if (stamp := public_check_timestamp(row.get('details_fetched'))) and cutoff < stamp <= checked]


def stage_candidates(source, query, priority, start):
    for index, listing_id in enumerate(priority[start:], start=start + 1):
        yield listing_id, {'priority_index': index}

    prioritized = set(priority)
    while batch := list(source.find(query, {'_id': 1}).sort('_id', 1).limit(50)):
        for candidate in batch:
            listing_id = candidate['_id']
            if listing_id not in prioritized:
                yield listing_id, {'last_id': listing_id}
        query['_id'] = {'$gt': batch[-1]['_id']}


def gallery_progress(source, results, model, statuses, checkpoint):
    query = {'RAW_STATUS': {'$in': list(statuses)}} if statuses else {}
    if 'current_id' in checkpoint:
        query['_id'] = checkpoint['current_id']
    elif not statuses or 'priority_index' in checkpoint:
        return ''
    elif 'last_id' in checkpoint:
        query['_id'] = {'$gt': checkpoint['last_id']}
    listing = source.find_one(query, PROJECTION, sort=[('_id', 1)], max_time_ms=8000)
    if listing is None:
        return ''
    urls, _ = photo_references(listing)
    photo_ids = classification_urls(urls)
    labels = {row['photo_id']: row for row in listing.get('gallery_photo_labels') or ()
              if isinstance(row, dict) and row.get('category') and row.get('photo_id') in photo_ids}
    keys = [photo_key(listing['_id'], url, model) for url in urls]
    # Show the old worker's current partial gallery before the cutover too.
    records = list(results.find({'_id': {'$in': keys}}, {'result': 1, 'classified_at': 1})) if not labels else []
    classified = len(labels) + sum(record.get('result') == 'classified' for record in records)
    unavailable = sum(record.get('result') == 'unavailable' for record in records)
    timestamps = (public_check_timestamp(record.get('classified_at'))
                  for record in (*labels.values(), *records))
    latest = max((stamp for stamp in timestamps if stamp is not None), default=None)
    label = 'current gallery' if 'current_id' in checkpoint else 'next gallery'
    total = len(urls) if records else len(photo_ids)
    line = (f' · {label} {classified:,}/{total:,} photos classified'
            f' · {unavailable:,} unavailable')
    if latest:
        line += f' · last photo saved {latest:%H:%M:%S} UTC'
    return line


def show_progress(jobs, model, totals, *, live_gallery=None, current_stage=None, gallery=None):
    job_ids = {stage: f'{METHOD}:{model}:{stage}' for stage, _ in REPORT_STAGES}
    checkpoints = {job['_id']: job for job in jobs.find(
        {'_id': {'$in': list(job_ids.values())}},
        {'state': 1, 'counts': 1, 'last_id': 1, 'current_id': 1, 'priority_index': 1, 'priority_total': 1,
         'updated_at': 1},
    )}
    lines = [now().strftime('%Y-%m-%d %H:%M:%S UTC')]
    for stage, statuses in REPORT_STAGES:
        checkpoint = checkpoints.get(job_ids[stage], {})
        state = checkpoint.get('state', 'pending')
        counts = checkpoint.get('counts', {})
        scanned = counts.get('listings', 0)
        total = max(totals[stage], scanned)
        percent = scanned * 100 / total if total else (100 if state == 'complete' else 0)
        line = (f'{stage.title()} [{state}] {scanned:,}/{total:,} listings scanned · '
                f'{total - scanned:,} remaining · {percent:.2f}% · '
                f'{counts.get("classified", 0):,} photos classified in scanned listings · '
                f'{counts.get("cached", 0):,} reused · {counts.get("unavailable", 0):,} unavailable')
        if checkpoint.get('priority_total'):
            priority_label = 'FlipDB priority' if stage == FLIP_STAGE else 'fresh display checks'
            line += (f' · {priority_label} {checkpoint["priority_index"]:,}'
                     f'/{checkpoint["priority_total"]:,} processed')
        if stage == current_stage and gallery:
            line += f' · current gallery {gallery[0]:,}/{gallery[1]:,} photos checked'
        if live_gallery and state == 'running':
            line += gallery_progress(*live_gallery, model, statuses, checkpoint)
            updated_at = public_check_timestamp(checkpoint.get('updated_at'))
            if updated_at and (age := (now() - updated_at).total_seconds()) >= 300:
                line += f' · checkpoint inactive for {int(age // 60):,} min'
        lines.append(line)
    print('\n'.join(lines), flush=True)


def download_with_retry(http, url):
    for delay in (*RETRY_DELAYS, None):
        try:
            return read_image(http, url)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if delay is None or (status not in (408, 429) and status < 500):
                raise
        except (requests.ConnectionError, requests.Timeout):
            if delay is None:
                raise
        sleep(delay)


def caption_with_retry(engine, image):
    for delay in (*RETRY_DELAYS, None):
        try:
            return caption_for(engine, image)
        except (InferenceError, ValueError) as exc:
            if delay is None:
                raise
            print(f'Ollama photo retry in {delay}s ({type(exc).__name__})', flush=True)
            sleep(delay)


def classify_photo(http, engine, photo_id, urls, model):
    for url in urls:
        try:
            image = download_with_retry(http, url)
        except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError):
            continue
        break
    else:
        return None
    try:
        caption = caption_with_retry(engine, image)
    except ValueError:
        return None
    return {'photo_id': photo_id, 'category': infer_label(caption), 'caption': caption,
            'model': model, 'method': METHOD, 'source': LABEL_SOURCE, 'classified_at': now()}


def parallel_classifier(stack, make_engine, workers):
    clients = SimpleQueue()
    thread = local()
    for _ in range(workers):
        engine = make_engine()
        stack.callback(engine.session.close)
        clients.put((stack.enter_context(requests.Session()), engine))

    def initialize():
        thread.http, thread.engine = clients.get_nowait()

    def classify(photo_id, urls, model):
        return classify_photo(thread.http, thread.engine, photo_id, urls, model)

    pool = ThreadPoolExecutor(max_workers=workers, initializer=initialize)
    stack.callback(pool.shutdown, cancel_futures=True)
    return classify, pool


def run_stage(source, jobs, classify, pool, model, stage, statuses, totals, public_check_days, *, flip_ids=()):
    job_id = f'{METHOD}:{model}:{stage}'
    previous = jobs.find_one({'_id': job_id}) or {}
    resuming = previous.get('state') == 'running'
    query = {'RAW_STATUS': {'$in': list(statuses)}} if statuses else {}
    if statuses and resuming and previous.get('last_id'):
        query['_id'] = {'$gt': previous['last_id']}
    update = {'$set': {'state': 'running', 'updated_at': now()}}
    if not resuming:
        update['$unset'] = {'last_id': '', 'counts': ''}
    priority = []
    priority_index = 0
    if stage in (FLIP_STAGE, 'active'):
        if resuming and 'priority_queue' in previous:
            priority = previous['priority_queue']
            priority_index = previous['priority_index']
        elif stage == FLIP_STAGE:
            priority = list(flip_ids)
        else:
            priority = fresh_display_checks(source, query, public_check_days)
        update['$set'].update(priority_queue=priority, priority_index=priority_index, priority_total=len(priority))
    jobs.update_one({'_id': job_id}, update, upsert=True)
    show_progress(jobs, model, totals)
    next_report = perf_counter() + PROGRESS_SECONDS
    candidates = (stage_candidates(source, query, priority, priority_index) if statuses else
                  ((listing_id, {'priority_index': index})
                   for index, listing_id in enumerate(priority[priority_index:], start=priority_index + 1)))
    for listing_id, position in candidates:
        listing_query = {'_id': listing_id}
        if statuses:
            listing_query['RAW_STATUS'] = {'$in': list(statuses)}
        listing = source.find_one(listing_query, PROJECTION)
        if listing is None:
            jobs.update_one({'_id': job_id}, {'$set': position, '$unset': {'current_id': ''}})
            continue
        jobs.update_one({'_id': job_id}, {'$set': {'current_id': listing_id, 'updated_at': now()}})
        urls, skipped = photo_references(listing)
        photos = classification_urls(urls)
        complete = reusable_photo_ids(listing, photos, model)
        counts = dict(listings=1, classified=0, cached=len(complete), unavailable=0, rejected_refs=skipped)
        pending = [(photo_id, variants, model) for photo_id, variants in photos.items() if photo_id not in complete]
        if pool is None:
            outcomes = (classify(*photo) for photo in pending)
        else:
            futures = [pool.submit(classify, *photo) for photo in pending]
            outcomes = (future.result() for future in as_completed(futures))
        labels = []

        def flush_labels():
            if not labels:
                return
            batch = labels[:]
            labels.clear()
            if not store_gallery_labels(source, listing, batch):
                raise RuntimeError('Gallery changed while classifying; resume from the saved listing checkpoint')

        try:
            for checked, label in enumerate(outcomes, start=counts['cached'] + 1):
                if label is not None:
                    labels.append(label)
                    counts['classified'] += 1
                else:
                    counts['unavailable'] += 1
                if len(labels) >= SAVE_BATCH_SIZE:
                    flush_labels()
                if perf_counter() >= next_report:
                    jobs.update_one({'_id': job_id}, {'$set': {'updated_at': now()}})
                    show_progress(jobs, model, totals, current_stage=stage, gallery=(checked, len(photos)))
                    next_report = perf_counter() + PROGRESS_SECONDS
        finally:
            flush_labels()
        jobs.update_one({'_id': job_id}, {
            '$set': {**position, 'updated_at': now()},
            '$unset': {'current_id': ''},
            '$inc': {f'counts.{key}': value for key, value in counts.items()},
        })
        if perf_counter() >= next_report:
            show_progress(jobs, model, totals)
            next_report = perf_counter() + PROGRESS_SECONDS
    jobs.update_one({'_id': job_id}, {'$set': {'state': 'complete', 'updated_at': now()},
                                     '$unset': {'current_id': '', 'priority_queue': ''}})
    show_progress(jobs, model, totals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='moondream:latest')
    parser.add_argument('--ollama-url', default=OLLAMA_URL)
    parser.add_argument('--ollama-timeout', default=90, type=int)
    parser.add_argument('--workers', default=1, type=int, help='maximum concurrent photo requests (default: 1)')
    parser.add_argument('--public-check-days', default=PUBLIC_CHECK_DAYS, type=int,
                        help='prioritize active MLS details checked within this many days (default: 90)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='check model and count listings without writes')
    mode.add_argument('--status', action='store_true', help='show Mongo checkpoint state without running inference')
    parser.add_argument('--watch', action='store_true', help='refresh --status every 15 seconds')
    args = parser.parse_args()
    if args.ollama_timeout < 1:
        parser.error('--ollama-timeout must be positive')
    if args.workers < 1:
        parser.error('--workers must be positive')
    if not 1 <= args.public_check_days <= 36500:
        parser.error('--public-check-days must be between 1 and 36500')
    if args.watch and not args.status:
        parser.error('--watch requires --status')

    # Queries have at most 25 seconds of server work. Bound a lost connection
    # so the supervisor can resume instead of leaving an idle worker behind.
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000, socketTimeoutMS=45000) as mongo, ExitStack() as stack:
        source = mongo[MONGO_DB].mls_listings
        db = mongo[FLIPDB_DATABASE]
        results, jobs = db[RESULT_COLLECTION], db[JOB_COLLECTION]
        if args.status:
            flip_ids = flip_listing_ids(source, db.flipdb)
            totals = listing_totals(source, flip_ids)
            show_progress(jobs, args.model, totals, live_gallery=(source, results))
            if args.watch:
                try:
                    while True:
                        sleep(PROGRESS_SECONDS)
                        show_progress(jobs, args.model, totals, live_gallery=(source, results))
                except KeyboardInterrupt:
                    pass
            return
        make_engine = partial(Inference, local=True, model=args.model, ollama_url=args.ollama_url,
                              ollama_timeout=args.ollama_timeout)
        engine = make_engine()
        stack.callback(engine.session.close)
        engine.verify_vision()
        flip_ids = flip_listing_ids(source, db.flipdb)
        totals = listing_totals(source, flip_ids)
        if args.dry_run:
            print(f'flips: {totals[FLIP_STAGE]:,} distinct purchase and resale listings, processed first')
            for stage, _ in STAGES:
                print(f'{stage}: {totals[stage]:,} listings')
            active = {'RAW_STATUS': {'$in': list(STAGES[0][1])}}
            print(f'active: {len(fresh_display_checks(source, active, args.public_check_days)):,} fresh display checks '
                  f'within {args.public_check_days} days before excluding checkpointed listings')
            print(f'{args.model} verified. No inference or database writes.')
            return
        if args.workers == 1:
            classify = partial(classify_photo, stack.enter_context(requests.Session()), engine)
            pool = None
        else:
            classify, pool = parallel_classifier(stack, make_engine, args.workers)
        print(f'{args.model}: up to {args.workers} photo requests in flight · '
              f'{len(flip_ids):,} FlipDB listings first · saving labels in {source.full_name}', flush=True)
        completed_active = jobs.find_one({'_id': f'{METHOD}:{args.model}:active', 'state': 'complete'}, {'_id': 1})
        running_sold = jobs.find_one({'_id': f'{METHOD}:{args.model}:sold', 'state': 'running'}, {'_id': 1})
        stages = STAGES[1:] if completed_active and running_sold else STAGES
        run_stage(source, jobs, classify, pool, args.model, FLIP_STAGE, None, totals,
                  args.public_check_days, flip_ids=flip_ids)
        for stage, statuses in stages:
            run_stage(source, jobs, classify, pool, args.model, stage, statuses, totals, args.public_check_days)


if __name__ == '__main__':
    main()
