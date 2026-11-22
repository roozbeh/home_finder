"""Private workflow and published withdrawal regression."""
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, abort, request, session

from buyer_catalog import Catalog
from buyer_publish import publish
from buyer_supply import FIXTURE_SOURCE, PACIFIC, SupplyStore, overlay, register_supply
from public_buyer import register_public

TEMPLATES = Path(__file__).resolve().parents[1] / 'templates'


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('role') != 'admin':
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def validate_csrf():
    if request.form.get('csrf_token') != 'only-for-supply-test':
        abort(400)


class SupplyTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        self.catalog_path = directory / 'catalog.sqlite'
        self.store_path = directory / 'supply.sqlite'
        self.manifest_path = directory / 'manifest.json'
        self.base = {'mode': 'preview', 'sources': {}, 'homes': []}
        self.manifest_path.write_text(json.dumps(self.base))
        self.catalog = Catalog(self.catalog_path)
        self.store = SupplyStore(self.store_path)
        publish(self.base, self.catalog_path, directory)

        self.app = Flask(__name__, template_folder=str(TEMPLATES))
        self.app.secret_key = 'only-for-supply-session-test'
        self.app.config['TESTING'] = True
        self.app.jinja_env.filters['money'] = str
        self.app.jinja_env.globals['csrf_token'] = lambda: 'only-for-supply-test'
        config = {
            'BUYER_SUPPLY_DB': str(self.store_path), 'BUYER_SUPPLY_MANIFEST': str(self.manifest_path),
            'BUYER_CATALOG': str(self.catalog_path), 'BUYER_SUPPLY_ASSETS': str(directory),
            'BUYER_MODE': 'preview', 'BUYER_SUBMISSIONS': str(directory / 'submissions.sqlite'),
        }
        with patch.dict('os.environ', config):
            register_public(self.app, admin_required, validate_csrf)
            register_supply(self.app, admin_required, validate_csrf)
        self.client = self.app.test_client()

    def admin(self):
        with self.client.session_transaction() as session:
            session['role'] = 'admin'

    @staticmethod
    def opportunity(**extra):
        start = datetime.now(timezone.utc).astimezone(PACIFIC) + timedelta(hours=1)
        end = start + timedelta(minutes=90)
        format_local = lambda value: value.strftime('%Y-%m-%dT%H:%M')
        return {
            'csrf_token': 'only-for-supply-test', 'city': 'Daly City, CA', 'price': '650000',
            'property_type': 'Single family', 'advertising_basis': 'Consent fixture DOC-902',
            'access_contact': 'Private Access Coordinator', 'access_instructions': 'PRIVATE-LATCH-989',
            'assignment_terms': 'PRIVATE-TERMS-562', 'verified': 'yes',
            'start_1': format_local(start), 'end_1': format_local(end), **extra,
        }

    def test_intake_verification_access_update_and_withdrawal(self):
        self.assertEqual(self.client.get('/admin/supply').status_code, 403)
        with self.client.session_transaction() as state:
            state['role'] = 'demo'
        self.assertEqual(self.client.get('/admin/supply').status_code, 403)
        self.admin()
        self.assertEqual(self.client.get('/admin/supply').status_code, 200)
        self.assertEqual(self.client.get('/admin/supply/new').status_code, 200)
        self.assertEqual(self.client.post('/admin/supply/new', data=self.opportunity(csrf_token='wrong')).status_code, 400)

        missing_rights = self.client.post('/admin/supply/new', data=self.opportunity(advertising_basis=''))
        self.assertEqual(missing_rights.status_code, 422)
        self.assertIn(b'Record who authorized advertising', missing_rights.data)
        self.assertFalse(self.store.all())

        draft = self.client.post('/admin/supply/new', data=self.opportunity(verified=''))
        self.assertEqual(draft.status_code, 303)
        home_id = draft.location.rsplit('/', 1)[1]
        self.assertIsNone(self.catalog.get('preview', home_id))

        activated = self.client.post(draft.location, data=self.opportunity())
        self.assertEqual(activated.status_code, 303)
        public = self.catalog.get('preview', home_id)
        self.assertEqual(public['price'], 650000)
        self.assertEqual(public['credit']['source'], 'Sample Bay Area Supply')
        self.assertIn('updated', public)
        self.assertEqual(self.catalog.search('preview', {}, 1)[3], 1)
        self.assertIsNone(self.catalog.get('approved', home_id))
        serialized = json.dumps(public)
        for private in ('PRIVATE-LATCH-989', 'PRIVATE-TERMS-562', 'Private Access Coordinator', 'Consent fixture DOC-902'):
            self.assertNotIn(private, serialized)

        buyer = self.app.test_client()
        detail_url = f'/homes/{home_id}'
        tour_url = f'{detail_url}/request-tour'
        self.assertEqual(buyer.get(detail_url).status_code, 200)
        tour_form = buyer.get(tour_url)
        self.assertEqual(tour_form.status_code, 200)
        self.assertEqual(tour_form.headers['Cache-Control'], 'private, no-store, max-age=0')
        token = re.search(rb'name="submission_id" value="([^"]+)"', tour_form.data)[1].decode()
        chosen_time = self.opportunity()['start_1']
        tour_data = {
            'csrf_token': 'only-for-supply-test', 'submission_id': token,
            'name': 'Test Buyer', 'channel': 'email', 'contact': 'buyer@example.test',
            'preferred_time': chosen_time, 'representation': 'unsure', 'window_minutes': '60',
        }
        receipt = buyer.post(tour_url, data=tour_data)
        self.assertEqual(receipt.status_code, 201)
        self.assertIn(b'Tour requested', receipt.data)
        self.assertIn(b'Test Buyer', self.client.get('/admin/leads').data)

        updated = self.client.post(draft.location, data=self.opportunity(access_instructions='PRIVATE-NEW-154'))
        self.assertEqual(updated.status_code, 303)
        private = self.client.get(draft.location)
        self.assertIn(b'PRIVATE-NEW-154', private.data)
        self.assertIn(b'PRIVATE-LATCH-989', private.data)
        self.assertNotIn('PRIVATE-NEW-154', json.dumps(self.catalog.get('preview', home_id)))

        withdrawn = self.client.post(draft.location, data={
            'csrf_token': 'only-for-supply-test', 'action': 'withdraw',
        })
        self.assertEqual(withdrawn.status_code, 303)
        self.assertEqual(self.catalog.search('preview', {}, 1)[3], 0)
        self.assertIsNone(self.catalog.get('preview', home_id))
        self.assertEqual(buyer.get(detail_url).status_code, 404)
        self.assertEqual(buyer.post(tour_url, data=tour_data).status_code, 404)
        self.assertIn(b'Withdrawn', self.client.get(draft.location).data)
        self.assertEqual(self.client.post(draft.location, data=self.opportunity()).status_code, 409)

    def test_withdrawal_removes_old_catalog_record_when_rebuild_fails(self):
        self.admin()
        opened = self.client.post('/admin/supply/new', data=self.opportunity())
        home_id = opened.location.rsplit('/', 1)[1]
        self.assertIsNotNone(self.catalog.get('preview', home_id))
        self.manifest_path.unlink()
        with self.assertLogs(self.app.logger, 'ERROR'):
            response = self.client.post(opened.location, data={
                'csrf_token': 'only-for-supply-test', 'action': 'withdraw',
            })
        self.assertEqual(response.status_code, 503)
        self.assertIsNotNone(self.store.get(home_id)['withdrawn_at'])
        self.assertIsNone(self.catalog.get('preview', home_id))

    def test_approved_mode_rejects_fixture_source(self):
        self.manifest_path.write_text(json.dumps({**self.base, 'mode': 'approved'}))
        with self.assertRaises(ValueError):
            overlay(self.manifest_path, self.store, FIXTURE_SOURCE, 'Sample Bay Area Supply')


if __name__ == '__main__':
    unittest.main()
