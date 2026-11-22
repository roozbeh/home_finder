import re
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from unittest.mock import patch

from flask import Flask, abort, request, session

from buyer_fixtures import FAIRFIELD, LIMITED, PENDING, PRIVATE, SUPPRESSED, fixture_manifest
from buyer_media import validated_jpeg
from buyer_publish import publish
from public_buyer import register_public

ASSETS = Path(__file__).with_name('fixtures')


class BuyerJourneyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'catalog.sqlite'
        self.manifest = fixture_manifest(ASSETS)
        publish(self.manifest, self.path, ASSETS)
        self.app = Flask('public_buyer')
        self.app.secret_key = 'fixture-test-secret'
        self.app.add_url_rule('/admin/login', 'login', lambda: ('Sign in', 401))
        self.app.testing = True

        def admin_required(view):
            @wraps(view)
            def guarded(*args, **kwargs):
                if session.get('flip_ui_role') != 'admin':
                    abort(403)
                return view(*args, **kwargs)
            return guarded

        def validate():
            if request.form.get('csrf_token') != 'fixture-csrf':
                abort(400)

        self.app.jinja_env.globals['csrf_token'] = lambda: 'fixture-csrf'
        with patch.dict('os.environ', {'BUYER_MODE': 'preview', 'BUYER_CATALOG': str(self.path),
                                       'BUYER_SUBMISSIONS': str(Path(temporary.name) / 'inbox.sqlite')}):
            register_public(self.app, admin_required, validate)
        self.client = self.app.test_client()

    def get_token(self, path):
        body = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="submission_id" value="([^"]+)"', body)[1]

    def set_role(self, role):
        with self.client.session_transaction() as m:
            m['flip_ui_role'] = role
            if role == 'admin':
                m['flip_ui_admin'] = True

    def test_media_metadata_is_refused(self):
        original = (ASSETS / 'ranch.jpg').read_bytes()
        self.assertEqual(validated_jpeg(original), original)
        payload = PRIVATE.encode()
        length = (len(payload) + 2).to_bytes(2, 'big')
        for marker in (b'\xff\xfe', b'\xff\xe1', b'\xff\xe2'):
            with self.assertRaises(ValueError):
                validated_jpeg(original[:2] + marker + length + payload + original[2:])

    def test_approved_mode_requires_source_activation(self):
        from buyer_catalog import Catalog

        self.manifest['mode'] = 'approved'
        publish(self.manifest, self.path, ASSETS)
        self.assertIsNone(Catalog(self.path).get('approved', FAIRFIELD))
        self.manifest['sources']['fixture']['production_approved'] = True
        publish(self.manifest, self.path, ASSETS)
        self.assertIsNotNone(Catalog(self.path).get('approved', FAIRFIELD))

    def test_mls_listing_record_respects_credit_address_and_update_permissions(self):
        url = f'/homes/{FAIRFIELD}'
        home = self.manifest['homes'][0]
        source = self.manifest['sources']['fixture']['allow']
        listing = home['policy']['allow']
        home['credit'] = {'source': 'MLS credit', 'mls_id': 'ML123456', 'agent': '<b>Listing Specialist</b>',
                          'agent_phone': '(650) 555-0100', 'office': 'Listing Brokerage',
                          'office_phone': '650.555.0199', 'agent_email': PRIVATE}
        home['days_on_market'] = 27
        home['listing_updated'] = '2026-11-07'
        home['details_checked'] = '2026-11-22T09:15:54-08:00'
        for field in ('credits', 'updated', 'days_on_market'):
            source[field] = listing[field] = True
        publish(self.manifest, self.path, ASSETS)
        body = self.client.get(url).data
        for value in (b'ML123456', b'2026-11-07', b'2026-11-22 at 17:15 UTC', b'27 days on market',
                      b'Listing Specialist', b'Listing Brokerage', b'tel:+16505550100', b'tel:+16505550199'):
            self.assertIn(value, body)
        self.assertNotIn(PRIVATE.encode(), body)

        for permissions, field, hidden, visible in (
            (listing, 'address', b'ML123456', b'MLS credit'),
            (listing, 'address', b'Listing Specialist', b'MLS credit'),
            (source, 'credits', b'ML123456', b'Listing updated by MLS'),
            (source, 'credits', b'tel:+16505550100', b'Listing updated by MLS'),
            (source, 'days_on_market', b'days on market', b'Listed by'),
            (listing, 'days_on_market', b'days on market', b'Listed by'),
            (listing, 'updated', b'2026-11-07', b'ML123456'),
        ):
            with self.subTest(field=field):
                permissions[field] = False
                publish(self.manifest, self.path, ASSETS)
                body = self.client.get(url).data
                self.assertNotIn(hidden, body)
                self.assertIn(visible, body)
                permissions[field] = True

    def test_live_listing_update_uses_provider_calendar_date(self):
        from buyer_live_feed import listing_update_date

        self.assertEqual(listing_update_date('11/07/2026 04:31 PM'), '2026-11-07')
        self.assertIsNone(listing_update_date('unknown'))

    def test_live_listing_baths_follow_mls_display_formats(self):
        from buyer_live_feed import listing_baths

        self.assertEqual(listing_baths({}, {'Baths': '2'}), 2)
        self.assertEqual(listing_baths({'BATHROOMS_FULL': 2}, {'Baths': '2/1'}), 2.5)
        self.assertEqual(listing_baths({}, {'BATHROOMS_FULL': '1', 'BATHROOMS_HALF': '1'}), 1.5)
        self.assertIsNone(listing_baths({}, {'Baths': '--'}))

    def test_live_assessment_import_requires_review_of_exact_revision(self):
        from buyer_live_feed import manifest_for

        now = datetime(2026, 11, 22, tzinfo=timezone.utc)
        observed = now - timedelta(hours=1)
        fields = ['internet', 'address', 'derived', 'valuation', 'sold_comps', 'ai']
        config = {'feed': {'production_approved': True, 'listing_refresh_hours': 24,
                           'internet_display_values': {'Full': fields}}}
        row = {'SOURCE_MLS': 'feed', 'LISTING_ID': 'M1', 'RAW_STATUS': 'ACTV',
               '_updated_at': observed, 'details_fetched': observed,
               'details': {'INTERNET_DISPLAY': 'Full', 'INTERNET_SITES': 'Yes - All Sites'}}
        assessment = {'created_at': observed, 'valuation_status': 'AI_estimate_requires_review',
                      'resale_midpoint': 950_000, 'photo_urls': [PRIVATE]}
        evidence = {'purchase_assessment_created_at': observed.isoformat(),
                    'purchase_assessment_refresh_by': (now + timedelta(hours=2)).isoformat(),
                    'allow': dict.fromkeys(fields, True)}
        def resulting_row():
            return manifest_for([row], config, b'test-key', reviewed={'feed:M1': evidence},
                                saved_assessments={'M1': assessment}, now=now)['homes'][0]

        carried = resulting_row()['reviewed_purchase_assessment']
        self.assertEqual(carried['resale_midpoint'], 950_000)
        self.assertNotIn('photo_urls', carried)
        evidence['allow']['sold_comps'] = False
        self.assertNotIn('reviewed_purchase_assessment', resulting_row())
        evidence['allow']['sold_comps'] = True
        assessment['created_at'] += timedelta(seconds=1)
        self.assertNotIn('reviewed_purchase_assessment', resulting_row())

    def test_public_surfaces_and_filters(self):
        for path in ('/', '/homes', f'/homes/{FAIRFIELD}', f'/homes/{PENDING}', f'/homes/{SUPPRESSED}',
                     f'/homes/{LIMITED}', f'/homes/{FAIRFIELD}/contact'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            for item in (PRIVATE, 'PRIVATE_REMARKS', 'SHOWING_INSTRUCTIONS', 'UNEXPECTED_MLS_COLUMN',
                         '37.77777', '-122.22222', 'private_email', 'preview-closed-1'):
                self.assertNotIn(item, body)
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn(b'Renovation estimate unavailable', self.client.get('/homes').data)
        self.assertIn(b'Comparable sales', self.client.get(f'/homes/{FAIRFIELD}').data)
        self.assertNotIn(f'/homes/{LIMITED}/media/'.encode(), self.client.get(f'/homes/{LIMITED}').data)
        result = self.client.get('/homes?dom=60&reduced=1')
        self.assertIn(FAIRFIELD.encode(), result.data)
        self.assertNotIn(PENDING.encode(), result.data)
        self.assertIn(b'reduced=1', self.client.get(f'/homes/{FAIRFIELD}?dom=60&reduced=1').data)
        self.assertEqual(self.client.get('/homes?min_price=100&max_price=20').status_code, 400)
        self.assertIn(LIMITED.encode(), self.client.get('/homes?auction=1').data)
        self.assertNotIn(FAIRFIELD.encode(), self.client.get('/homes?auction=1').data)

    def test_property_type_tiles_search_map_count_and_old_url(self):
        single, pending, suppressed, limited = self.manifest['homes'][:4]
        single['property_type'] = 'Detached'
        pending['property_type'] = 'Condo'
        suppressed['property_type'] = 'Duplex'
        limited['property_type'] = '109'
        limited['facts'] = {'Property and parking': {'Property subtype': '109'}}
        rental = dict(pending, id='fixture-rental-condo', market='rent')
        self.manifest['homes'].append(rental)
        publish(self.manifest, self.path, ASSETS)
        filters = 'property_type=houses&property_type=condos-co-ops'
        render = self.client.get('/homes?' + filters)
        self.assertEqual(render.status_code, 200)
        for text in (b'Condos/Co-ops', b'See 2 results', FAIRFIELD.encode(), PENDING.encode()):
            self.assertIn(text, render.data)
        self.assertNotIn(SUPPRESSED.encode(), render.data)
        self.assertNotIn(LIMITED.encode(), render.data)
        points = self.client.get('/homes/map-data?' + filters).json
        self.assertEqual({point['url'].split('?')[0] for point in points}, {f'/homes/{FAIRFIELD}', f'/homes/{PENDING}'})
        self.assertTrue(all(filters in point['url'] for point in points))
        self.assertEqual(self.client.get('/homes/property-type-count?' + filters).json, {'count': 2})
        self.assertEqual(self.client.get('/homes/property-type-count?' + filters + '&market=rent').json, {'count': 1})
        self.assertEqual(self.client.get('/homes/property-type-count?' + filters + '&city=Fairfield').json, {'count': 1})
        self.assertIn(PENDING.encode(), self.client.get('/homes?property_type=Condo').data)
        self.assertIn(LIMITED.encode(), self.client.get('/homes?' + filters + '&all_property_types=1').data)
        detail = self.client.get(f'/homes/{FAIRFIELD}?' + filters).data
        self.assertIn(b'/homes?property_type=houses&amp;property_type=condos-co-ops', detail)
        self.assertNotIn(b'>109<', self.client.get(f'/homes/{LIMITED}').data)

        # Before publisher rollout, the web build must also understand existing snapshots.
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE homes SET payload=json_remove(payload, '$.property_type_group')")
        self.assertEqual(self.client.get('/homes/property-type-count?' + filters).json, {'count': 2})

    def test_property_type_permission_also_controls_group_and_subtype(self):
        home = self.manifest['homes'][0]
        home['property_type'] = 'Condo'
        home['facts'] = {'Property and parking': {'Property subtype': 'Condo'}}
        home['policy'] = deepcopy(home['policy'])
        home['policy']['allow']['property_type'] = False
        publish(self.manifest, self.path, ASSETS)
        payload = self.app.extensions['buyer_catalog'].get('preview', FAIRFIELD)
        self.assertNotIn('property_type', payload)
        self.assertNotIn('property_type_group', payload)
        self.assertNotIn('Property subtype', payload.get('facts', {}).get('Property and parking', {}))
        self.assertIn(FAIRFIELD.encode(), self.client.get('/homes').data)
        self.assertEqual(self.client.get('/homes/property-type-count?property_type=condos-co-ops').json, {'count': 0})

    def test_map_preview_uses_permitted_facts_media_and_rechecks_filters(self):
        listing = f'/homes/{FAIRFIELD}'
        row = self.manifest['homes'][0]
        row['locality'] = 'Fairfield, CA 94533'
        publish(self.manifest, self.path, ASSETS)
        bulk = self.client.get('/homes/map-data?city=Fairfield').json
        self.assertEqual(len(bulk), 1)
        self.assertEqual(set(bulk[0]), {'point', 'label', 'price', 'url'})
        response = self.client.get(f'{listing}/map-preview?city=Fairfield&property_type=houses&page=2')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        card = response.json
        self.assertEqual(card['price'], 460_000)
        self.assertEqual(card['locality'], 'Fairfield, CA 94533')
        self.assertEqual(card['facts']['beds'], 3)
        self.assertEqual(card['facts']['days_on_market'], 65)
        self.assertIn('property_type=houses&page=2', card['url'])
        self.assertTrue(card['photo'].startswith(listing + '/media/'))
        self.assertEqual(self.client.get(card['photo']).status_code, 200)
        for private in (PRIVATE, 'coordinates', 'resale', 'credit', 'remark'):
            self.assertNotIn(private, response.get_data(as_text=True))
        self.assertEqual(self.client.get(f'{listing}/map-preview?city=Oakland').status_code, 404)
        self.assertEqual(self.client.get(f'/homes/{SUPPRESSED}/map-preview').status_code, 404)
        self.assertIsNone(self.client.get(f'/homes/{LIMITED}/map-preview').json['photo'])

        row['policy'] = deepcopy(row['policy'])
        row['policy']['allow']['sqft'] = False
        row['policy']['allow']['days_on_market'] = False
        row['policy']['allow']['media'] = False
        publish(self.manifest, self.path, ASSETS)
        limited = self.client.get(f'{listing}/map-preview').json
        self.assertNotIn('sqft', limited['facts'])
        self.assertNotIn('days_on_market', limited['facts'])
        self.assertIsNone(limited['photo'])
        self.assertEqual(self.client.get(card['photo']).status_code, 404)
        row['policy']['allow']['geolocation'] = False
        publish(self.manifest, self.path, ASSETS)
        self.assertEqual(self.client.get(f'{listing}/map-preview').status_code, 404)
        row['policy']['allow']['geolocation'] = True
        row['policy']['allow']['address'] = False
        publish(self.manifest, self.path, ASSETS)
        self.assertEqual(self.client.get(f'{listing}/map-preview').status_code, 404)
        detail = self.client.get(listing)
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(b'94533', detail.data)

    def test_flipfinder_filters_cover_map_counts_and_publication_freshness(self):
        fairfield, pending, _, limited = self.manifest['homes']
        fairfield['media'][0]['category'] = 'exterior'
        for row, resale in ((pending, 1_300_000), (limited, 580_000)):
            row.update(analysis_origin='model', purchase_assessment_refresh_by='2098-01-01T00:00:00+00:00',
                       reviewed_purchase_assessment={'resale_low': resale - 10_000, 'resale_midpoint': resale,
                                                     'resale_high': resale + 10_000, 'renovation_low': 35_000,
                                                     'renovation_high': 55_000, 'hoa_monthly': 0})
        publish(self.manifest, self.path, ASSETS)
        cases = (
            ('photo_categories=yes', {FAIRFIELD}, 1),
            ('photo_categories=no&purchase_recommendation=yes', {PENDING, LIMITED}, 2),
            ('purchase_recommendation=no', set(), 1),  # The hidden-address home has no map pin.
            ('good_investment=yes', {PENDING}, 1),
            ('good_investment=no', {LIMITED}, 1),
        )
        for query, ids, count in cases:
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/homes/property-type-count?' + query).json, {'count': count})
                points = self.client.get('/homes/map-data?' + query).json
                self.assertEqual({point['url'].split('?')[0].rsplit('/', 1)[1] for point in points}, ids)
                self.assertTrue(all(query in point['url'] for point in points))
        self.assertIn(b'FlipFinder filters', self.client.get('/homes').data)
        self.assertEqual(self.client.get('/homes?good_investment=unknown').status_code, 400)
        self.assertIn(b'Recommended purchase price', self.client.get(f'/homes/{PENDING}').data)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE homes SET payload=json_set(payload, '$.recommendation_expires', ?) WHERE id=?",
                       ('2000-01-01T00:00:00+00:00', PENDING))
        self.assertEqual(self.client.get('/homes/property-type-count?good_investment=yes').json, {'count': 0})
        self.assertNotIn(b'Recommended purchase price', self.client.get(f'/homes/{PENDING}').data)

    def test_withdraw_after_warming_search_detail_media_and_form(self):
        path = f'/homes/{FAIRFIELD}'
        warm = self.client.get(path)
        self.assertIn(b'$545,000', warm.data)
        self.assertNotIn('ETag', warm.headers)
        asset = self.app.extensions['buyer_catalog'].get('preview', FAIRFIELD)['photos'][0]
        media = self.client.get(f'{path}/media/{asset}')
        self.assertEqual(media.status_code, 200)
        self.assertIn('no-store', media.headers['Cache-Control'])
        self.assertIn(FAIRFIELD.encode(), self.client.get('/homes').data)
        data = {'csrf_token': 'fixture-csrf', 'submission_id': self.get_token(f'{path}/contact'),
                'name': 'Test Buyer', 'channel': 'email', 'contact': 'buyer@example.test'}
        self.manifest['homes'][0]['withdrawn'] = True
        publish(self.manifest, self.path, ASSETS)
        for suffix in ('', '/contact', '/map-preview', f'/media/{asset}'):
            response = self.client.get(path + suffix, headers={'If-None-Match': '*'})
            self.assertEqual(response.status_code, 404)
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotIn(FAIRFIELD.encode(), self.client.get('/homes').data)
        self.assertEqual(self.client.post(f'{path}/contact', data=data).status_code, 404)

    def test_source_and_listing_grants_remove_dependent_output(self):
        path = f'/homes/{FAIRFIELD}'
        first = self.client.get(path)
        self.assertIn(b'$545,000', first.data)
        comp = self.manifest['sales'][0]
        comp['policy'] = deepcopy(comp['policy'])
        comp['policy']['allow']['sold_comps'] = False
        publish(self.manifest, self.path, ASSETS)
        refreshed = self.client.get(path)
        for forbidden in (b'$545,000', b'$530,000', b'$47,575'):
            self.assertNotIn(forbidden, refreshed.data)
        self.manifest['sources']['fixture']['allow']['valuation'] = False
        publish(self.manifest, self.path, ASSETS)
        refreshed = self.client.get(path)
        self.assertNotIn(b'$150,000', refreshed.data)
        self.assertIn(b'$460,000', refreshed.data)
        self.assertIn(b'Renovation scope', refreshed.data)

    def test_comp_policy_expires_without_unpublishing_home(self):
        now = datetime.now(timezone.utc)
        for sale in self.manifest['sales']:
            sale['policy'] = {**sale['policy'], 'refresh_by': (now + timedelta(seconds=60)).isoformat()}
        publish(self.manifest, self.path, ASSETS)
        self.assertIn(b'$545,000', self.client.get(f'/homes/{FAIRFIELD}').data)
        with patch('buyer_catalog.utcnow', return_value=(now + timedelta(seconds=90)).isoformat()):
            response = self.client.get(f'/homes/{FAIRFIELD}')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'$545,000', response.data)

    def test_forms_idempotency_validation_and_private_roles(self):
        contact = f'/homes/{FAIRFIELD}/contact'
        token = self.get_token(contact)
        data = {'csrf_token': 'fixture-csrf', 'submission_id': token, 'name': 'Test buyer',
                'channel': 'email', 'contact': 'bad email', 'question': '<script>test</script>'}
        response = self.client.post(contact, data=data)
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'Enter a valid email address', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        data['contact'] = 'buyer@example.test'
        self.assertEqual(self.client.post(contact, data={**data, 'csrf_token': ''}).status_code, 400)
        self.assertEqual(self.client.post(contact, data=data).status_code, 201)
        self.assertEqual(self.client.post(contact, data=data).status_code, 201)
        self.assertEqual(self.client.post(f'/homes/{PENDING}/contact', data=data).status_code, 400)
        self.assertNotIn(b'buyer@example.test', self.client.post(contact, data=data).data)
        self.assertEqual(self.client.get('/admin/leads').status_code, 403)
        self.set_role('demo')
        self.assertEqual(self.client.get('/admin/leads').status_code, 403)
        self.assertEqual(self.client.get('/admin/partners').status_code, 403)
        self.assertEqual(self.client.post(f'/admin/submissions/{token.split(".")[0]}', data={'status':'closed'}).status_code, 403)
        self.set_role('admin')
        self.assertEqual(self.client.get('/admin/leads').data.count(b'Test buyer'), 1)
        self.assertEqual(self.client.post(contact, data={**data, 'submission_id': self.get_token(contact)}).status_code, 201)
        self.assertEqual(self.client.get('/admin/leads').data.count(b'Test buyer'), 2)

    def test_tour_partner_and_outbox_retry(self):
        tour = f'/homes/{PENDING}/request-tour'
        partner = '/agents/partner'
        data = {'csrf_token': 'fixture-csrf', 'name': 'Preview visitor', 'channel': 'phone',
                'contact': '(415) 555-0101'}
        tour_token = self.get_token(tour)
        self.assertEqual(self.client.post(tour, data={**data, 'submission_id': tour_token,
                         'preferred_time': '2099-01-02T10:00', 'alternative_time': '2099-01-03T11:00',
                         'window_minutes': '60', 'representation': 'unsure'}).status_code, 201)
        self.assertEqual(self.client.post(partner, data={**data, 'submission_id': self.get_token(partner),
                         'license': '01234567', 'brokerage': 'Sample brokerage', 'markets': 'East Bay',
                         'experience': 'Fixture projects', 'availability': 'Weekends'}).status_code, 201)
        self.set_role('admin')
        self.assertIn(b'Preview visitor', self.client.get('/admin/partners').data)
        path = '/admin/submissions/' + tour_token.split('.')[0]
        self.assertIn(b'2099-01-03T11:00', self.client.get(path).data)
        self.assertEqual(self.client.post(path, data={'csrf_token': 'fixture-csrf', 'status': 'tour confirmed'}).status_code, 409)
        self.assertEqual(self.client.post(path, data={'csrf_token': 'fixture-csrf', 'status': 'tour confirmed',
                                                     'access_verified': 'yes'}).status_code, 303)
        inbox = self.app.extensions['buyer_inbox']

        def fail(_):
            raise OSError('fixture transport failure')

        self.assertEqual(inbox.deliver(fail), 0)
        delivered = []
        self.assertEqual(inbox.deliver(delivered.append), 2)
        self.assertEqual(inbox.deliver(delivered.append), 0)
        self.assertEqual({row['kind'] for row in delivered}, {'partner', 'request-tour'})

    def test_public_detail_uses_proxy_thumbnails_inline_credit_and_buyer_actions(self):
        home = self.manifest['homes'][0]
        home['year_built'] = 1983
        home['credit'] = {**home['credit'], 'mls_id': 'SAMPLE1983'}
        home['media'] = [
            {**photo, 'category': 'kitchen' if index else 'exterior'}
            for index, photo in enumerate(home['media'])
        ]
        publish(self.manifest, self.path, ASSETS)
        path = f'/homes/{FAIRFIELD}'
        body = self.client.get(path).get_data(as_text=True)
        self.assertIn('Built in 1983', body)
        self.assertIn('class="property-credit"', body)
        self.assertIn('MLS #SAMPLE1983', body)
        self.assertNotIn('MLS listing record', body)
        self.assertIn('View all 2 photos by category', body)
        self.assertIn('class="property-thumbnail-strip"', body)
        self.assertIn('id="property-photo-modal"', body)
        self.assertIn(f'href="{path}/request-tour">Request a Tour</a>', body)
        self.assertIn(f'href="{path}/contact">Contact Agent</a>', body)
        self.assertNotIn('sanitized_file', body)
        thumb_urls = set(re.findall(r'href="(' + re.escape(path) + r'/media/[0-9a-f]+)"', body))
        self.assertEqual(len(thumb_urls), 2)
        self.assertTrue(all(self.client.get(url).status_code == 200 for url in thumb_urls))
        limited = self.client.get(f'/homes/{LIMITED}').get_data(as_text=True)
        self.assertIn('Photo unavailable', limited)
        self.assertNotIn('id="property-photo-modal"', limited)


if __name__ == '__main__':
    unittest.main()
