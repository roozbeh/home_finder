"""Read the public property catalog directly from the MLS collection."""
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from itertools import islice
import hmac
import json
from pathlib import Path
import re

from pymongo import MongoClient

from buyer_listing_facts import FACT_KEYS
from buyer_live_feed import ACTIVE, BAY_COUNTY_QUERY, MAPPED, PHOTO_LABEL_PROJECTION, PROJECTION, manifest_for, publication_id
from buyer_property_types import PROPERTY_GROUPS, normalized, type_filter
from buyer_publish import expire_payload, project


GALLERY_PROJECTION = {
    'photos': 1,
    'details.PRIMARY_PHOTO_URI': 1,
    **PHOTO_LABEL_PROJECTION,
}
DETAIL_PROJECTION = {
    **PROJECTION,
    **GALLERY_PROJECTION,
    **dict.fromkeys(('details.status', 'details.BATHROOMS_HALF', 'details.BATHS_DISPLAY',
                     'details.YEAR_BUILT', 'details.LOT_SQFT', 'details.SQFT',
                     'details.BEDROOMS_TOTAL', 'BATHS_DISPLAY'), 1),
    '_id': 0,
}
SEARCH_PROJECTION = {
    field: include for field, include in DETAIL_PROJECTION.items()
    if field not in {
        *FACT_KEYS, *(f'details.{key}' for key in FACT_KEYS),
        'details.PUBLIC_REMARKS', 'details.LANAMEBIZCARD.html', 'details.LIST_OFFICE_BCARD.html',
        'details.REPORT_LIST_AGENT_PHONE', 'details.LOPHONENO',
    }
}
# Some fact fields also supply search filters and the card summary.
SEARCH_PROJECTION.update(dict.fromkeys((
    *MAPPED.values(), 'BATHS_DISPLAY', 'BUILDING_TYPE', 'details.BUILDING_TYPE',
    'PROPERTY_SUBTYPE', 'details.PROPERTY_SUBTYPE', 'details.PROPERTY_TYPE_DISPLAY',
    'details.BEDROOMS_TOTAL', 'details.SQFT', 'details.LOT_SQFT', 'details.YEAR_BUILT',
    'details.BATHROOMS_FULL', 'details.BATHROOMS_HALF', 'details.BATHS_DISPLAY',
), 1))


def matches(home, filters):
    if filters.get('city') and home.get('city') != filters['city']:
        return False
    for name, field in (
        ('min_price', 'price'), ('beds', 'beds'), ('baths', 'baths'),
        ('min_sqft', 'sqft'), ('min_lot', 'lot_sqft'), ('min_year', 'year_built'),
        ('dom', 'days_on_market'),
    ):
        minimum = filters.get(name)
        if minimum is not None and (home.get(field) is None or home[field] < minimum):
            return False
    maximum = filters.get('max_price')
    if maximum is not None and home['price'] > maximum:
        return False
    selected = filters.get('property_type') or ()
    if isinstance(selected, str):
        selected = (type_filter(selected),)
    if selected and not any(
        home.get('property_type_group') == value if value in PROPERTY_GROUPS
        else normalized(home.get('property_type', '')) == normalized(value)
        for value in selected
    ):
        return False
    for name, field in (('photos_only', 'photos'), ('reduced', 'reduced'),
                        ('auction', 'auction'), ('bank_owned', 'bank_owned')):
        if filters.get(name) and not home.get(field):
            return False
    for name, field in (('photo_categories', 'photo_groups'), ('purchase_recommendation', 'recommendation')):
        if (choice := filters.get(name)) and bool(home.get(field)) != (choice == 'yes'):
            return False
    if choice := filters.get('good_investment'):
        recommendation = home.get('recommendation')
        if not recommendation or recommendation.get('profitable') != (choice == 'yes'):
            return False
    return True


class AtlasCatalog:
    def __init__(self, collection, config, id_key, asset_dir, *, clock=None):
        if len(id_key) < 32:
            raise RuntimeError('Public Atlas access requires the existing public listing ID key')
        self.collection = collection
        self.rules = {name: rule for name, rule in config['sources'].items()
                      if rule.get('production_approved') is True}
        self.reviewed = config.get('reviewed_listings') or {}
        self.id_key = id_key
        self.asset_dir = asset_dir
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._resolve_id = lru_cache(maxsize=256)(self._find_identity)

    @classmethod
    def from_environment(cls, uri, policy_path, key):
        if not uri or not policy_path:
            raise RuntimeError('Public Atlas access requires BUYER_ATLAS_URI and BUYER_ATLAS_POLICY')
        path = Path(policy_path)
        config = json.loads(path.read_text())
        id_key = key.encode() if key else path.with_name('public-id-key').read_bytes().strip()
        client = MongoClient(uri, connect=False, serverSelectionTimeoutMS=8000)
        return cls(client['mls']['mls_listings'], config, id_key, path.parent / 'media')

    def _eligible_candidates(self, market=None):
        branches = []
        for source, rule in self.rules.items():
            if market == 'sold' and rule.get('sold_search_days', 0) <= 0:
                continue
            allowed = [display for display, fields in rule.get('internet_display_values', {}).items()
                       if isinstance(fields, list) and {'internet', 'price'} <= set(fields)]
            branches.append({
                'SOURCE_MLS': source,
                'details.INTERNET_DISPLAY': {'$in': allowed},
                'details.INTERNET_SITES': {'$in': list(rule.get('internet_sites_values', ('Yes - All Sites',)))},
            })
        if not branches:
            return {'_id': {'$exists': False}}
        statuses = ['SLD'] if market == 'sold' else list(ACTIVE) if market else [*ACTIVE, 'SLD']
        query = {'COUNTY_OR_PARISH': BAY_COUNTY_QUERY, 'RAW_STATUS': {'$in': statuses}, '$or': branches}
        if market in ('sale', 'rent'):
            query['LIST_PRICE'] = {'$gt': 0}
        return query

    def _homes(self, query, now, *, projection=DETAIL_PROJECTION, summary=False):
        with self.collection.find(query, projection) as cursor:
            while batch := list(islice(cursor, 100)):
                yield from self._project(batch, now, summary=summary)

    def _project(self, docs, now, *, summary=False):
        manifest = manifest_for(
            docs, self.rules, self.id_key, now=now, live_inventory=True,
            reviewed=self.reviewed, summary=summary,
        )
        now_iso = now.isoformat()
        for row in manifest['homes']:
            result, assets = project(manifest, row, self.asset_dir, now=now_iso)
            if result:
                home, _ = result
                # Keep the same public payload as the catalog reader.
                yield expire_payload(home, now_iso), assets, row['credit']['mls_id']

    def _summaries(self, filters, now, *, include_gallery=False, include_all_cities=False):
        market = filters.get('market', 'sale')
        query = self._eligible_candidates(market)
        if filters.get('city') and not include_all_cities:
            query['CITY'] = filters['city']
        projection = SEARCH_PROJECTION if include_gallery else {
            field: value for field, value in SEARCH_PROJECTION.items() if field not in GALLERY_PROJECTION
        }
        for home, _, listing_id in self._homes(query, now, projection=projection, summary=True):
            if home['market'] == market:
                yield home, listing_id

    def search(self, mode, filters, page, size=12):
        if mode != 'approved':
            return [], [], [0, 0, 0, 0], 0
        now = self.clock()
        cities = Counter()
        capabilities = [0, 0, 0, 0]
        matching = []
        gallery_filter = bool(filters.get('photos_only') or filters.get('photo_categories'))
        for home, listing_id in self._summaries(filters, now, include_gallery=gallery_filter, include_all_cities=True):
            cities[home.get('city')] += 1
            for index, field in enumerate(('days_on_market', 'reduced', 'auction', 'bank_owned')):
                capabilities[index] += field in home
            if matches(home, filters):
                matching.append((home, listing_id))
        matching.sort(key=lambda item: (item[0]['price'], item[0]['id']))
        offset = (page - 1) * size
        selected = matching[offset:offset + size]
        homes = [home for home, _ in selected]
        if selected and not gallery_filter:
            market = filters.get('market', 'sale')
            query = {**self._eligible_candidates(market), 'LISTING_ID': {'$in': [key for _, key in selected]}}
            current = {home['id']: home for home, _, _ in
                       self._homes(query, now, projection=SEARCH_PROJECTION, summary=True)
                       if home['market'] == market and matches(home, filters)}
            homes = [current[home['id']] for home in homes if home['id'] in current]
        return homes, sorted(cities.items(), key=lambda item: item[0] or ''), capabilities, len(matching)

    def _matches(self, mode, filters, *, include_gallery=False):
        if mode == 'approved':
            gallery = include_gallery or filters.get('photos_only') or filters.get('photo_categories')
            for home, _ in self._summaries(filters, self.clock(), include_gallery=gallery):
                if matches(home, filters):
                    yield home

    def count(self, mode, filters):
        return sum(1 for _ in self._matches(mode, filters))

    def matching_locations(self, mode, filters):
        return sorted(
            (home['id'], home['price'], home['location'], home['coordinates'])
            for home in self._matches(mode, filters) if 'coordinates' in home
        )

    def _find_identity(self, public_id):
        with self.collection.find(self._eligible_candidates(), {'_id': 0, 'SOURCE_MLS': 1, 'LISTING_ID': 1}) as cursor:
            for row in cursor:
                source, listing_id = row.get('SOURCE_MLS'), row.get('LISTING_ID')
                if isinstance(listing_id, str) and hmac.compare_digest(
                    publication_id(self.id_key, source, listing_id), public_id
                ):
                    return source, listing_id
        raise LookupError(public_id)

    def _lookup(self, mode, public_id):
        if mode != 'approved' or not re.fullmatch(r'[0-9a-f]{32}', public_id):
            return None, []
        # An existing HMAC URL cannot be decoded. Only the identity is cached.
        # Always re-read the indexed listing and apply its current display rules.
        try:
            source, listing_id = self._resolve_id(public_id)
        except LookupError:
            return None, []
        query = {**self._eligible_candidates(), 'SOURCE_MLS': source, 'LISTING_ID': listing_id}
        doc = self.collection.find_one(query, DETAIL_PROJECTION)
        if not doc:
            return None, []
        home, assets, _ = next(self._project([doc], self.clock()), (None, [], None))
        return home, assets

    def get(self, mode, public_id):
        return self._lookup(mode, public_id)[0]

    def map_home(self, mode, filters, public_id):
        home = self.get(mode, public_id)
        if home and 'coordinates' in home and home['market'] == filters.get('market', 'sale') and matches(home, filters):
            return home
        return None

    def media(self, mode, public_id, media_id):
        _, assets = self._lookup(mode, public_id)
        return next(((content, mime) for _, key, content, mime in assets if key == media_id), None)
