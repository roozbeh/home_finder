"""The private reviewer workflow persists immutable versions and evidence."""
import io
import json
import os
import tempfile
import unittest
from functools import wraps
from pathlib import Path
from unittest.mock import patch

from flask import Flask, abort, request, session

from buyer_analysis_fixtures import cost_items
from buyer_analysis_review import register_analysis_review


class ReviewerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'review.sqlite'
        app = Flask(__name__)
        app.secret_key = 'analysis-test-only'
        app.config['TESTING'] = True

        def require_admin(view):
            @wraps(view)
            def gated(*args, **kwargs):
                if session.get('role') != 'admin':
                    abort(403)
                return view(*args, **kwargs)
            return gated

        def csrf():
            if request.form.get('csrf_token') != 'fixture-token':
                abort(400)

        app.jinja_env.globals['csrf_token'] = lambda: 'fixture-token'
        app.jinja_env.filters['money'] = lambda value: f'${value:,}'
        app.jinja_env.filters['percent'] = lambda value: f'{value:%}'
        with patch.dict(os.environ, {'BUYER_REVIEW_DB': str(self.path), 'BUYER_MODE': 'preview'}):
            register_analysis_review(app, require_admin, csrf)
        self.client = app.test_client()

    def test_private_review_save_duplicate_export_and_attachment(self):
        listing = '/admin/renovation/BA326091746'
        self.assertEqual(self.client.get(listing).status_code, 403)
        with self.client.session_transaction() as state:
            state['role'] = 'demo'
        self.assertEqual(self.client.get(listing).status_code, 403)
        with self.client.session_transaction() as state:
            state['role'] = 'admin'
        self.assertIn(b'Dated Fairfield regression', self.client.get(listing).data)
        self.assertIn(b'47,575', self.client.get(listing).data)
        self.assertIn(b'100,575', self.client.get(listing + '?scope_0=full').data)
        self.assertEqual(self.client.post(listing, data={}).status_code, 400)
        fields = {'version': 'bid-1', 'items': json.dumps(cost_items()), 'csrf_token': 'fixture-token', 'partial': '1'}
        rejected = self.client.post(listing, data={**fields, 'evidence': (io.BytesIO(b'<html>'), 'invalid.html')})
        self.assertIn(b'Upload a PDF, JPEG or PNG', rejected.data)
        document = b'%PDF-1.0\nsynthetic private attachment\n'
        response = self.client.post(listing, data={**fields, 'evidence': (io.BytesIO(document), 'synthetic.pdf')})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(listing, data=fields)
        self.assertIn(b'This review version already exists', response.data)
        exported = self.client.get(listing + '/bid-1/export')
        self.assertEqual(exported.json['cost_version'], 'bid-1')
        self.assertNotIn(b'private attachment', exported.data)
        return_evidence = self.client.get(listing + '/bid-1/evidence')
        self.assertEqual(return_evidence.data, document)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertIn(b'Synthetic holdout', self.client.get('/admin/renovation/evaluation').data)


    def test_paid_outcomes_are_independent_and_export_only_verified_amounts(self):
        path = '/admin/renovation/outcomes/synthetic-pair'
        self.assertEqual(self.client.get(path).status_code, 403)
        with self.client.session_transaction() as state:
            state['role'] = 'admin'
        form = {'csrf_token': 'fixture-token', 'available_date': '2026-02-01', 'renovation': '45000',
                'hold': '0', 'provenance': 'Private invoice reference'}
        no_evidence = self.client.post(path, data=form)
        self.assertIn(b'Attach the evidence', no_evidence.data)
        document = (io.BytesIO(b'%PDF-1.0\nfixture'), 'invoice.pdf')
        post = self.client.post(path, data={**form, 'evidence': document}, follow_redirects=True)
        self.assertIn(b'Current verified costs', post.data)
        export = self.client.get(path + '/export').json
        self.assertEqual(export['verified_costs'], {'basis': 'paid_invoice', 'available_date': '2026-02-01',
                                                     'renovation': 45000, 'hold': 0})


if __name__ == '__main__':
    unittest.main()
