"""Private cost-basis review and versioned evidence storage."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from flask import Blueprint, Response, abort, redirect, render_template, request, url_for

from buyer_analysis import detail_analysis, exact_date, scopes, short_text
from buyer_analysis_fixtures import cost_items
from buyer_catalog import utcnow
from buyer_evaluate import demo_dataset, evaluate

LEGACY_CASE = 'BA326091746'
DEMO_VERSION = 'november-21-regression-1'
DEMO_CASE = {
    'cost_version': DEMO_VERSION, 'cost_items': cost_items(), 'partial_budget': True,
    'price': 460000, 'resale': 545000,
}
ALLOWED_ITEM_FIELDS = {
    'room', 'task', 'evidence_type', 'quantity', 'unit', 'unit_low', 'unit_high', 'source_type',
    'source', 'source_date', 'geography', 'confidence', 'evidence_photo_ids', 'included',
    'alternative_group', 'alternative', 'alternative_label',
}
MAX_ATTACHMENT = 4_000_000
OUTCOME_FIELDS = {
    'renovation': 'Paid renovation invoices', 'financing': 'Financing',
    'hold': 'Taxes, insurance and other hold costs', 'purchase_fees': 'Purchase settlement costs',
    'resale_fees': 'Resale settlement costs',
}


class CostReviews:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        db.executescript("""CREATE TABLE IF NOT EXISTS cost_reviews(
            listing TEXT NOT NULL, version TEXT NOT NULL, saved_at TEXT NOT NULL,
            reviewer TEXT NOT NULL, payload TEXT NOT NULL, evidence BLOB, digest TEXT,
            PRIMARY KEY(listing, version));
            CREATE TABLE IF NOT EXISTS historical_costs(
                pair_key TEXT NOT NULL, saved_at TEXT NOT NULL, reviewer TEXT NOT NULL,
                values_json TEXT NOT NULL, evidence BLOB NOT NULL, PRIMARY KEY(pair_key, saved_at));
        """)
        self.path.chmod(0o600)
        return closing(db)

    def versions(self, listing):
        with self.connect() as db:
            return db.execute("""SELECT version, saved_at, reviewer, digest FROM cost_reviews
                                 WHERE listing=? ORDER BY saved_at DESC""", (listing,)).fetchall()

    def scope(self, listing, version=None):
        with self.connect() as db:
            if version:
                row = db.execute('SELECT payload FROM cost_reviews WHERE listing=? AND version=?',
                                 (listing, version)).fetchone()
            else:
                row = db.execute('SELECT payload FROM cost_reviews WHERE listing=? ORDER BY saved_at DESC LIMIT 1',
                                 (listing,)).fetchone()
        if row:
            return json.loads(row['payload'])
        return None

    def evidence(self, listing, version):
        with self.connect() as db:
            row = db.execute('SELECT evidence FROM cost_reviews WHERE listing=? AND version=?',
                             (listing, version)).fetchone()
        return row['evidence'] if row else None

    def save(self, listing, reviewer, payload, evidence):
        scopes(payload['cost_items'], payload['cost_version'], payload['partial_budget'])
        with self.connect() as db, db:
            db.execute('INSERT INTO cost_reviews VALUES (?,?,?,?,?,?,?)', (
                listing, payload['cost_version'], utcnow(), reviewer, json.dumps(payload), evidence,
                hashlib.sha256(evidence).hexdigest() if evidence else None,
            ))

    def save_outcome(self, pair_key, reviewer, values, evidence):
        with self.connect() as db, db:
            db.execute('INSERT INTO historical_costs VALUES (?,?,?,?,?)', (
                pair_key, utcnow(), reviewer, json.dumps(values), evidence,
            ))

    def outcome(self, pair_key):
        with self.connect() as db:
            row = db.execute("""SELECT values_json FROM historical_costs WHERE pair_key=?
                                ORDER BY saved_at DESC LIMIT 1""", (pair_key,)).fetchone()
        return json.loads(row[0]) if row else {}


def validate_document_length():
    if request.content_length is None:
        abort(400)
    if request.content_length > MAX_ATTACHMENT + 200_000:
        abort(413)


def verified_costs():
    available = exact_date(request.form.get('available_date')).isoformat()
    if available > utcnow()[:10]:
        raise ValueError('Enter when the evidence became available.')
    values = {'basis': 'paid_invoice', 'available_date': available,
              'provenance': short_text(request.form.get('provenance'), 'provenance')}
    for key, label in OUTCOME_FIELDS.items():
        amount = request.form.get(key, '').strip()
        if amount:
            if not re.fullmatch(r'\d{1,9}', amount):
                raise ValueError(f'Enter a nonnegative whole-dollar figure for {label}.')
            values[key] = int(amount)
    if 'renovation' not in values:
        raise ValueError('Enter the paid renovation cost.')
    return values


def structured_input():
    raw = request.form.get('items', '')
    if len(raw) > 140_000:
        raise ValueError('Use at most 140 KB of line-item data')
    items = json.loads(raw)
    if not isinstance(items, list) or any(not isinstance(item, dict) or item.keys() - ALLOWED_ITEM_FIELDS for item in items):
        raise ValueError('Use only the listed line-item fields')
    version = request.form.get('version', '').strip()
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,40}', version) or version == DEMO_VERSION:
        raise ValueError('Enter a unique version using letters, digits, dots, underscores or hyphens')
    return dict(cost_items=items, cost_version=version, partial_budget=request.form.get('partial') == '1')


def attachment():
    upload = request.files.get('evidence')
    if not upload or not upload.filename:
        return None
    data = upload.stream.read(MAX_ATTACHMENT + 1)
    if len(data) > MAX_ATTACHMENT or not data.startswith((b'%PDF-', b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n')):
        raise ValueError('Upload a PDF, JPEG or PNG up to 4 MB')
    return data


def register_analysis_review(app, admin_required, validate_csrf):
    bp = Blueprint('analysis_review', __name__)
    default_db = Path(os.environ.get('BUYER_SUBMISSIONS', '/tmp/flip-buyer/submissions.sqlite')).with_name('review.sqlite')
    store = CostReviews(os.environ.get('BUYER_REVIEW_DB', str(default_db)))
    preview = os.environ.get('BUYER_MODE') == 'preview'

    @bp.get('/admin/renovation')
    @admin_required
    def index():
        listing_id = request.args.get('listing_id', LEGACY_CASE if preview else '')
        if listing_id:
            return redirect(url_for('analysis_review.review', listing_id=listing_id))
        return render_template('buyer_analysis_review.html', listing_id=None)

    @bp.route('/admin/renovation/<listing_id>', methods=['GET', 'POST'])
    @admin_required
    def review(listing_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', listing_id):
            abort(404)
        latest = store.scope(listing_id)
        fixture = preview and listing_id == LEGACY_CASE and not latest
        shown = latest or (DEMO_CASE if fixture else None)
        error = None
        items_text = json.dumps(shown['cost_items'], indent=2) if shown else '[]'
        if request.method == 'POST':
            validate_document_length()
            validate_csrf()
            items_text = request.form.get('items', '')
            try:
                data = structured_input()
                store.save(listing_id, os.environ.get('FLIP_UI_ADMIN_USERNAME', 'admin'), data, attachment())
            except (ValueError, TypeError) as exc:
                error = str(exc)
            except sqlite3.IntegrityError:
                error = 'This review version already exists. Choose a new version.'
            else:
                return redirect(url_for('analysis_review.review', listing_id=listing_id))
        home = {'scopes': scopes(shown['cost_items'], shown['cost_version'], shown['partial_budget'])} if shown else {}
        if fixture:
            home.update(price=DEMO_CASE['price'], resale=DEMO_CASE['resale'])
        return render_template('buyer_analysis_review.html', listing_id=listing_id, fixture=fixture,
                               shown=shown, error=error, items_text=items_text,
                               home=home, analysis=detail_analysis(home, request.args),
                               versions=store.versions(listing_id))

    @bp.get('/admin/renovation/<listing_id>/<version>/evidence')
    @admin_required
    def evidence(listing_id, version):
        data = store.evidence(listing_id, version)
        if data is None:
            abort(404)
        return Response(data, mimetype='application/octet-stream', headers={
            'Content-Disposition': 'attachment; filename="private-evidence"',
            'X-Content-Type-Options': 'nosniff',
        })

    @bp.get('/admin/renovation/<listing_id>/<version>/export')
    @admin_required
    def export(listing_id, version):
        data = store.scope(listing_id, version)
        if data is None:
            abort(404)
        return Response(json.dumps(data, indent=2), mimetype='application/json', headers={
            'Content-Disposition': 'attachment; filename="reviewed-scope.json"',
        })

    @bp.get('/admin/renovation/evaluation')
    @admin_required
    def evaluation():
        evidence_path = os.environ.get('BUYER_HISTORICAL_EVALUATION')
        if evidence_path:
            evidence = json.loads(Path(evidence_path).read_text())
        elif preview:
            evidence = demo_dataset()
        else:
            abort(404)
        return render_template('buyer_analysis_review.html', evaluation=evaluate(**evidence))

    @bp.route('/admin/renovation/outcomes/<pair_key>', methods=['GET', 'POST'])
    @admin_required
    def outcome(pair_key):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', pair_key):
            abort(404)
        error = None
        if request.method == 'POST':
            validate_document_length()
            validate_csrf()
            try:
                values = verified_costs()
                document = attachment()
                if not document:
                    raise ValueError('Attach the evidence for these costs.')
                store.save_outcome(pair_key, os.environ.get('FLIP_UI_ADMIN_USERNAME', 'admin'), values, document)
            except ValueError as exc:
                error = str(exc)
            else:
                return redirect(url_for('analysis_review.outcome', pair_key=pair_key))
        return render_template('buyer_analysis_outcome.html', pair_key=pair_key, fields=OUTCOME_FIELDS,
                               stored=store.outcome(pair_key), error=error)

    @bp.get('/admin/renovation/outcomes/<pair_key>/export')
    @admin_required
    def export_outcome(pair_key):
        data = store.outcome(pair_key)
        if not data:
            abort(404)
        data.pop('provenance')
        return Response(json.dumps({'verified_costs': data}, indent=2), mimetype='application/json', headers={
            'Content-Disposition': 'attachment; filename="verified-costs.json"',
        })

    app.register_blueprint(bp)
