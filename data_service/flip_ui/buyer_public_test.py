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
        self.assertNotIn(b'Property photo', self.client.get(f'/homes/{LIMITED}').data)
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
        for suffix in ('', '/contact', f'/media/{asset}'):
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


if __name__ == '__main__':
    unittest.main()
