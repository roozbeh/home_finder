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
from buyer_analysis import ASSESSMENT_NUMBERS
from buyer_listing_facts import FACT_KEYS, public_facts, public_text
from buyer_media import approved_mls_gallery
from buyer_photo_labels import CATEGORIES, apply_bulk_categories, gallery_category
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
    'details.PUBLIC_REMARKS', 'details.Baths', 'details.MODIFICATION_TIMESTAMP', 'details.DAYS_ON_MARKET',
    'details.LANAMEBIZCARD.html', 'details.LIST_OFFICE_BCARD.html',
    'details.REPORT_LIST_AGENT_PHONE', 'details.LOPHONENO',
    'PROPERTY_SUB_TYPE', 'details.PROPERTY_SUB_TYPE',
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


def listing_update_date(value):
    # ConnectMLS sends this value without a time zone. Keep its source calendar date.
    if isinstance(value, str):
        try:
            return datetime.strptime(value, '%m/%d/%Y %I:%M %p').date().isoformat()
        except ValueError:
            pass
    stamp = read_stamp(value)
    return stamp.date().isoformat() if stamp else None


def listing_baths(doc, details):
    full = integer_amount(details.get('BATHROOMS_FULL'))
    half = integer_amount(details.get('BATHROOMS_HALF'))
    if full is not None and half is not None:
        return full + half / 2
    for value in (details.get('Baths'), details.get('BATHS_DISPLAY'), doc.get('BATHS_DISPLAY')):
        if match := re.fullmatch(r'(\d+)\s*/\s*(\d+)', str(value).strip()):
            whole, fractional = map(int, match.groups())
            return whole + fractional / 2
        if re.fullmatch(r'\d+(?:\.\d+)?', str(value).strip()):
            return float(value)
    full = full if full is not None else integer_amount(doc.get('BATHROOMS_FULL'))
    return full + (half or 0) / 2 if full is not None else None


def listing_contact(details):
    names = {}
    for party, key in (('agent', 'LANAMEBIZCARD'), ('office', 'LIST_OFFICE_BCARD')):
        card = details.get(key)
        if isinstance(card, dict):
            names[party] = card.get('html')
    return {**names, 'agent_phone': details.get('REPORT_LIST_AGENT_PHONE'), 'office_phone': details.get('LOPHONENO')}


def manifest_for(listings, rules, id_key, *, reviewed=None, photo_labels=None, bulk_classifications=None,
                 saved_assessments=None, now=None):
    now = now or datetime.now(timezone.utc)
    source_rules = {name: config for name, config in rules.items()
                    if config.get('production_approved') is True}
    manifest = {'mode': 'approved', 'sources': source_rules, 'homes': []}
    reviewed = reviewed or {}
    photo_labels = photo_labels or {}
    saved_assessments = saved_assessments or {}
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
               'details_checked': permission_checked.isoformat(timespec='minutes'),
               'listing_updated': listing_update_date(details.get('MODIFICATION_TIMESTAMP')),
               'id': hmac.new(id_key, stable_key.encode(), hashlib.sha256).hexdigest()[:32],
               'policy': {'basis': f'{source}: INTERNET_DISPLAY={display}',
                          'effective': max(refreshed, permission_checked).isoformat(), 'refresh_by': expires.isoformat(),
                          'allow': permissions},
               'credit': {'source': config.get('credit_name', source), 'mls_id': listing_id}}
        for key, field in MAPPED.items():
            if field in doc:
                row[key] = doc[field]
        if permissions.get('credits') and permissions.get('address'):
            row['credit'].update(listing_contact(details))
        days = integer_amount(row.get('days_on_market'))
        row['days_on_market'] = days if days is not None else integer_amount(details.get('DAYS_ON_MARKET'))
        row['property_type'] = listing_property_type(doc, details)
        for name in ('beds', 'sqft', 'lot_sqft', 'year_built'):
            row[name] = integer_amount(row.get(name, details.get(MAPPED[name])))
        row['baths'] = listing_baths(doc, details)
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
            photo_urls = approved_mls_gallery(details.get('PRIMARY_PHOTO_URI'), gallery)
            labels = photo_labels.get(listing_id, {})
            row['media'] = [
                {'display': True, 'mls_photo_url': url, 'category': gallery_category(labels, url, aliases),
                 **({'_photo_aliases': aliases} if aliases else {})}
                for url, aliases in photo_urls
            ]
        for key in ('media', 'reviewed_public_remarks', 'remarks_dependency_free', 'analysis_origin',
                    'cost_items', 'cost_version', 'cost_dependency_free', 'partial_budget', 'analysis_date'):
            if key in evidence:
                row[key] = evidence[key]
        approval_time = read_stamp(evidence.get('purchase_assessment_created_at'))
        assessment = saved_assessments.get(listing_id)
        if (market == 'sale' and approval_time is not None and approval_time <= now and assessment
                and read_stamp(assessment.get('created_at')) == approval_time
                and assessment.get('valuation_status') == 'AI_estimate_requires_review'
                and evidence.get('purchase_assessment_refresh_by')
                and all(permissions.get(field) for field in ('derived', 'valuation', 'sold_comps', 'ai', 'address'))):
            row.setdefault('analysis_origin', 'model')
            row['reviewed_purchase_assessment'] = {key: assessment.get(key) for key in ASSESSMENT_NUMBERS}
            row['purchase_assessment_refresh_by'] = evidence['purchase_assessment_refresh_by']
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
            reviewed = config.get('reviewed_listings') or {}
            approved_ids = {stable_key.split(':', 1)[1] for stable_key, evidence in reviewed.items()
                            if ':' in stable_key and evidence.get('purchase_assessment_created_at')}
            assessments = {}
            if approved_ids:
                assessments = {assessment['listing_id']: assessment for assessment in
                               photo_db.active_renovation_insights.find(
                                   {'listing_id': {'$in': sorted(approved_ids)}},
                                   {field: 1 for field in ('listing_id', 'created_at', 'valuation_status', *ASSESSMENT_NUMBERS)})}
            manifest = manifest_for(records, config['sources'], key, reviewed=reviewed, photo_labels=labels,
                                    bulk_classifications=photo_db.mls_photo_classifications, saved_assessments=assessments)
    else:
        manifest = manifest_for((), {}, key)
    if not args.dry_run:
        publish(manifest, args.catalog, args.assets)
    print(f'{len(cleared)} activated sources; {len(manifest["homes"])} MLS publication candidates')


if __name__ == '__main__':
    main()
