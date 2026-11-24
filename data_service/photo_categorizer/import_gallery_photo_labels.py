"""Import legacy classification labels matched to current Atlas MLS galleries."""

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import islice

from pymongo import MongoClient, UpdateOne

from buyer_photo_labels import CATEGORIES
from gallery_photo_labels import (
    FIELD, PROJECTION, gallery_filter, gallery_ids, label_priority, label_timestamp, labels_filter,
    merge_gallery_labels, photo_id_for, store_gallery_labels,
)


BULK = 'mls_photo_classifications'
TARGETED = 'active_photo_classifications'
HISTORICAL = 'photo_classifications'
ROW_FIELDS = {
    'listing_object_id': 1, 'listing_id': 1, 'source_mls': 1, 'photo_url': 1, 'category': 1,
    'caption': 1, 'visible_features': 1, 'model': 1, 'method': 1, 'classified_at': 1,
    'result': 1, 'pair_key': 1, 'side': 1,
}


def batches(items, size):
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


def snapshot_rows(legacy, cutoff, since):
    upper_bound = {'classified_at': {'$lte': cutoff}}
    bulk_time = {**upper_bound['classified_at'], **({'$gte': since} if since else {})}
    rows = {}
    for name in (BULK, TARGETED, HISTORICAL):
        time_filter = {'classified_at': bulk_time} if name == BULK else upper_bound
        query = time_filter if name == BULK and since else {
            '$or': [time_filter, {'classified_at': None}],
        }
        rows[name] = list(legacy[name].find(query, ROW_FIELDS).batch_size(2000))
    return rows


def load_listings(listings, rows):
    object_ids = {row['listing_object_id'] for row in rows[BULK] if row.get('listing_object_id') is not None}
    listing_ids = {
        row['listing_id'] for name in (BULK, TARGETED, HISTORICAL) for row in rows[name]
        if isinstance(row.get('listing_id'), str) and (name != BULK or not row.get('listing_object_id'))
    }
    docs = {}
    by_listing_id = defaultdict(list)
    for field, values in (('_id', object_ids), ('LISTING_ID', listing_ids)):
        for batch in batches(values, 250):
            for listing in listings.find({field: {'$in': batch}}, PROJECTION):
                listing_id = listing['_id']
                if listing_id not in docs:
                    docs[listing_id] = listing
                    by_listing_id[listing.get('LISTING_ID')].append(listing_id)
    return docs, by_listing_id


def resolve_listing(row, photo_id, docs, by_listing_id, galleries):
    if (object_id := row.get('listing_object_id')) is not None:
        listing = docs.get(object_id)
        if listing is None:
            return None, 'listing_missing'
        if (row.get('listing_id') != listing.get('LISTING_ID')
                or row.get('source_mls') != listing.get('SOURCE_MLS')):
            return None, 'identity_conflict'
        return (object_id, None) if photo_id in galleries[object_id] else (None, 'photo_missing')

    matching_ids = [
        listing_id for listing_id in by_listing_id.get(row.get('listing_id'), ())
        if not row.get('source_mls') or docs[listing_id].get('SOURCE_MLS') == row['source_mls']
    ]
    if not matching_ids:
        return None, 'listing_missing'
    matches = [listing_id for listing_id in matching_ids if photo_id in galleries[listing_id]]
    if len(matches) != 1:
        return None, 'ambiguous_listing' if matches else 'photo_missing'
    return matches[0], None


def classification_label(row, photo_id, source):
    label = {'photo_id': photo_id, 'category': row['category'],
             'classified_at': row['classified_at'], 'source': source}
    for field in ('model', 'method'):
        if isinstance(row.get(field), str) and row[field]:
            label[field] = row[field]
    for field in ('caption', 'visible_features'):
        if isinstance(row.get(field), str) and (caption := row[field].strip()):
            label['caption'] = caption
            break
    return label


def import_priority(label):
    # Keep legacy floor-plan suppression across photo-size aliases at the same source priority.
    return label_priority(label), label['category'] == 'floor_plan', label_timestamp(label)


def select_labels(legacy, rows, docs, by_listing_id):
    galleries = {listing_id: set(gallery_ids(listing)) for listing_id, listing in docs.items()}
    pairs = {}
    pair_keys = {row['pair_key'] for row in rows[HISTORICAL] if isinstance(row.get('pair_key'), str)}
    for chunk in batches(pair_keys, 250):
        for pair in legacy.flipdb.find({'pair_key': {'$in': chunk}},
                                      {'pair_key': 1, 'buy_mls': 1, 'sell_mls': 1}):
            pairs[pair['pair_key']] = pair
    candidates = defaultdict(dict)
    historical = defaultdict(list)
    source_counts = {}
    conflicts = set()
    for name, source_rows in rows.items():
        counts = source_counts[name] = Counter(rows=len(source_rows))
        for row in source_rows:
            if name == BULK and row.get('result') != 'classified':
                counts['unavailable' if row.get('result') == 'unavailable' else 'invalid_record'] += 1
                continue
            photo_id = photo_id_for(row.get('photo_url'))
            category = row.get('category')
            if (not photo_id or not isinstance(category, str) or category not in CATEGORIES
                    or not isinstance(row.get('classified_at'), datetime)):
                counts['invalid_record'] += 1
                continue
            if name == HISTORICAL:
                pair = pairs.get(row.get('pair_key'))
                side = row.get('side')
                if side not in ('buy', 'sell') or not pair or row.get('listing_id') != pair.get(f'{side}_mls'):
                    counts['pair_identity_conflict'] += 1
                    continue
            listing_id, problem = resolve_listing(row, photo_id, docs, by_listing_id, galleries)
            if problem:
                counts[problem] += 1
                continue
            label = classification_label(row, photo_id, f'{legacy.name}.{name}')
            counts['matched'] += 1
            if name == HISTORICAL:
                historical[listing_id, photo_id].append(label)
                continue
            current = candidates[listing_id].get(photo_id)
            if current and current['category'] != label['category']:
                conflicts.add((listing_id, photo_id))
            if not current or import_priority(label) > import_priority(current):
                candidates[listing_id][photo_id] = label

    historical_counts = source_counts[HISTORICAL]
    for (listing_id, photo_id), labels in historical.items():
        if len({label['category'] for label in labels}) > 1:
            historical_counts['conflicting_pair_rows'] += len(labels)
            continue
        current = candidates.get(listing_id, {}).get(photo_id)
        if current and current['category'] != labels[0]['category']:
            historical_counts['preserved_conflicting_pair_rows'] += len(labels)
            continue
        if not current:
            candidates[listing_id][photo_id] = max(labels, key=import_priority)

    totals = {
        'sources': source_counts,
        'candidate_photo_labels': sum(len(labels) for labels in candidates.values()),
        'candidate_listings': len(candidates),
        'photos_with_conflicting_nonhistorical_labels': len(conflicts),
    }
    return candidates, totals


def plan_updates(docs, candidates):
    stats = Counter()
    updates = []
    for listing_id, by_photo in candidates.items():
        listing = docs[listing_id]
        labels = list(by_photo.values())
        replacement = merge_gallery_labels(listing, labels)
        existing = listing.get(FIELD)
        existing = existing if isinstance(existing, list) else []
        old_by_photo = {row.get('photo_id'): row for row in existing if isinstance(row, dict)}
        stats['final_labels_in_candidate_listings'] += len(replacement)
        stats['new_labels'] += sum(label['photo_id'] not in old_by_photo for label in replacement)
        stats['updated_labels'] += sum(
            label['photo_id'] in old_by_photo and label != old_by_photo[label['photo_id']]
            for label in replacement
        )
        if replacement != listing.get(FIELD, []):
            updates.append((listing, labels, replacement))
    stats['listings_to_update'] = len(updates)
    return updates, stats


def apply_updates(listings, updates):
    applied = 0
    races = 0
    for batch in batches(updates, 50):
        result = listings.bulk_write([
            UpdateOne(labels_filter(listing, gallery_filter(listing)), {'$set': {FIELD: replacement}})
            for listing, _, replacement in batch
        ], ordered=False)
        if result.matched_count != len(batch):
            for listing, labels, _ in batch:
                if store_gallery_labels(listings, listing, labels):
                    applied += 1
                else:
                    races += 1
        else:
            applied += result.matched_count
    return {'listings_applied_or_already_current': applied, 'listings_with_concurrent_changes': races}


def parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError('Use an ISO-8601 UTC timestamp.') from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError('The timestamp must include a timezone.')
    return parsed.astimezone(timezone.utc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='write the guarded updates to Atlas')
    parser.add_argument('--legacy-since', type=parse_datetime, help='include bulk classifications since this time')
    parser.add_argument('--legacy-through', type=parse_datetime, help='pin the snapshot to an earlier dry run')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    upper_bound = args.legacy_through or now
    if upper_bound > now or (args.legacy_since and args.legacy_since > upper_bound):
        parser.error('The snapshot must end by now and after its starting time.')
    with MongoClient(os.environ['MONGO_URI']) as client:
        listings = client[os.environ.get('MONGO_DB', 'mls')].mls_listings
        legacy = client[os.environ.get('FLIPDB_DATABASE', 'flip_finder')]
        rows = snapshot_rows(legacy, upper_bound, args.legacy_since)
        docs, by_listing_id = load_listings(listings, rows)
        candidates, totals = select_labels(legacy, rows, docs, by_listing_id)
        updates, changes = plan_updates(docs, candidates)
        print(json.dumps({'apply': args.apply, 'snapshot_utc': upper_bound.isoformat(), **totals, **changes},
                         sort_keys=True), flush=True)
        if args.apply:
            print(json.dumps(apply_updates(listings, updates), sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
