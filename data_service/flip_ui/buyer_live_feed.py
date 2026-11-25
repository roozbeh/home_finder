"""Project live ConnectMLS listing fields using explicitly cleared source rules."""
import hashlib
import hmac
import math
import re
from datetime import datetime, timedelta, timezone


from mls_geography import BAY_COUNTIES
from buyer_analysis import ASSESSMENT_NUMBERS
from buyer_listing_facts import FACT_KEYS, public_facts, public_text
from buyer_media import approved_mls_gallery, mls_photo_variant
from buyer_photo_labels import CATEGORIES
from buyer_property_types import listing_property_type
from buyer_publish import FIELDS, rights

ACTIVE = ('ACTV', 'NEW', 'BOMK', 'PCH')
BAY_COUNTY_QUERY = {'$in': BAY_COUNTIES + [county.upper() for county in BAY_COUNTIES]}
MAPPED = {
    'city': 'CITY', 'price': 'LIST_PRICE', 'property_type': 'PROPERTY_TYPE',
    'beds': 'BEDROOMS_TOTAL', 'baths': 'BATHROOMS_FULL', 'sqft': 'SQFT',
    'address': 'STREET_ADDRESS', 'days_on_market': 'DAYS_ON_MARKET', 'original_price': 'ORIGINAL_PRICE',
    'locality': 'CITY_STATE_ZIP', 'year_built': 'YEAR_BUILT', 'lot_sqft': 'LOT_SQFT',
}
PROJECTION = {key: 1 for key in (
    'SOURCE_MLS', 'LISTING_ID', 'RAW_STATUS', 'MLS_STATUS', 'COUNTY_OR_PARISH', '_updated_at', 'details_fetched', 'LATITUDE', 'LONGITUDE',
    *MAPPED.values(), *FACT_KEYS, 'CLOSE_DATE', 'CLOSE_PRICE', 'photos',
    'SALE_OR_RENT', 'details.SALE_OR_RENT', 'details.CLOSE_DATE', 'details.CLOSE_PRICE',
    'details.INTERNET_DISPLAY', 'details.INTERNET_SITES', 'details.PRIMARY_PHOTO_URI',
    'details.PUBLIC_REMARKS', 'details.Baths', 'details.MODIFICATION_TIMESTAMP', 'details.DAYS_ON_MARKET',
    'details.LANAMEBIZCARD.html', 'details.LIST_OFFICE_BCARD.html',
    'details.REPORT_LIST_AGENT_PHONE', 'details.LOPHONENO',
    'PROPERTY_SUB_TYPE', 'details.PROPERTY_SUB_TYPE',
    'details.PROPERTY_TYPE', *(f'details.{key}' for key in FACT_KEYS),
)}
PHOTO_LABEL_PROJECTION = {'gallery_photo_labels.photo_id': 1, 'gallery_photo_labels.category': 1}


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


def publication_id(id_key, source, listing_id):
    return hmac.new(id_key, f'{source}:{listing_id}'.encode(), hashlib.sha256).hexdigest()[:32]


ACTIVE_STATUS_LABELS = {'ACTV': 'Active', 'NEW': 'New', 'BOMK': 'Back on market', 'PCH': 'Price changed'}


def manifest_for(listings, rules, id_key, *, reviewed=None, saved_assessments=None, publish_operator_estimates=False, now=None, audit=None,
                 live_inventory=False, summary=False):
    now = now or datetime.now(timezone.utc)
    source_rules = {name: config for name, config in rules.items()
                    if config.get('production_approved') is True}
    manifest = {'mode': 'approved', 'sources': source_rules, 'homes': [],
                'publish_operator_renovation_estimates': publish_operator_estimates is True}
    reviewed = reviewed or {}
    saved_assessments = saved_assessments or {}
    for doc in listings:
        source = doc.get('SOURCE_MLS')
        listing_id = doc.get('LISTING_ID')
        def excluded(reason):
            if audit is not None:
                audit(doc, reason)
        if not isinstance(source, str) or not isinstance(listing_id, str) or not listing_id:
            excluded('missing_identity')
            continue
        if source not in source_rules:
            excluded('source_not_enabled')
            continue
        if audit is not None and doc.get('COUNTY_OR_PARISH') not in BAY_COUNTY_QUERY['$in']:
            excluded('outside_publisher_geography')
            continue
        config = source_rules[source]
        details = doc.get('details') or {}
        market = transaction_market(doc, details)
        if audit is not None and market != 'sale':
            excluded('not_active_sale')
            continue
        if market is None or (market == 'sold' and config.get('sold_search_days', 0) <= 0):
            continue
        display = details.get('INTERNET_DISPLAY')
        if (not isinstance(display, str)
                or details.get('INTERNET_SITES') not in config.get('internet_sites_values', ('Yes - All Sites',))):
            excluded('display_or_internet_permission')
            continue
        allowed = config.get('internet_display_values', {}).get(display)
        if not isinstance(allowed, list) or 'internet' not in allowed:
            excluded('display_or_internet_permission')
            continue
        refreshed = read_stamp(doc.get('_updated_at'))
        permission_checked = read_stamp(doc.get('details_fetched'))
        if refreshed is None or permission_checked is None:
            excluded('missing_refresh_or_display_check')
            continue
        detail_status = details.get('status')
        if (live_inventory and permission_checked >= refreshed and isinstance(detail_status, str)
                and detail_status and detail_status not in (('SLD',) if market == 'sold' else ACTIVE)):
            continue
        refresh_hours = config.get('listing_refresh_hours')
        if type(refresh_hours) not in (int, float) or not math.isfinite(refresh_hours) or refresh_hours <= 0:
            raise ValueError(f'Set a valid listing refresh interval for {source}')
        permission_hours = config.get('permission_refresh_hours', refresh_hours)
        if type(permission_hours) not in (int, float) or not math.isfinite(permission_hours) or permission_hours <= 0:
            raise ValueError(f'Set a valid permission refresh interval for {source}')
        inventory_expires = refreshed + timedelta(hours=permission_hours if market == 'sold' else refresh_hours)
        expires = permission_checked + timedelta(hours=permission_hours)
        if market == 'sold' or not live_inventory:
            expires = min(expires, inventory_expires)
        close_date = None
        if market == 'sold':
            close_date = read_stamp(details.get('CLOSE_DATE'))
            if not close_date or not close_date <= now < close_date + timedelta(days=config['sold_search_days']):
                continue
            expires = min(expires, close_date + timedelta(days=config['sold_search_days']))
        if not max(refreshed, permission_checked) <= now < expires:
            excluded('past_or_future_refresh_window')
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
               'id': publication_id(id_key, source, listing_id),
               'policy': {'basis': f'{source}: INTERNET_DISPLAY={display}',
                          'effective': max(refreshed, permission_checked).isoformat(), 'refresh_by': expires.isoformat(),
                          'allow': permissions},
               'credit': {'source': config.get('credit_name', source), 'mls_id': listing_id}}
        if live_inventory:
            row['inventory_stale'] = market != 'sold' and now >= inventory_expires
            row['mls_status_label'] = ACTIVE_STATUS_LABELS.get(doc.get('RAW_STATUS'), 'Sold')
        if audit is not None:
            grants, _ = rights(manifest, row, now=now.isoformat())
            if not {'internet', 'price'} <= grants:
                excluded('source_or_listing_grant')
            elif type(doc.get('LIST_PRICE')) is not int or doc['LIST_PRICE'] <= 0:
                excluded('missing_or_invalid_price')
            else:
                excluded('eligible')
            continue
        for key, field in MAPPED.items():
            if field in doc:
                row[key] = doc[field]
        if not summary and permissions.get('credits') and permissions.get('address'):
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
        if not summary and permissions.get('facts'):
            row['facts'] = public_facts(doc, details)
        if (not summary and permissions.get('public_remarks') and config.get('publish_public_remarks') is True
                and (remarks := public_text(details.get('PUBLIC_REMARKS'), maxlength=6500))):
            row['reviewed_public_remarks'] = remarks
            row['remarks_dependency_free'] = True
        if all(type(doc.get(k)) in (int, float) and math.isfinite(doc[k])
               for k in ('LATITUDE', 'LONGITUDE')):
            row['coordinates'] = [doc['LATITUDE'], doc['LONGITUDE']]
        if config.get('publish_source_photos') is True and permissions['media'] and permissions['address']:
            gallery = doc.get('photos')
            if not isinstance(gallery, list):
                gallery = []
            photo_urls = approved_mls_gallery(details.get('PRIMARY_PHOTO_URI'), gallery)
            embedded = doc.get('gallery_photo_labels')
            embedded_categories = {
                label['photo_id']: label['category'] for label in embedded
                if isinstance(label, dict) and isinstance(label.get('photo_id'), str)
                and isinstance(label.get('category'), str) and label['category'] in CATEGORIES
            } if isinstance(embedded, list) else {}
            row['media'] = [
                {'display': True, 'mls_photo_url': url,
                 'category': embedded_categories.get(mls_photo_variant(url)[0])}
                for url, _ in photo_urls
            ]
        for key in ('media', 'reviewed_public_remarks', 'remarks_dependency_free', 'analysis_origin',
                    'cost_items', 'cost_version', 'cost_dependency_free', 'partial_budget', 'analysis_date'):
            if key in evidence:
                row[key] = evidence[key]
        approval_time = read_stamp(evidence.get('purchase_assessment_created_at'))
        assessment = saved_assessments.get(listing_id)
        if (market == 'sale' and publish_operator_estimates is True and assessment
                and assessment.get('method') == 'active_listing_proposed_visible_work'):
            row['operator_flip_assessment'] = {key: assessment.get(key) for key in (*ASSESSMENT_NUMBERS, 'observations')}
        if (market == 'sale' and approval_time is not None and approval_time <= now and assessment
                and read_stamp(assessment.get('created_at')) == approval_time
                and assessment.get('valuation_status') == 'AI_estimate_requires_review'
                and evidence.get('purchase_assessment_refresh_by')
                and all(permissions.get(field) for field in ('derived', 'valuation', 'sold_comps', 'ai', 'address'))):
            row.setdefault('analysis_origin', 'model')
            row['reviewed_purchase_assessment'] = {key: assessment.get(key) for key in ASSESSMENT_NUMBERS}
            row['purchase_assessment_refresh_by'] = evidence['purchase_assessment_refresh_by']
        manifest['homes'].append(row)
    return manifest
