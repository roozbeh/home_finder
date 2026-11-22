"""Publish the daily ConnectMLS store using explicitly cleared source rules."""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path

from pymongo import MongoClient

from mls_geography import BAY_COUNTIES
from buyer_listing_facts import FACT_KEYS, public_facts, public_text
from buyer_media import approved_mls_photo_url
from buyer_photo_labels import CATEGORIES, apply_bulk_categories
from buyer_property_types import listing_property_type
from buyer_publish import FIELDS, publish

ACTIVE = ('ACTV', 'NEW', 'BOMK', 'PCH')
MAPPED = {
    'city': 'CITY', 'price': 'LIST_PRICE', 'property_type': 'PROPERTY_TYPE',
    'beds': 'BEDROOMS_TOTAL', 'baths': 'BATHROOMS_FULL', 'sqft': 'SQFT',
    'address': 'STREET_ADDRESS', 'days_on_market': 'DAYS_ON_MARKET', 'original_price': 'ORIGINAL_PRICE',
    'locality': 'CITY_STATE_ZIP', 'year_built': 'YEAR_BUILT', 'lot_sqft': 'LOT_SQFT',
}
PROJECTION = {key: 1 for key in (
    'SOURCE_MLS', 'LISTING_ID', 'RAW_STATUS', '_updated_at', 'details_fetched', 'LATITUDE', 'LONGITUDE',
    *MAPPED.values(), *FACT_KEYS, 'CLOSE_DATE', 'CLOSE_PRICE', 'photos',
    'SALE_OR_RENT', 'details.SALE_OR_RENT', 'details.CLOSE_DATE', 'details.CLOSE_PRICE',
    'details.INTERNET_DISPLAY', 'details.INTERNET_SITES', 'details.PRIMARY_PHOTO_URI',
    'details.PUBLIC_REMARKS', 'details.Baths', 'PROPERTY_SUB_TYPE', 'details.PROPERTY_SUB_TYPE',
    'details.PROPERTY_TYPE', *(f'details.{key}' for key in FACT_KEYS),
)}


def integer_amount(value):
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return int(value)
    if isinstance(value, str):
        plain = value.replace('$', '').replace(',', '').strip()
        if re.fullmatch(r'[0-9]+(?:\.0+)?', plain):
            return int(float(plain))
    return None


def transaction_market(doc, details):
    raw_status = doc.get('RAW_STATUS')
    kind = str(details.get('SALE_OR_RENT') or doc.get('SALE_OR_RENT') or '').strip().lower()
    if raw_status == 'SLD' and kind in {'for sale', ''}:
        return 'sold'
    if raw_status in ACTIVE and kind in {'for rent', 'for lease', 'rent', 'lease'}:
        return 'rent'
    if raw_status in ACTIVE and kind in {'for sale', 'sale', ''}:
        return 'sale'
    return None


def read_stamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            try:
                value = datetime.strptime(value, '%B %d %Y %I:%M%p')
            except ValueError:
                try:
                    value = datetime.strptime(value, '%m/%d/%Y')
                except ValueError:
                    return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def manifest_for(listings, rules, id_key, *, reviewed=None, photo_labels=None, bulk_classifications=None, now=None):
    now = now or datetime.now(timezone.utc)
    source_rules = {name: config for name, config in rules.items()
                    if config.get('production_approved') is True}
    manifest = {'mode': 'approved', 'sources': source_rules, 'homes': []}
    reviewed = reviewed or {}
    photo_labels = photo_labels or {}
    uncategorized = []
    for doc in listings:
        source = doc.get('SOURCE_MLS')
        listing_id = doc.get('LISTING_ID')
        if source not in source_rules or not isinstance(listing_id, str):
            continue
        config = source_rules[source]
        details = doc.get('details') or {}
        market = transaction_market(doc, details)
        if market is None or (market == 'sold' and config.get('sold_search_days', 0) <= 0):
            continue
        display = details.get('INTERNET_DISPLAY')
        if (not isinstance(display, str)
                or details.get('INTERNET_SITES') not in config.get('internet_sites_values', ('Yes - All Sites',))):
            continue
        allowed = config.get('internet_display_values', {}).get(display)
        if not isinstance(allowed, list) or 'internet' not in allowed:
            continue
        refreshed = read_stamp(doc.get('_updated_at'))
        permission_checked = read_stamp(doc.get('details_fetched'))
        if refreshed is None or permission_checked is None:
            continue
        refresh_hours = config.get('listing_refresh_hours')
        if type(refresh_hours) not in (int, float) or not math.isfinite(refresh_hours) or refresh_hours <= 0:
            raise ValueError(f'Set a valid listing refresh interval for {source}')
        permission_hours = config.get('permission_refresh_hours', refresh_hours)
        if type(permission_hours) not in (int, float) or not math.isfinite(permission_hours) or permission_hours <= 0:
            raise ValueError(f'Set a valid permission refresh interval for {source}')
        expires = min(refreshed + timedelta(hours=permission_hours if market == 'sold' else refresh_hours),
                      permission_checked + timedelta(hours=permission_hours))
        close_date = None
        if market == 'sold':
            close_date = read_stamp(details.get('CLOSE_DATE'))
            if not close_date or not close_date <= now < close_date + timedelta(days=config['sold_search_days']):
                continue
            expires = min(expires, close_date + timedelta(days=config['sold_search_days']))
        if not max(refreshed, permission_checked) <= now < expires:
            continue

        stable_key = f'{source}:{listing_id}'
        evidence = reviewed.get(stable_key, {})
        permissions = {name: name in allowed for name in (*FIELDS, 'internet')}
        if config.get('publish_source_photos') is not True and evidence.get('allow', {}).get('media') is not True:
            permissions['media'] = False
        if market == 'sold' and config.get('publish_sold_photos') is not True:
            permissions['media'] = False
        for field in ('derived', 'valuation', 'sold_comps', 'historical_projects'):
            if evidence.get('allow', {}).get(field) is not True:
                permissions[field] = False

        row = {'source': source, 'market': market, 'status': 'Sold' if market == 'sold' else 'Active',
               'updated': refreshed.isoformat(),
               'id': hmac.new(id_key, stable_key.encode(), hashlib.sha256).hexdigest()[:32],
               'policy': {'basis': f'{source}: INTERNET_DISPLAY={display}',
                          'effective': max(refreshed, permission_checked).isoformat(), 'refresh_by': expires.isoformat(),
                          'allow': permissions},
               'credit': {'source': config.get('credit_name', source)}}
        for key, field in MAPPED.items():
            if field in doc:
                row[key] = doc[field]
        row['property_type'] = listing_property_type(doc, details)
        for name in ('beds', 'sqft', 'lot_sqft', 'year_built'):
            row[name] = integer_amount(row.get(name, details.get(MAPPED[name])))
        bath_display = details.get('Baths') or details.get('BATHS_DISPLAY') or ''
        matched_baths = re.fullmatch(r'(\d+)\s*/\s*(\d+)', str(bath_display))
        full = integer_amount(details.get('BATHROOMS_FULL', row.get('baths')))
        half = integer_amount(details.get('BATHROOMS_HALF'))
        if full is None and matched_baths:
            full, half = map(int, matched_baths.groups())
        if full is not None:
            row['baths'] = full + (half or 0) / 2
        if market == 'sold':
            row['price'] = integer_amount(details.get('CLOSE_PRICE') or doc.get('CLOSE_PRICE'))
            row['sale_date'] = close_date.date().isoformat()
        if permissions.get('facts'):
            row['facts'] = public_facts(doc, details)
        if (permissions.get('public_remarks') and config.get('publish_public_remarks') is True
                and (remarks := public_text(details.get('PUBLIC_REMARKS'), maxlength=6500))):
            row['reviewed_public_remarks'] = remarks
            row['remarks_dependency_free'] = True
        if all(type(doc.get(k)) in (int, float) and math.isfinite(doc[k])
               for k in ('LATITUDE', 'LONGITUDE')):
            row['coordinates'] = [doc['LATITUDE'], doc['LONGITUDE']]
        if config.get('publish_source_photos') is True and permissions['media'] and permissions['address']:
            gallery = doc.get('photos') or []
            photo_urls = [details.get('PRIMARY_PHOTO_URI'), *gallery]
            labels = photo_labels.get(listing_id, {})
            row['media'] = [
                {'display': True, 'mls_photo_url': url, 'category': labels.get(url)}
                for url in dict.fromkeys(url for url in photo_urls if approved_mls_photo_url(url))
            ]
        for key in ('media', 'reviewed_public_remarks', 'remarks_dependency_free', 'analysis_origin',
                    'cost_items', 'cost_version', 'cost_dependency_free', 'partial_budget', 'analysis_date'):
            if key in evidence:
                row[key] = evidence[key]
        if (bulk_classifications is not None and '_id' in doc and 'media' not in evidence
                and row.get('media')):
            uncategorized.extend((doc['_id'], asset) for asset in row['media'] if not asset['category'])
        manifest['homes'].append(row)
    if uncategorized:
        apply_bulk_categories(bulk_classifications, uncategorized)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--interval', type=int, default=0, help='Repeat publication at this interval in seconds')
    args = parser.parse_args()
    if args.interval < 0 or (args.interval and args.dry_run):
        parser.error('A recurring run needs a positive interval with publication enabled')
    while True:
        refresh(args)
        if not args.interval:
            break
        time.sleep(args.interval)


def refresh(args):
    config = json.loads(args.policy.read_text())
    cleared = [key for key, value in config['sources'].items() if value.get('production_approved') is True]
    key = os.environ.get('BUYER_PUBLIC_ID_KEY', '').encode()
    if cleared and not key:
        path = args.policy.with_name('public-id-key')
        if path.is_file():
            key = path.read_bytes().strip()
    if cleared and len(key) < 32:
        raise RuntimeError('Set a stable BUYER_PUBLIC_ID_KEY of at least 32 characters')
    if cleared:
        with MongoClient(os.environ['MONGO_URI'], serverSelectionTimeoutMS=8000) as client:
            inventory = client[os.environ.get('MONGO_DB', 'mls')].mls_listings
            query = {
                'SOURCE_MLS': {'$in': cleared},
                'COUNTY_OR_PARISH': {'$in': BAY_COUNTIES + [county.upper() for county in BAY_COUNTIES]},
            }
            active = inventory.find({**query, 'RAW_STATUS': {'$in': ACTIVE}, 'LIST_PRICE': {'$gt': 0}}, PROJECTION)
            sold_sources = [name for name in cleared if config['sources'][name].get('sold_search_days', 0) > 0]
            sold_projection = {key: value for key, value in PROJECTION.items() if key != 'photos'}
            sold = inventory.find({**query, 'RAW_STATUS': 'SLD', 'SOURCE_MLS': {'$in': sold_sources}}, sold_projection)
            records = chain(active, sold)
            labels = {}
            reviewed_labels = {}
            photo_db = client[os.environ.get('FLIPDB_DATABASE', 'flip_finder')]
            classifications = photo_db.active_photo_classifications
            for record in classifications.find(
                {'category': {'$in': list(CATEGORIES)}},
                {'_id': 0, 'listing_id': 1, 'photo_url': 1, 'category': 1, 'method': 1},
            ).sort('classified_at', 1):
                destination = reviewed_labels if record.get('method') == 'manual_visual_review' else labels
                destination.setdefault(record['listing_id'], {})[record['photo_url']] = record['category']
            for listing_id, gallery in reviewed_labels.items():
                labels.setdefault(listing_id, {}).update(gallery)
            manifest = manifest_for(records, config['sources'], key, reviewed=config.get('reviewed_listings'),
                                    photo_labels=labels, bulk_classifications=photo_db.mls_photo_classifications)
    else:
        manifest = manifest_for((), {}, key)
    if not args.dry_run:
        publish(manifest, args.catalog, args.assets)
    print(f'{len(cleared)} activated sources; {len(manifest["homes"])} MLS publication candidates')


if __name__ == '__main__':
    main()
