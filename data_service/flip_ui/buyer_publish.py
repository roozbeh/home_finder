"""Apply display grants to normalized listing fields and assets."""
import hashlib
import math
import re
from datetime import date, datetime, timezone

from buyer_analysis import comparable_sales, money, project_matches, purchase_recommendation, saved_flip_breakdown, scopes
from buyer_listing_facts import checked_facts, listing_credit, public_text
from buyer_media import approved_mls_photo_url, validated_jpeg
from buyer_photo_labels import CATEGORIES
from buyer_property_types import property_type_group, property_type_label

LOCAL_ESTIMATE_METHOD = 'local_visible_work_unit_rates'

FIELDS = ('city', 'price', 'property_type', 'beds', 'baths', 'sqft', 'status', 'address',
          'geolocation', 'media', 'public_remarks', 'credits', 'updated', 'days_on_market',
          'original_price', 'auction', 'bank_owned', 'ai', 'derived', 'valuation',
          'sold_comps', 'historical_projects', 'comments', 'sale_date', 'purchase_price',
          'purchase_date', 'verified_costs', 'comp_matching', 'facts', 'sold_search')


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def expire_payload(data, now):
    for expiry, fields in (
        ('sales_expires', ('sales', 'resale')),
        ('projects_expires', ('projects',)),
        ('recommendation_expires', ('recommendation',)),
    ):
        if data.pop(expiry, now) <= now:
            for key in fields:
                data.pop(key, None)
    return data


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Permission times must include an offset')
    return result.astimezone(timezone.utc).isoformat()


def checked_operator_estimate(estimate):
    if not isinstance(estimate, dict):
        return None
    low, high = estimate.get('low'), estimate.get('high')
    if all(type(value) in (int, float) and math.isfinite(value) for value in (low, high)) and 0 <= low <= high:
        return {'low': low, 'high': high}
    return None


SAVED_COST_RANGE = re.compile(
    r'\$([0-9][0-9,]*(?:\.[0-9]+)?)(k?)\s*[-–—]\s*\$([0-9][0-9,]*(?:\.[0-9]+)?)(k?)',
    re.IGNORECASE,
)
MLS_EVIDENCE_TEXT = re.compile(
    r'\b(?:MLS|comps?|comparables?|closed sales?|private remarks?|showing|seller|owner|agent|contact|address)\b',
    re.IGNORECASE,
)


def saved_work_note(detail):
    if not isinstance(detail, str):
        return None
    sentences = re.split(r'(?<=[.!?])\s+', detail)
    notes = [note for sentence in sentences if not MLS_EVIDENCE_TEXT.search(sentence)
             and (note := public_text(sentence, maxlength=900))]
    return public_text(' '.join(notes), maxlength=900)


def project_operator_assessment(home, expires, assessment):
    """Copy operator-authorized estimates, omitting underlying photo evidence."""
    estimate = checked_operator_estimate({'low': assessment.get('renovation_low'),
                                          'high': assessment.get('renovation_high')})
    if assessment.get('method') == LOCAL_ESTIMATE_METHOD:
        areas = []
        for area in (assessment.get('area_estimates') or {}).values():
            if not isinstance(area, dict) or not (label := public_text(area.get('label'), maxlength=50)):
                continue
            items = []
            for observation in area.get('items') or []:
                if not isinstance(observation, dict):
                    continue
                task = public_text(observation.get('task'), maxlength=90)
                if task:
                    items.append({'label': task, 'detail': saved_work_note(observation.get('detail')),
                                  **(checked_operator_estimate(observation) or {})})
            areas.append({'label': label, 'condition': saved_work_note(area.get('condition')),
                          'partial': area.get('partial') is True, 'items': items,
                          **(checked_operator_estimate(area) or {})})
        if areas:
            home['operator_renovation_estimate'] = {
                **(estimate or {}), 'areas': areas, 'partial': assessment.get('state') == 'partial',
            }
        return
    if not estimate:
        return
    home['operator_renovation_estimate'] = estimate
    existing = home.get('recommendation')
    if ((existing and not existing.get('operator_saved'))
            or not (breakdown := saved_flip_breakdown(assessment, home['price']))):
        return
    items = []
    for observation in assessment.get('observations') or []:
        if not isinstance(observation, dict):
            continue
        label = public_text(observation.get('area'), maxlength=60)
        detail = observation.get('detail')
        match = SAVED_COST_RANGE.search(detail) if isinstance(detail, str) else None
        if not label or not match:
            continue
        low, low_scale, high, high_scale = match.groups()
        low = float(low.replace(',', '')) * (1_000 if low_scale else 1)
        high = float(high.replace(',', '')) * (1_000 if high_scale else 1)
        if estimate_item := checked_operator_estimate({'low': low, 'high': high}):
            items.append({'label': label, **estimate_item, 'detail': saved_work_note(detail)})
    ledger = [{'label': label, 'amount': money(amount)} for label, amount in breakdown.pop('lines')
              if label != 'HOA carrying cost' or amount]
    rounding = -sum(line['amount'] for line in ledger[:-1])
    if rounding:
        ledger.insert(-1, {'label': 'Whole-dollar rounding', 'amount': rounding})
    profit = -breakdown['gap'] * 1.1025
    home['recommendation'] = {
        **{key: money(value) for key, value in breakdown.items()},
        'operator_saved': True, 'hold_months': 10, 'ledger': ledger, 'renovation_items': items,
        'renovation_range': estimate, 'result': money(profit), 'profitable': money(profit) > 0,
    }
    home['recommendation_expires'] = expires


def rights(manifest, row, *, now=None):
    now = now or utcnow()
    source = manifest['sources'].get(row.get('source'), {})
    policy = row.get('policy', {})
    if manifest.get('mode') == 'approved' and source.get('production_approved') is not True:
        return set(), now
    grants = []
    for rule in (source, policy):
        try:
            starts = timestamp(rule['effective'])
            expires = timestamp(rule['refresh_by'])
            if not rule['basis'] or not starts <= now < expires:
                return set(), now
        except (KeyError, TypeError, ValueError):
            return set(), now
        grants.append(expires)
    allowed = {field for field in (*FIELDS, 'internet') if
               source.get('allow', {}).get(field) is True and policy.get('allow', {}).get(field) is True}
    return allowed, min(grants)


def approved_evidence(manifest, row, feature):
    allowed, expires = rights(manifest, row)
    needed = {'internet', feature, 'sqft', 'property_type', 'price', 'sale_date', 'comp_matching'}
    if feature == 'historical_projects':
        needed.update(('purchase_price', 'purchase_date'))
    # This normalized feed's evidence record is approved as a group; it has no address or media.
    if row.get('withdrawn') or not needed <= allowed:
        return None
    keys = ('id', 'subject_id', 'property_type', 'sqft', 'finish', 'sale_date', 'price',
            'purchase_date', 'purchase_price', 'pair_verified', 'flip_verified', 'available_date')
    result = {key: row[key] for key in keys if key in row}
    for key in ('city', 'beds', 'verified_costs'):
        if key in allowed and key in row:
            result[key] = row[key]
    if 'verified_costs' in allowed and row.get('verified_costs_public') is True:
        result['verified_costs_public'] = True
    if {'geolocation', 'address'} <= allowed:
        for key in ('coordinates', 'distance_miles'):
            if key in row:
                result[key] = row[key]
    return {**result, 'expires': expires}


def project(manifest, row, asset_dir, *, now=None):
    allow, expires = rights(manifest, row, now=now)
    market = row.get('market', 'sale')
    required = {'internet', 'price'} | ({'sold_search'} if market == 'sold' else set())
    if (row.get('withdrawn') or market not in {'sale', 'rent', 'sold'}
            or row.get('status') != ('Sold' if market == 'sold' else 'Active') or not required <= allow):
        return None, []
    home_id = row['id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,64}', home_id):
        raise ValueError('Use a stable opaque publication ID')
    home = {'id': home_id, 'preview': manifest['mode'] == 'preview', 'photos': [], 'market': market}
    for field in ('lot_sqft', 'year_built'):
        if 'facts' in allow and type(row.get(field)) is int and row[field] > 0:
            home[field] = row[field]
    if 'facts' in allow and isinstance(row.get('facts'), dict):
        home['facts'] = checked_facts(row['facts'])
    if market == 'sold' and 'sale_date' in allow:
        home['sale_date'] = row.get('sale_date')
    field_types = {'city': (str,), 'price': (int,), 'property_type': (str,), 'beds': (int,),
                   'baths': (int, float), 'sqft': (int,), 'status': (str,), 'updated': (str,)}
    for key, types in field_types.items():
        if key in allow and type(row.get(key)) in types:
            home[key] = row[key]
    if 'status' in allow and 'mls_status_label' in row:
        home['status'] = row['mls_status_label']
    if 'updated' in home and row.get('inventory_stale'):
        home['inventory_stale'] = True
    if 'property_type' in home:
        value = home.pop('property_type')
        label = property_type_label(value)
        if label:
            home['property_type'] = label
            if group := property_type_group(label):
                home['property_type_group'] = group
    property_facts = home.get('facts', {}).get('Property and parking', {})
    if 'Property subtype' in property_facts:
        if 'property_type' in home:
            property_facts['Property subtype'] = home['property_type']
        else:
            property_facts.pop('Property subtype')
    if type(home.get('price')) is not int or home['price'] <= 0:
        return None, []
    address_ok = 'address' in allow and bool(row.get('address'))
    home['location'] = row['address'] if address_ok else home.get('city', 'Bay Area')
    if address_ok and (locality := public_text(row.get('locality'))):
        home['locality'] = locality
    if not address_ok and home.get('facts'):
        for group, labels in (('Community and HOA', ('Community', 'Association name')),
                              ('Property and parking', ('Parcel number',))):
            for label in labels:
                home['facts'].get(group, {}).pop(label, None)
    if address_ok and 'geolocation' in allow:
        coordinates = row.get('coordinates', [])
        if (len(coordinates) == 2 and all(type(n) in (int, float) for n in coordinates)
                and -90 <= coordinates[0] <= 90 and -180 <= coordinates[1] <= 180):
            home['coordinates'] = coordinates
    if address_ok and 'public_remarks' in allow and row.get('reviewed_public_remarks') and row.get('remarks_dependency_free') is True:
        remarks = public_text(row['reviewed_public_remarks'], maxlength=6500)
        if remarks:
            home['remarks'] = remarks
    if 'credits' in allow:
        credit = row.get('credit', {})
        home['credit'] = {'source': str(credit.get('source', ''))}
        # Attribution can reveal a withheld address through another service.
        if address_ok:
            listing_id = credit.get('mls_id')
            if isinstance(listing_id, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,31}', listing_id):
                home['credit']['mls_id'] = listing_id
            home['credit'].update(listing_credit(credit))
    if 'updated' in allow:
        try:
            checked = datetime.fromisoformat(row['details_checked'])
            if checked.tzinfo is not None:
                checked = checked.astimezone(timezone.utc)
                home['details_checked'] = checked.isoformat(timespec='minutes')
        except (KeyError, TypeError, ValueError):
            pass
        try:
            home['listing_updated'] = date.fromisoformat(row['listing_updated']).isoformat()
        except (KeyError, TypeError, ValueError):
            pass
    if 'days_on_market' in allow and type(row.get('days_on_market')) is int and row['days_on_market'] >= 0:
        home['days_on_market'] = row['days_on_market']
    for key in ('auction', 'bank_owned'):
        if key in allow and key in row:
            home[key] = row[key]
    if 'original_price' in allow and type(row.get('original_price')) is int:
        home['reduced'] = row['original_price'] > home['price']
    assets = []
    if 'media' in allow:
        for photo in row.get('media', []):
            category = photo.get('category')
            if (photo.get('display') is not True or category == 'floor_plan'
                    or (not address_ok and photo.get('address_safe') is not True)):
                continue
            mime = 'image/jpeg'
            if 'mls_photo_url' in photo:
                url = approved_mls_photo_url(photo['mls_photo_url'])
                if not url or not address_ok:
                    continue
                mime = 'application/x-mls-photo-reference'
                data = url.encode('utf-8')
            else:
                name = photo['sanitized_file']
                if not re.fullmatch(r'[a-zA-Z0-9_-]+\.jpg', name):
                    raise ValueError('Expected an approved sanitized JPEG asset name')
                data = validated_jpeg((asset_dir / name).read_bytes())
            media_id = hashlib.sha256(data).hexdigest()[:24]
            if media_id not in home['photos']:
                home['photos'].append(media_id)
                if category in CATEGORIES:
                    home.setdefault('photo_groups', {}).setdefault(CATEGORIES[category], []).append(media_id)
                assets.append((home_id, media_id, data, mime))
    subject = {**row, '_location_visible': address_ok and 'geolocation' in allow}
    if not subject['_location_visible']:
        subject.pop('coordinates', None)
    if 'derived' in allow and (row.get('analysis_origin') == 'reviewer'
                               or (row.get('analysis_origin') == 'model' and 'ai' in allow)):
        if row.get('cost_items') and row.get('cost_dependency_free') is True:
            home['scopes'] = scopes(row['cost_items'], row.get('cost_version', ''), row.get('partial_budget', True), address_ok)
        if 'valuation' in allow and 'sold_comps' in allow:
            sales = [e for comp in manifest.get('sales', []) if (e := approved_evidence(manifest, comp, 'sold_comps'))]
            selected = comparable_sales(subject, sales)
            if len(selected) >= 2:
                home['sales_expires'] = min(sale.pop('expires') for sale in selected)
                home['sales'] = selected
                home['resale'] = round(sum(sale['price'] for sale in selected) / len(selected))
        if 'historical_projects' in allow and 'valuation' in allow:
            projects = [e for pair in manifest.get('projects', []) if (e := approved_evidence(manifest, pair, 'historical_projects'))]
            selected = project_matches(subject, projects)
            if selected:
                home['projects_expires'] = min(pair.pop('expires') for pair in selected)
                home['projects'] = selected
        if market == 'sale' and address_ok and {'valuation', 'sold_comps'} <= allow:
            reviewed = row.get('reviewed_purchase_assessment')
            recommendation = purchase_recommendation(home)
            recommendation_expires = home.get('sales_expires', '')
            if not recommendation and reviewed and 'ai' in allow:
                recommendation = purchase_recommendation(home, reviewed)
                try:
                    recommendation_expires = min(expires, timestamp(row['purchase_assessment_refresh_by']))
                except (KeyError, TypeError, ValueError):
                    recommendation_expires = ''
            if recommendation and recommendation_expires > utcnow():
                home['recommendation'] = recommendation
                home['recommendation_expires'] = recommendation_expires
    if market == 'sale' and manifest.get('publish_operator_renovation_estimates') is True:
        if isinstance(assessment := row.get('operator_flip_assessment'), dict):
            if assessment.get('method') != LOCAL_ESTIMATE_METHOD or {'address', 'media'} <= allow:
                project_operator_assessment(home, expires, assessment)
    return (home, expires), assets
