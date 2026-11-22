"""Private intake for a single consenting supply partner and its publication overlay."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, redirect, render_template, request, url_for

from buyer_catalog import utcnow

FIXTURE_SOURCE = 'fixture-wholesaler'
PACIFIC = ZoneInfo('America/Los_Angeles')
SAFE_FIELDS = {'internet', 'price', 'city', 'property_type', 'status', 'credits', 'updated', 'media'}
PROPERTY_TYPES = ('Single family', 'Townhouse', 'Condominium', 'Multifamily')
SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities(
    id TEXT PRIMARY KEY, modified TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS updates(
    sequence INTEGER PRIMARY KEY, id TEXT NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL
);
"""


def read_window(raw):
    value = datetime.strptime(raw, '%Y-%m-%dT%H:%M')
    first = value.replace(tzinfo=PACIFIC, fold=0)
    second = value.replace(tzinfo=PACIFIC, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError('Choose a showing time outside the daylight-saving clock change.')
    return first.astimezone(timezone.utc).isoformat()


def form_payload(values, assigned):
    text_limits = {
        'city': 80, 'advertising_basis': 500, 'media_basis': 500,
        'access_contact': 200, 'access_instructions': 1500, 'assignment_terms': 1000,
        'sanitized_file': 100,
    }
    payload = {key: values.get(key, '').strip() for key in text_limits}
    errors = {key: f'Use at most {size} characters.' for key, size in text_limits.items()
              if len(payload[key]) > size}
    if not payload['city']:
        errors['city'] = 'Enter a permitted city or market label.'
    raw_price = values.get('price', '')
    if not re.fullmatch(r'[1-9][0-9]{0,8}', raw_price):
        errors['price'] = 'Enter a whole-dollar asking price.'
    property_type = values.get('property_type')
    if property_type not in PROPERTY_TYPES:
        errors['property_type'] = 'Select the property type.'
    name = payload['sanitized_file']
    if name and not re.fullmatch(r'[a-zA-Z0-9_-]+\.jpg', name):
        errors['sanitized_file'] = 'Enter the approved JPEG asset file name.'
    if name and not payload['media_basis']:
        errors['media_basis'] = 'Record the photo permission before attaching a photo.'

    windows = []
    for index in (1, 2):
        start = values.get(f'start_{index}', '')
        end = values.get(f'end_{index}', '')
        if not start and not end:
            continue
        try:
            first, last = read_window(start), read_window(end)
            if first >= last:
                raise ValueError('End the window after its start.')
            windows.append((first, last))
        except ValueError as exc:
            errors[f'start_{index}'] = (
                str(exc) if 'window' in str(exc) or 'daylight-saving' in str(exc)
                else 'Enter a valid start and end time for this window.'
            )
    verified = values.get('verified') == 'yes'
    if verified:
        if not payload['advertising_basis']:
            errors['advertising_basis'] = 'Record who authorized advertising and the written permission.'
        if not payload['access_contact']:
            errors['access_contact'] = 'Identify the party authorized to arrange access.'
        if not any(end > utcnow() for _, end in windows):
            errors['start_1'] = 'Enter at least one current or upcoming window before verifying.'
    if errors:
        return None, errors
    payload.update(
        windows=sorted(windows), price=int(raw_price), property_type=property_type, assigned=assigned,
        verified_at=utcnow() if verified else None,
    )
    return payload, {}


class SupplyStore:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def lock(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            yield db

    def all(self):
        with self.connect() as db:
            return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM opportunities ORDER BY modified DESC')]

    def get(self, home_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM opportunities WHERE id = ?', (home_id,)).fetchone()
        return json.loads(row['payload']) if row else None

    def history(self, home_id):
        with self.connect() as db:
            rows = db.execute('SELECT at, action, payload FROM updates WHERE id = ? ORDER BY sequence DESC', (home_id,))
            return [(row['at'], row['action'], json.loads(row['payload'])) for row in rows]

    def save(self, home_id, payload, action):
        at = utcnow()
        with self.connect() as db:
            db.execute(
                '''INSERT INTO opportunities VALUES (?,?,?)
                   ON CONFLICT(id) DO UPDATE SET modified=excluded.modified,payload=excluded.payload''',
                (home_id, at, json.dumps(payload)),
            )
            db.execute('INSERT INTO updates(id,at,action,payload) VALUES (?,?,?,?)',
                       (home_id, at, action, json.dumps(payload)))


def normalized_opportunity(item, source, label, now):
    verified = item.get('verified_at')
    if item.get('withdrawn_at') or not verified or not item.get('advertising_basis'):
        return None
    upcoming_ends = [end for _, end in item['windows'] if end > now.isoformat()]
    if not upcoming_ends:
        return None
    expires = min((datetime.fromisoformat(verified) + timedelta(hours=12)).isoformat(), max(upcoming_ends))
    if expires <= now.isoformat():
        return None
    media = bool(item['media_basis'] and item['sanitized_file'])
    allow = {field: True for field in SAFE_FIELDS if field != 'media' or media}
    row = {
        'id': item['id'], 'source': source, 'city': item['city'], 'price': item['price'],
        'status': 'Active', 'property_type': item['property_type'], 'updated': verified,
        'credit': {'source': label},
        'policy': {'effective': verified, 'refresh_by': expires, 'basis': item['advertising_basis'], 'allow': allow},
    }
    if media:
        # In this intake, attaching a photo requires approval for city-only display.
        row['media'] = [{'sanitized_file': item['sanitized_file'], 'address_safe': True, 'display': True}]
    return row


def overlay(base, store, source, label):
    now = datetime.now(timezone.utc)
    manifest = json.loads(Path(base).read_text())
    mode = manifest['mode']
    if mode == 'preview' and source == FIXTURE_SOURCE:
        manifest['sources'][source] = {
            'effective': '2020-01-01T00:00:00+00:00', 'refresh_by': '2099-01-01T00:00:00+00:00',
            'basis': 'Fictional partner approval for preview', 'allow': dict.fromkeys(SAFE_FIELDS, True),
        }
    elif source == FIXTURE_SOURCE or source not in manifest['sources']:
        raise ValueError('Approved supply requires its own reviewed source permissions.')
    source_grants = manifest['sources'][source].get('allow', {})
    if not all(source_grants.get(field) is True for field in ('credits', 'updated')):
        raise ValueError('Supply publication requires source attribution and verification-time permissions.')
    manifest['homes'].extend(
        row for item in store.all() if item.get('source_key') == source
        and (row := normalized_opportunity(item, source, label, now)) is not None
    )
    return manifest


def rebuild(base, store, source, label, catalog, assets):
    # Lazy import keeps store and form validation independent of the offline publisher.
    from buyer_publish import publish

    publish(overlay(base, store, source, label), catalog, assets)


def remove_public_copy(catalog, home_id):
    """Invalidate stale media and search entries before a changed record can be rebuilt."""
    path = Path(catalog).resolve()
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)) as db, db:
            db.execute('DELETE FROM media WHERE home_id = ?', (home_id,))
            db.execute('DELETE FROM homes WHERE id = ?', (home_id,))


def _editable_values(item):
    values = dict(item or {})
    for i, (start, end) in enumerate(values.get('windows', ()), start=1):
        values[f'start_{i}'] = datetime.fromisoformat(start).astimezone(PACIFIC).strftime('%Y-%m-%dT%H:%M')
        values[f'end_{i}'] = datetime.fromisoformat(end).astimezone(PACIFIC).strftime('%Y-%m-%dT%H:%M')
    if values.get('verified_at'):
        values['verified'] = 'yes'
    return values


def register_supply(app, admin_required, validate_csrf):
    bp = Blueprint('buyer_supply', __name__)
    store = SupplyStore(os.environ.get('BUYER_SUPPLY_DB', '/tmp/flip-finder-supply.sqlite3'))
    base = os.environ.get('BUYER_SUPPLY_MANIFEST')
    catalog = os.environ.get('BUYER_CATALOG')
    assets = os.environ.get('BUYER_SUPPLY_ASSETS')
    preview = os.environ.get('BUYER_MODE') == 'preview'
    source = os.environ.get('BUYER_SUPPLY_SOURCE', FIXTURE_SOURCE if preview else '')
    if not source:
        return
    label = os.environ.get('BUYER_SUPPLY_LABEL', 'Sample Bay Area Supply' if preview else source)
    assigned = os.environ.get('BUYER_SUPPLY_ASSIGNEE', 'Roozbeh')
    publish_ready = bool(base and catalog and assets)

    @bp.get('/admin/supply')
    @admin_required
    def index():
        return render_template('buyer_supply_index.html', rows=store.all(), assigned=assigned,
                               label=label, preview=source == FIXTURE_SOURCE, publish_ready=publish_ready)

    @bp.route('/admin/supply/new', methods=['GET', 'POST'])
    @bp.route('/admin/supply/<home_id>', methods=['GET', 'POST'])
    @admin_required
    def detail(home_id=None):
        errors = {}
        form = None
        item = store.get(home_id) if home_id else None
        if home_id and item is None:
            abort(404)
        if request.method == 'POST':
            validate_csrf()
            action = request.form.get('action', 'save')
            if action not in ('save', 'withdraw') or (action == 'withdraw' and not home_id):
                abort(400)
            form = request.form
            payload, errors = (None, {}) if action == 'withdraw' else form_payload(form, assigned)
            if not publish_ready and action == 'save' and payload and payload['verified_at']:
                errors['verified'] = 'Connect the approved publication settings before verifying an opportunity.'
            if not errors:
                with store.lock():
                    current = store.get(home_id) if home_id else None
                    if current and current.get('withdrawn_at'):
                        abort(409, 'This opportunity has already been withdrawn.')
                    saved_id = home_id or uuid4().hex
                    if action == 'withdraw':
                        payload = {**current, 'withdrawn_at': utcnow()}
                    payload.update(id=saved_id, source=label, source_key=source)
                    if publish_ready and current:
                        remove_public_copy(catalog, saved_id)
                    store.save(saved_id, payload, action)
                    if publish_ready:
                        try:
                            rebuild(base, store, source, label, catalog, assets)
                        except (RuntimeError, ValueError, OSError, sqlite3.Error):
                            app.logger.exception('Supply publication failed')
                            return render_template('buyer_supply_form.html', item=payload,
                                                   values=_editable_values(payload), label=label, errors={},
                                                   history=store.history(saved_id), types=PROPERTY_TYPES,
                                                   message='Private update saved. Publication failed. Review the settings and rebuild the catalog.'), 503
                return redirect(url_for('buyer_supply.detail', home_id=saved_id), code=303)
        return render_template('buyer_supply_form.html', item=item, values=form or _editable_values(item),
                               label=label, errors=errors, history=store.history(home_id) if home_id else [],
                               types=PROPERTY_TYPES), 422 if errors else 200

    app.register_blueprint(bp)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--assets', required=True)
    parser.add_argument('--source', default=FIXTURE_SOURCE)
    parser.add_argument('--label', default='Sample Bay Area Supply')
    args = parser.parse_args()
    store = SupplyStore(args.store)
    with store.lock():
        rebuild(args.base, store, args.source, args.label, args.catalog, args.assets)
