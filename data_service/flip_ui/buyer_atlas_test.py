from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from flask import Flask

from buyer_atlas import AtlasCatalog
from buyer_live_feed import publication_id
from buyer_media import mls_photo_variant
from public_buyer import register_public


NOW = datetime(2026, 11, 22, 20, tzinfo=timezone.utc)
KEY = b'fixture-opaque-publication-key-000'
PHOTO = 'https://media.mlslmedia.com/property/MLSL/82065431/00000000000000000000000000000001/2/1'
FIELDS = ('internet', 'city', 'price', 'baths', 'beds', 'status', 'address', 'geolocation', 'media',
          'property_type', 'updated', 'credits')


class ListingCollection:
    def __init__(self, listing):
        self.listing = listing

    def find(self, query, projection):
        doc = self.find_one(query, projection)
        return nullcontext(iter([doc] if doc else []))

    def find_one(self, query, projection):
        def value(field):
            result = self.listing
            for key in field.split('.'):
                result = result.get(key, {}) if isinstance(result, dict) else {}
            return result

        def matches(clause):
            for key, expected in clause.items():
                if key == '$or':
                    if not any(matches(branch) for branch in expected):
                        return False
                elif isinstance(expected, dict):
                    if '$in' in expected and value(key) not in expected['$in']:
                        return False
                    if '$gt' in expected and not value(key) > expected['$gt']:
                        return False
                elif value(key) != expected:
                    return False
            return True

        if matches(query):
            doc = {}
            for field, include in projection.items():
                if not include:
                    continue
                head, _, tail = field.partition('.')
                if tail and isinstance(self.listing.get(head), list):
                    labels = self.listing[head]
                    projected = doc.setdefault(head, [{} for _ in labels])
                    for label, entry in zip(labels, projected):
                        if tail in label:
                            entry[tail] = label[tail]
                elif value(field) == {}:
                    continue
                elif tail:
                    doc.setdefault(head, {})[tail] = value(field)
                else:
                    doc[head] = value(field)
            return doc
        return None


class AtlasBuyerTest(TestCase):
    def setUp(self):
        self.listing = {
            'LISTING_ID': 'ML82065431', 'SOURCE_MLS': 'MLSLISTINGS',
            'COUNTY_OR_PARISH': 'San Mateo', 'CITY': 'MENLO PARK', 'RAW_STATUS': 'ACTV',
            'STREET_ADDRESS': '1149 Berkeley', 'LIST_PRICE': 998000, 'BEDROOMS_TOTAL': 3,
            'LATITUDE': 37.4, 'LONGITUDE': -122.2, 'photos': [PHOTO],
            '_updated_at': NOW - timedelta(days=28), 'details_fetched': NOW - timedelta(days=28),
            'details': {'INTERNET_DISPLAY': 'Full Street Address', 'INTERNET_SITES': 'Yes - All Sites',
                        'SALE_OR_RENT': 'For Sale', 'BATHROOMS_FULL': '2', 'BATHROOMS_HALF': '1',
                        'PROPERTY_SUBTYPE': 'Single Family Residence', 'PRIVATE_REMARKS': 'agent only'},
        }
        config = {'sources': {'MLSLISTINGS': {
            'production_approved': True, 'basis': 'Fixture display agreement',
            'effective': (NOW - timedelta(days=30)).isoformat(),
            'refresh_by': (NOW + timedelta(days=30)).isoformat(),
            'listing_refresh_hours': 48, 'permission_refresh_hours': 2160,
            'allow': dict.fromkeys(FIELDS, True), 'publish_source_photos': True,
            'internet_display_values': {'Full Street Address': list(FIELDS),
                                        'No Address': ['internet', 'city', 'price', 'updated']},
        }}}
        self.collection = ListingCollection(self.listing)
        self.catalog = AtlasCatalog(self.collection, config, KEY, Path('.'), clock=lambda: NOW)
        app = Flask(__name__)
        app.secret_key = 'staging-test-secret-more-than-32-bytes'
        app.config['SESSION_COOKIE_SECURE'] = True
        app.jinja_env.globals['csrf_token'] = lambda: 'test-token'
        with patch.dict('os.environ', {'BUYER_ATLAS_STAGING': 'true', 'BUYER_MODE': 'approved'}), \
                patch.object(AtlasCatalog, 'from_environment', return_value=self.catalog):
            register_public(app, lambda view: view, lambda: None)
        self.assertNotIn('buyer_inbox', app.extensions)
        self.client = app.test_client()
        self.public_id = publication_id(KEY, 'MLSLISTINGS', 'ML82065431')
        self.url = f'/homes/{self.public_id}'

    def test_stale_active_listing_and_next_request_updates(self):
        for path in ('/homes?property_type=houses&baths=2.5', self.url):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            for text in (b'$998,000', b'1149 Berkeley', b'Active', b'Inventory last checked', b'2026-10-25'):
                self.assertIn(text, response.data)
            self.assertNotIn(b'agent only', response.data)
            self.assertIn('noindex', response.headers['X-Robots-Tag'])
        self.assertEqual(self.client.get('/homes/property-type-count?property_type=houses').json, {'count': 1})
        self.assertEqual(self.client.get('/homes/map-data').json[0]['price'], 998000)
        preview = self.client.get(f'{self.url}/map-preview').json
        self.assertTrue(preview['last_checked'].startswith('2026-10-25'))
        old_media = preview['photo']

        self.listing.update(LIST_PRICE=980000, RAW_STATUS='PCH', photos=[PHOTO[:-1] + '2'])
        self.assertIn(b'$980,000', self.client.get(self.url).data)
        self.assertIn(b'Price changed', self.client.get(self.url).data)
        self.assertEqual(self.client.get(old_media).status_code, 404)
        new_media = self.client.get(f'{self.url}/map-preview').json['photo']
        with patch('public_buyer.fetch_mls_photo', return_value=b'sanitized-image'):
            self.assertEqual(self.client.get(new_media).data, b'sanitized-image')

        self.listing['details']['INTERNET_DISPLAY'] = 'No Address'
        self.assertIn(b'MENLO PARK', self.client.get(self.url).data)
        self.assertNotIn(b'1149 Berkeley', self.client.get(self.url).data)
        self.assertEqual(self.client.get('/homes/map-data').json, [])
        self.assertEqual(self.client.get(new_media).status_code, 404)
        self.assertEqual(self.client.get(f'{self.url}/contact').status_code, 410)
        self.assertEqual(self.client.get('/agents/partner').status_code, 410)

    def test_withdrawal_opt_out_and_expired_permission_revoke_all_routes(self):
        photo = self.client.get(f'{self.url}/map-preview').json['photo']
        for doc, field, replacement in (
            (self.listing, 'RAW_STATUS', 'WDN'),
            (self.listing['details'], 'status', 'WDN'),
            (self.listing['details'], 'INTERNET_SITES', 'No'),
            (self.listing, 'details_fetched', NOW - timedelta(days=91)),
        ):
            with self.subTest(field=field), patch.dict(doc, {field: replacement}):
                self.assertEqual(self.client.get('/homes/property-type-count').json, {'count': 0})
                self.assertEqual(self.client.get('/homes/map-data').json, [])
                self.assertNotIn(self.public_id.encode(), self.client.get('/homes').data)
                for path in (self.url, f'{self.url}/map-preview', photo):
                    self.assertEqual(self.client.get(path).status_code, 404)

    def test_embedded_categories_follow_photo_identity_and_hide_floor_plans(self):
        alternate_size = PHOTO.rsplit('/', 2)[0] + '/1/1'
        second_photo, floor_plan = PHOTO[:-1] + '2', PHOTO[:-1] + '3'
        self.listing['photos'] = [alternate_size, second_photo, floor_plan]
        floor_plan_url = f'{self.url}/media/{sha256(floor_plan.encode()).hexdigest()[:24]}'
        self.listing['gallery_photo_labels'] = [
            {'photo_id': mls_photo_variant(url)[0], 'category': category, 'model': 'private provenance'}
            for url, category in ((PHOTO, 'exterior'), (second_photo, 'kitchen'), (floor_plan, 'floor_plan'))
        ]
        response = self.client.get(self.url)
        for text in (b'View all 2 photos by category', b'Exterior', b'Kitchen'):
            self.assertIn(text, response.data)
        self.assertNotIn(b'private provenance', response.data)
        self.assertNotIn(b'Floor plans', response.data)
        self.assertEqual(self.client.get(floor_plan_url).status_code, 404)
        self.assertEqual(self.client.get('/homes/property-type-count?photo_categories=yes').json, {'count': 1})
        self.listing['photos'] = [PHOTO[:-1] + '4']
        self.assertEqual(self.client.get('/homes/property-type-count?photo_categories=yes').json, {'count': 0})
