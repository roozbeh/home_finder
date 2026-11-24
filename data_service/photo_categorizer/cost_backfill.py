"""Draft Bay Area renovation scopes with local Gemma and price them from saved rates."""
import argparse
import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, timedelta
from itertools import islice
from pathlib import Path
from time import perf_counter, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import requests
from PIL import Image
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from bulk_moondream import download_with_retry, now, photo_key
from buyer_live_feed import ACTIVE, BAY_COUNTY_QUERY, MAPPED, PROJECTION, integer_amount, listing_baths, transaction_market
from buyer_photo_labels import BULK_MODEL, CATEGORIES
from buyer_property_types import listing_property_type, property_type_group
from categorize_photos import FLIPDB_DATABASE, MONGO_DB, MONGO_URI, OLLAMA_URL
from gallery_photo_labels import photo_id_for
from photo_inference import Inference, InferenceError, parse_response
from renovation_costs import calculate_budget
from renovation_drafts import COLLECTION, METHOD, current_gallery, fingerprint


GALLERY_PROJECTION = {key: 1 for key in (
    'SOURCE_MLS', 'LISTING_ID', 'RAW_STATUS', 'COUNTY_OR_PARISH', 'STREET_ADDRESS',
    'SALE_OR_RENT', 'details.SALE_OR_RENT', 'photos', 'details.PRIMARY_PHOTO_URI',
    'gallery_photo_labels.photo_id', 'gallery_photo_labels.category', 'gallery_photo_labels.caption',
    'CITY', 'BUILDING_TYPE', 'PROPERTY_TYPE', 'PROPERTY_SUBTYPE', 'PROPERTY_SUB_TYPE',
    'details.BUILDING_TYPE', 'details.PROPERTY_TYPE', 'details.PROPERTY_TYPE_DISPLAY',
    'details.PROPERTY_SUBTYPE', 'details.PROPERTY_SUB_TYPE',
    'details.BATHROOMS_FULL', 'details.BATHROOMS_HALF', 'details.Baths',
    'BATHS_DISPLAY', 'details.BATHS_DISPLAY',
    *(MAPPED[key] for key in ('sqft', 'beds', 'baths', 'year_built')),
    *(f'details.{MAPPED[key]}' for key in ('sqft', 'beds', 'year_built')),
)}
DEFAULT_RATES = Path(__file__).with_name('bay_area_renovation_rates.json')
DONE = ('complete', 'partial')
PROMPT_VERSION = 3
MAX_IMAGES_PER_CATEGORY = 4
MAX_TASKS = 12
EXCLUDED_PHOTOS = {'floor_plan', 'view', 'amenities', 'neighborhood'}
# Area and component constrain overlap even if the model renames a room.
TASKS = {
    'replace_vanity': ('Replace vanity in place', 'vanity', {'bathroom'}),
    'replace_faucet': ('Replace faucet in place', 'faucet', {'bathroom', 'kitchen', 'laundry'}),
    'replace_toilet': ('Replace toilet in place', 'toilet', {'bathroom'}),
    'replace_shower_fixture': ('Replace shower fixture in place', 'shower', {'bathroom'}),
    'replace_showerhead': ('Replace showerhead in place', 'shower', {'bathroom'}),
    'replace_lvp_flooring': ('Install vinyl plank flooring', 'flooring', None),
    'refinish_hardwood': ('Refinish hardwood flooring', 'flooring', None),
    'replace_tile_flooring': ('Replace tile flooring', 'flooring', None),
    'paint': ('Repaint interior walls', 'paint', None),
    'cabinet_work': ('Repair or refresh cabinetry', 'cabinetry', {'bathroom', 'kitchen', 'laundry'}),
    'countertop_work': ('Repair or replace countertop', 'countertop', {'bathroom', 'kitchen'}),
    'replace_quartz_countertop': ('Install quartz countertop', 'countertop', {'kitchen'}),
    'lighting_work': ('Update visible light fixtures', 'lighting', None),
    'appliance_work': ('Replace dated appliances', 'appliance', {'kitchen', 'laundry'}),
    'wall_tile_work': ('Repair or refresh wall tile', 'wall_tile', {'bathroom', 'kitchen'}),
    'tub_work': ('Refinish or replace bathtub', 'tub', {'bathroom'}),
    'other_work': ('Other visible work', 'other', None),
}
COUNTABLE = {'replace_vanity', 'replace_faucet', 'replace_toilet', 'replace_shower_fixture', 'replace_showerhead'}
SURFACES = {'replace_lvp_flooring', 'refinish_hardwood', 'replace_tile_flooring', 'paint', 'replace_quartz_countertop'}


def batches(iterable, size=150):
    iterator = iter(iterable)
    while batch := list(islice(iterator, size)):
        yield batch


def listing_identity(doc):
    source, listing = doc.get('SOURCE_MLS'), doc.get('LISTING_ID')
    if isinstance(source, str) and source and isinstance(listing, str) and listing:
        return source, listing
    return None


def listing_blocker(doc):
    if not listing_identity(doc):
        return 'missing_identity'
    details = doc.get('details') or {}
    if transaction_market(doc, details) != 'sale':
        return 'not_active_sale'
    label = listing_property_type(doc, details)
    if label is None:
        return 'unclassified_property_type'
    if property_type_group(label) == 'lots-land':
        return 'land'
    return None


def listing_facts(doc):
    details = doc.get('details') or {}
    facts = {key: integer_amount(doc.get(MAPPED[key], details.get(MAPPED[key])))
             for key in ('beds', 'sqft', 'year_built')}
    return {**facts, 'baths': listing_baths(doc, details),
            'property_type': listing_property_type(doc, details)}


def saved_evidence(db, docs):
    galleries = {doc['_id']: current_gallery(doc) for doc in docs}
    keys = {photo_key(doc['_id'], url, BULK_MODEL)
            for doc in docs for main, aliases in galleries[doc['_id']] for url in (main, *aliases)}
    fields = {'category': 1, 'visible_features': 1}
    bulk = {row['_id']: row for batch in batches(keys, 1500) for row in
            db.mls_photo_classifications.find({'_id': {'$in': batch}, 'result': 'classified'}, fields)}
    older = defaultdict(dict)
    ids = list({doc['LISTING_ID'] for doc in docs})
    for row in db.active_photo_classifications.find(
        {'listing_id': {'$in': ids}},
        {**fields, 'source_mls': 1, 'listing_id': 1, 'photo_url': 1, 'method': 1},
    ).sort('classified_at', 1):
        key = (row.get('source_mls'), row['listing_id'])
        url = row.get('photo_url')
        previous = older[key].get(url, {})
        if previous.get('method') != 'manual_visual_review' or row.get('method') == 'manual_visual_review':
            older[key][url] = row

    for doc in docs:
        gallery = galleries[doc['_id']]
        source, listing_id = listing_identity(doc)
        legacy = older[None, listing_id] | older[source, listing_id]
        embedded = {row['photo_id']: row for row in doc.get('gallery_photo_labels') or ()
                    if isinstance(row, dict) and isinstance(row.get('photo_id'), str)}
        photos = []
        for index, (url, aliases) in enumerate(gallery, 1):
            labels = [embedded.get(photo_id_for(url), {})]
            labels += [legacy.get(ref, {}) for ref in (url, *aliases)]
            labels += [bulk.get(photo_key(doc['_id'], ref, BULK_MODEL), {}) for ref in (url, *aliases)]
            category = next((row['category'] for row in labels if row.get('category') in CATEGORIES), None)
            if category is None or any(row.get('category') in EXCLUDED_PHOTOS for row in labels):
                continue
            caption = next((value.strip()[:240] for row in labels
                            if isinstance(value := row.get('caption') or row.get('visible_features'), str)
                            and value.strip()), None)
            if caption:
                photos.append({'id': index, 'url': url, 'category': category, 'caption': caption})
        yield doc, gallery, photos


def area_photos(photos):
    groups = defaultdict(list)
    for photo in photos:
        groups[photo['category']].append(photo)
    return dict(sorted(groups.items()))


def select_photos(photos):
    if len(photos) <= MAX_IMAGES_PER_CATEGORY:
        return photos
    # Spread the sample across the category so later rooms have a chance to be represented.
    return [photos[index * (len(photos) - 1) // (MAX_IMAGES_PER_CATEGORY - 1)]
            for index in range(MAX_IMAGES_PER_CATEGORY)]


def load_rates(path):
    book = json.loads(path.read_text())
    date.fromisoformat(book['as_of'])
    if book['currency'] != 'USD' or book['region'] != 'San Francisco Bay Area':
        raise ValueError('Expected Bay Area rates in USD')
    for code, rate in book['rates'].items():
        if (code not in TASKS or rate['unit'] not in {'each', 'sq ft'}
                or (code in COUNTABLE) != (rate['unit'] == 'each')
                or type(rate['low']) not in (int, float) or type(rate['high']) not in (int, float)
                or not math.isfinite(rate['high']) or not 0 < rate['low'] <= rate['high']
                or urlsplit(rate['source']).scheme != 'https' or not urlsplit(rate['source']).hostname):
            raise ValueError(f'Invalid rate for {code}')
    return book


def clean_text(value):
    return ' '.join(value.split())[:320] if isinstance(value, str) else ''


def validate_scope(result, area, photos, facts):
    by_id = {photo['id']: photo for photo in photos}
    raw_tasks = result.get('tasks')
    if not isinstance(raw_tasks, list) or len(raw_tasks) > MAX_TASKS:
        raise ValueError('Expected up to twelve tasks')
    tasks = {}
    for task in raw_tasks:
        if not isinstance(task, dict):
            raise ValueError('Expected task object')
        code = task.get('code')
        refs = task.get('photo_ids')
        evidence, work = clean_text(task.get('evidence')), clean_text(task.get('work'))
        if (not isinstance(code, str) or code not in TASKS or not isinstance(refs, list) or not refs
                or any(type(ref) is not int or ref not in by_id for ref in refs)
                or not evidence or not work):
            raise ValueError('Task needs a recognized code, photo references, visible evidence and work')
        _, component, allowed_areas = TASKS[code]
        if allowed_areas and area not in allowed_areas:
            raise ValueError('Task uses an unsupported room category')
        if code == 'replace_faucet' and area == 'bathroom':
            component = 'vanity'  # The vanity allowance includes its faucet.
        identity = (area, component)
        low, high = task.get('quantity_low'), task.get('quantity_high')
        basis = clean_text(task.get('quantity_basis'))
        kind = task.get('quantity_kind')
        counted = (code in COUNTABLE and kind == 'visible_count' and type(low) is int and type(high) is int
                   and 1 <= low == high <= 8)
        # Use total interior area only as a sanity ceiling for floor quantities.
        ceiling = (facts.get('sqft') or 2500) if code in {
            'replace_lvp_flooring', 'refinish_hardwood', 'replace_tile_flooring',
        } and area not in {'garage', 'exterior', 'outdoor'} else 2500
        scaled = (code in SURFACES and kind == 'photo_allowance' and type(low) is int and type(high) is int
                  and 0 < low < high <= ceiling and high >= 1.2 * low)
        if not basis or not (counted or scaled):
            low = high = None
            kind, basis = 'unknown', basis or 'Field verification needed'
        line = {'area': area, 'code': code, 'photo_ids': sorted(set(refs)), 'evidence': evidence,
                'work': work, 'quantity': low, 'quantity_high': high, 'quantity_kind': kind,
                'quantity_basis': basis}
        if identity in tasks:
            existing = tasks[identity]
            # Collapse duplicate perspectives. Conflicting replacements or quantities need field review.
            existing['photo_ids'] = sorted(set(existing['photo_ids'] + line['photo_ids']))
            if any(existing[key] != line[key] for key in ('code', 'quantity', 'quantity_high')):
                existing.update(quantity=None, quantity_high=None, quantity_kind='unknown',
                                quantity_basis='Overlapping alternatives need review')
        else:
            tasks[identity] = line
    unknown = result.get('unknown')
    if not isinstance(unknown, list):
        raise ValueError('Expected an unknown conditions list')
    condition = clean_text(result.get('condition'))
    if not condition:
        raise ValueError('Expected an area condition summary')
    return {'tasks': list(tasks.values()), 'condition': condition,
            'unknown': [note for item in unknown[:8] if (note := clean_text(item))]}


def price_scope(scope, book, *, evidence_incomplete):
    areas, observations = {}, []
    for area, result in scope.items():
        priced, unpriced = [], []
        for item in result['tasks']:
            rate = book['rates'].get(item['code'])
            line = {'area': CATEGORIES[area], 'task': TASKS[item['code']][0], 'detail': item['work'],
                    'photo_ids': item['photo_ids'], 'evidence': item['evidence'], 'quantity': item['quantity'],
                    'quantity_high': item['quantity_high'], 'quantity_kind': item['quantity_kind'],
                    'quantity_basis': item['quantity_basis']}
            if rate and line['quantity'] is not None:
                priced.append({**line, 'room': line['area'], 'unit': rate['unit'], 'source': rate['source'],
                               'rate_date': book['as_of'], 'unit_low': rate['low'], 'unit_high': rate['high']})
            else:
                unpriced.append({**line, 'unpriced': 'Quantity needs verification' if rate else 'No matching Bay Area rate'})
        budget = calculate_budget(priced, {})
        items = [{**item, 'low': float(item['low']), 'high': float(item['high'])} for item in budget['items']]
        observations.extend([*items, *unpriced])
        photo_allowance = any(item['quantity_kind'] == 'photo_allowance' for item in items)
        partial = bool(unpriced or result['unknown'] or photo_allowance or result['incomplete_coverage'])
        areas[area] = {
            'label': CATEGORIES[area], 'condition': result['condition'], 'partial': partial,
            'low': float(budget['low']) if items or not unpriced else None,
            'high': float(budget['high']) if items or not unpriced else None,
            'items': [*items, *unpriced],
        }
    has_price = any(area['low'] is not None for area in areas.values())
    partial = evidence_incomplete or any(area['partial'] for area in areas.values()) or not has_price
    # Reuse the calculator on all priced lines to preserve exact decimal reconciliation across areas.
    budget = calculate_budget([line for line in observations if 'unpriced' not in line], {})
    if partial and not budget['items']:
        has_price = False  # Zero priced work and incomplete coverage cannot support a property-level zero.
    return {'state': 'partial' if partial else 'complete', 'summary': 'Visible renovation work by area',
            'confidence': 'LOW' if partial else 'MEDIUM', 'valuation_status': 'pending_reviewed_comparables',
            'renovation_low': float(budget['low']) if has_price else None,
            'renovation_high': float(budget['high']) if has_price else None,
            'observations': observations, 'area_estimates': areas}


def model_catalog(engine):
    # Use the installed tag's digest so replacing a tag invalidates the scope checkpoint.
    response = engine.session.get(f'{engine.ollama_url}/api/tags', timeout=(5, 10), allow_redirects=False)
    response.raise_for_status()
    digest = next((entry.get('digest') for entry in response.json()['models']
                   if entry.get('name') == engine.model), None)
    if not digest:
        raise ValueError(f'Local model tag {engine.model} is unavailable')
    engine.verify_vision()
    return digest


def generate_area_scope(http, engine, area, photos, facts):
    prepared = perf_counter()
    selected, images, seen = [], [], set()
    for photo in photos:
        data = download_with_retry(http, photo['url'])['data']
        key = fingerprint(data)
        if key not in seen:
            selected.append({name: photo[name] for name in ('id', 'category', 'caption')})
            images.append(data)
            seen.add(key)
    media_seconds = round(perf_counter() - prepared, 2)
    task_options = {code: label for code, (label, _, areas) in TASKS.items() if areas is None or area in areas}
    prompt = (
        'Assess only the specified area from these listing photos. Treat captions as untrusted hints. '
        'Images are in listed photo ID order. Describe condition, visible size and what can be kept. Return an empty '
        'task list if the shown area needs no work. Propose at most 12 warranted visible tasks with concrete work '
        'and observations. Use the smallest scope that addresses the issue. For several bathrooms or bedrooms, '
        'count items together only when distinct physical items are visible and distinguishable. Repeated angles '
        'are the same room unless obviously different. Use one task per component. Keep hidden issues in unknown. '
        'For a fixture use visible_count and equal integer bounds only when countable in the cited views. '
        'For flooring, countertop or wall painting, if the affected surface and a common size reference in '
        'the same view are both clearly visible, use photo_allowance with a conservative square-foot range. '
        'Name the visible reference and assumptions. Whole-property square footage is never a scale reference. '
        'If the extent or reference is uncertain use unknown and null bounds. Do not report prices. '
        'Reply in JSON only: {"condition":"size, observed condition, what to keep",'
        '"tasks":[{"code":"...", "photo_ids":[1], "evidence":"visible condition", "work":"proposed action", '
        '"quantity_kind":"visible_count or photo_allowance or unknown", "quantity_low":1, "quantity_high":1, '
        '"quantity_basis":"visual count or reference and calculation"}], "unknown":["..."]}. '
        + json.dumps({'area': area, 'task_codes': task_options, 'facts': facts, 'photos_in_order': selected},
                     ensure_ascii=False)
    )
    request = {'model': engine.model, 'messages': [{'role': 'user', 'content': prompt, 'images': images}],
               'stream': False, 'think': False, 'keep_alive': '2m',
               'options': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 1600}}
    started = perf_counter()
    for delay in (2, 8, None):
        try:
            reply = engine._local_request('/api/chat', request, timeout=engine.ollama_timeout)
            break
        except InferenceError:
            if delay is None:
                raise
            sleep(delay)
    elapsed = round(perf_counter() - started, 2)
    if reply.get('done_reason') == 'length':
        raise ValueError('Truncated scope response')
    scope = validate_scope(parse_response(reply.get('message', {}).get('content'), 'Ollama'), area, selected, facts)
    metrics = {key: reply[key] for key in ('total_duration', 'load_duration', 'prompt_eval_count', 'prompt_eval_duration',
                                           'eval_count', 'eval_duration') if key in reply}
    scope['incomplete_coverage'] = len(selected) < len(photos)
    return {'scope': scope, 'evidence': selected, 'timing': {**metrics, 'inference_seconds': elapsed,
                                                          'media_seconds': media_seconds, 'images': len(images)}}


def claim(collection, identity, metadata, timeout):
    stamp = now()
    owner = uuid4().hex
    try:
        return collection.find_one_and_update(
            {'_id': identity, 'state': {'$nin': list(DONE)}, '$or': [
                {'retry_at': {'$exists': False}}, {'retry_at': {'$lte': stamp}}]},
            {'$setOnInsert': metadata, '$set': {'owner': owner, 'state': 'running', 'updated_at': stamp,
                                               'retry_at': stamp + timedelta(seconds=timeout)},
             '$inc': {'attempts': 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        # Another process already owns or completed this version.
        return None


def checkpoint(collection, job, values):
    collection.update_one({'_id': job['_id'], 'owner': job['owner']},
                          {'$set': {**values, 'updated_at': now()}, '$unset': {'owner': ''}})


def audit_inventory(inventory, policy_path, catalog_path):
    from buyer_catalog import Catalog
    from buyer_live_feed import manifest_for, publication_id

    config = json.loads(policy_path.read_text())
    id_key = os.environ.get('BUYER_PUBLIC_ID_KEY', '').encode()
    if not id_key:
        id_key = policy_path.with_name('public-id-key').read_bytes().strip()
    if len(id_key) < 32:
        raise ValueError('Buyer catalog ID key must match the publisher')
    stamp = now()
    with closing(sqlite3.connect(catalog_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        where, params = Catalog._matching('approved', {'market': 'sale'})
        params[0] = stamp.isoformat()
        public_ids = {row[0] for row in db.execute(f'SELECT id FROM homes {where}', params)}
    reasons, additional = Counter(), Counter()
    matched = set()

    def record(doc, reason):
        identity = listing_identity(doc)
        opaque_id = publication_id(id_key, *identity) if identity else None
        published = opaque_id in public_ids
        if published:
            matched.add(opaque_id)
        if doc.get('MLS_STATUS') == 'ACTV':
            reasons['in_buyer_catalog' if published else
                    ('eligible_missing_from_catalog' if reason == 'eligible' else reason)] += 1
        elif published:
            additional[doc.get('RAW_STATUS') or 'missing_raw_status'] += 1

    query = {'$or': [{'MLS_STATUS': 'ACTV'}, {'RAW_STATUS': {'$in': list(ACTIVE)}}]}
    for docs in batches(inventory.find(query, PROJECTION)):
        manifest_for(docs, config['sources'], id_key, now=stamp, audit=record)
    print(json.dumps({'inventory_audit': {'admin_active_records': sum(reasons.values()),
                                         'public_for_sale_ids': len(public_ids),
                                         'admin_records_by_reason': reasons,
                                         'buyer_records_outside_admin_by_raw_status': additional,
                                         'buyer_ids_without_matching_active_records': len(public_ids - matched)}}))


def run_pass(inventory, db, args, book, http=None, engine=None, digest=None):
    started = perf_counter()
    counts, blockers = Counter(), Counter()
    query = {'RAW_STATUS': {'$in': list(ACTIVE)}, 'COUNTY_OR_PARISH': BAY_COUNTY_QUERY}
    if args.listing_id:
        query['LISTING_ID'] = args.listing_id
    if args.source:
        query['SOURCE_MLS'] = args.source
    listings = inventory.find(query, GALLERY_PROJECTION)
    if args.listing_id:
        listings = list(listings.limit(2))
        if len(listings) != 1:
            raise ValueError('Listing must match one active Bay Area record. Specify --source if ambiguous.')
    rate_version = fingerprint(book)
    collection = db[COLLECTION]
    stop = False

    for docs in batches(listings):
        eligible = []
        for doc in docs:
            counts['scanned'] += 1
            if reason := listing_blocker(doc):
                blockers[reason] += 1
            else:
                eligible.append(doc)
        if not eligible:
            continue
        ids = list({doc['LISTING_ID'] for doc in eligible})
        legacy = defaultdict(set)
        for row in db.active_renovation_insights.find({'listing_id': {'$in': ids}},
                                                    {'listing_id': 1, 'source_mls': 1}):
            legacy[row['listing_id']].add(row.get('source_mls'))
        previous = defaultdict(list)
        for row in collection.find(
            {'listing_id': {'$in': ids}, 'model': args.model},
            {'source_mls': 1, 'listing_id': 1, 'input_version': 1, 'rate_version': 1, 'model_digest': 1,
             'state': 1, 'retry_at': 1},
        ):
            previous[row['source_mls'], row['listing_id']].append(row)
        for doc, gallery, photos in saved_evidence(db, eligible):
            source, listing_id = identity = listing_identity(doc)
            existing = bool(legacy[listing_id] & {source, None})
            counts['legacy_or_reviewed'] += existing
            if not photos:
                counts['waiting'] += 1
                blockers['waiting_for_classified_photos' if gallery else 'missing_usable_gallery'] += 1
                continue
            facts, groups = listing_facts(doc), area_photos(photos)
            selected = {area: select_photos(group) for area, group in groups.items()}
            gallery_version = fingerprint(gallery)
            input_version = fingerprint([PROMPT_VERSION, str(doc['_id']), gallery_version, len(photos), facts, selected])
            candidates = [row for row in previous[identity] if row['input_version'] == input_version
                          and (digest is None or row.get('model_digest') == digest)]
            current = next((row for row in candidates if row['rate_version'] == rate_version), None)
            if current and current['state'] in DONE:
                counts[current['state']] += 1
                continue
            if current and current.get('retry_at') and current['retry_at'] > now():
                counts[current['state']] += 1
                blockers['retry_backoff' if current['state'] == 'failed' else 'in_progress'] += 1
                continue
            reusable = next((row for row in candidates if row['state'] in DONE), None)
            counts['eligible'] += 1
            counts['reprice_ready'] += bool(reusable)
            if args.dry_run:
                continue

            version = fingerprint([source, listing_id, args.model, digest, input_version, rate_version])
            metadata = {'source_mls': source, 'listing_id': listing_id, 'listing_object_id': doc['_id'],
                        'model': args.model, 'model_digest': digest, 'method': METHOD,
                        'input_version': input_version, 'gallery_version': gallery_version,
                        'prompt_version': PROMPT_VERSION, 'rate_version': rate_version}
            # Renew the lease per area, covering its bounded download and inference retries.
            lease = MAX_IMAGES_PER_CATEGORY * 135 + 3 * (args.ollama_timeout + 5) + 60
            job = claim(collection, version, metadata, lease)
            if not job:
                blockers['in_progress_or_completed'] += 1
                continue
            counts['attempted'] += 1
            print(json.dumps({'attempt': counts['attempted'], 'source': source, 'listing_id': listing_id,
                              'address': doc.get('STREET_ADDRESS'), 'classified_photos': len(photos),
                              'gallery_photos': len(gallery), 'selected_photos_by_area': {area: len(group) for area, group in selected.items()},
                              'existing_assessment': existing, 'reprice': bool(reusable)}))
            item_start = perf_counter()
            try:
                if reusable:
                    saved = collection.find_one({'_id': reusable['_id']}, {'scope': 1, 'photo_evidence': 1})
                    scope, evidence, metrics = saved['scope'], saved['photo_evidence'], {'repriced': True}
                else:
                    results = job.get('area_checkpoints', {})
                    owner = {'_id': version, 'owner': job['owner']}
                    for area, group in selected.items():
                        if area in results:
                            print(json.dumps({'area': area, 'reused': True}))
                            continue
                        if not collection.update_one(owner, {'$set': {
                            'retry_at': now() + timedelta(seconds=lease),
                        }}).matched_count:
                            raise ValueError('Another worker took over the listing')
                        print(json.dumps({'area': area, 'photos': len(group), 'state': 'inference'}))
                        result = generate_area_scope(http, engine, area, group, facts)
                        result['scope']['incomplete_coverage'] |= len(group) < len(groups[area])
                        results[area] = result
                        collection.update_one(owner, {'$set': {f'area_checkpoints.{area}': result}})
                        print(json.dumps({'area': area, 'timing': result['timing'],
                                          'tasks': len(result['scope']['tasks'])}))
                    scope = {area: result['scope'] for area, result in results.items()}
                    evidence = {area: result['evidence'] for area, result in results.items()}
                    timings = {area: result['timing'] for area, result in results.items()}
                    metrics = {'areas': timings, **{field: round(sum(
                        timing[field] for timing in timings.values()), 2)
                        for field in ('inference_seconds', 'media_seconds')}}
                fresh = inventory.find_one({'_id': doc['_id']}, GALLERY_PROJECTION)
                if (not fresh or listing_blocker(fresh) or fresh.get('COUNTY_OR_PARISH') not in BAY_COUNTY_QUERY['$in']
                        or listing_identity(fresh) != identity or fingerprint(current_gallery(fresh)) != gallery_version
                        or listing_facts(fresh) != facts):
                    raise ValueError('Listing changed during assessment')
                estimate = price_scope(scope, book, evidence_incomplete=len(photos) != len(gallery))
                metrics['property_seconds'] = round(perf_counter() - item_start, 2)
                checkpoint(collection, job, {**estimate, 'scope': scope, 'photo_evidence': evidence,
                                             'property_facts': facts, 'timing': metrics, 'created_at': now()})
                counts[estimate['state']] += 1
                print(json.dumps({'state': estimate['state'], 'version': version, 'timing': metrics,
                                  'low': estimate['renovation_low'], 'high': estimate['renovation_high'],
                                  'area_ranges': {area: [value['low'], value['high']]
                                                  for area, value in estimate['area_estimates'].items()},
                                  'priced_items': sum('low' in line for line in estimate['observations']),
                                  'unpriced_items': sum('unpriced' in line for line in estimate['observations'])}))
            except (InferenceError, ValueError, requests.RequestException, OSError, Image.DecompressionBombError) as exc:
                delay = min(1800, 30 * 2 ** min(job['attempts'] - 1, 6))
                checkpoint(collection, job, {'state': 'failed', 'error': str(exc)[:280],
                                             'retry_at': now() + timedelta(seconds=delay)})
                counts['failed'] += 1
                print(json.dumps({'state': 'failed', 'error': str(exc)[:280], 'retry_seconds': delay,
                                  'property_seconds': round(perf_counter() - item_start, 2)}))
            if args.limit and counts['attempted'] >= args.limit:
                stop = True
                break
        if stop:
            break
    for key in ('waiting', 'attempted', 'complete', 'partial', 'failed', 'eligible', 'legacy_or_reviewed'):
        counts.setdefault(key, 0)
    print(json.dumps({'counts': counts, 'blockers': blockers, 'seconds': round(perf_counter() - started, 2)}))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int, help='Maximum listing attempts in this run')
    parser.add_argument('--listing-id')
    parser.add_argument('--source', help='MLS source code')
    parser.add_argument('--watch', type=int, metavar='SECONDS', help='Recheck the market at this interval')
    parser.add_argument('--model', default='gemma4:12b')
    parser.add_argument('--ollama-url', default=OLLAMA_URL)
    parser.add_argument('--ollama-timeout', type=int, default=180)
    parser.add_argument('--rate-book', type=Path, default=DEFAULT_RATES)
    parser.add_argument('--policy', type=Path, help='Buyer policy for a read-only inventory reconciliation')
    parser.add_argument('--catalog', type=Path, help='Current buyer catalog for a read-only inventory reconciliation')
    args = parser.parse_args()
    if (args.catalog is None) != (args.policy is None) or (args.catalog and not args.dry_run):
        parser.error('--policy and --catalog are used together with --dry-run')
    if args.watch is not None and (args.watch < 1 or args.limit or args.dry_run or args.listing_id):
        parser.error('--watch needs a positive interval and a market run')
    if (args.limit is not None and args.limit < 1) or args.ollama_timeout < 1:
        parser.error('Limit and timeout must be positive')
    if not any((args.dry_run, args.limit, args.watch, args.listing_id)):
        parser.error('Use --dry-run, --listing-id, --limit or --watch')

    book = load_rates(args.rate_book)
    # Match the neighboring photo backfill's server selection and short database read/write bounds.
    with MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000, socketTimeoutMS=45000,
                     tz_aware=True) as mongo, requests.Session() as http:
        inventory, db = mongo[MONGO_DB].mls_listings, mongo[FLIPDB_DATABASE]
        if args.dry_run:
            if args.catalog:
                audit_inventory(inventory, args.policy, args.catalog)
            run_pass(inventory, db, args, book)
            return
        local = urlsplit(args.ollama_url)
        if local.scheme != 'http' or local.hostname not in {'localhost', '127.0.0.1', '::1', 'host.docker.internal'}:
            parser.error('Ollama must be running on the local host')
        engine = Inference(local=True, model=args.model, ollama_url=args.ollama_url, ollama_timeout=args.ollama_timeout)
        with engine.session:
            digest = model_catalog(engine)
            while True:
                counts = run_pass(inventory, db, args, book, http, engine, digest)
                if not args.watch:
                    if counts['failed']:
                        raise SystemExit(1)
                    return
                sleep(args.watch)


if __name__ == '__main__':
    main()
