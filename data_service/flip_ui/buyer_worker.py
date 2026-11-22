"""Resumable, metered whole-gallery worker. The included executor uses offline fixtures."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import uuid

from buyer_catalog import catalog_metrics, utcnow


COUNTIES = frozenset({
    'Alameda', 'Contra Costa', 'Marin', 'Napa', 'San Francisco',
    'San Mateo', 'Santa Clara', 'Solano', 'Sonoma',
})
MODEL = 'fixture-photo-v1'
PROMPT = 'room-observation-v1'
ATTEMPT_ESTIMATE = 100  # microdollars per fixture attempt
MAX_ATTEMPTS = 3
STATES = (
    'queued', 'processing', 'missing media', 'partial', 'insufficient comps',
    'complete', 'failed', 'rights disabled', 'out of pilot',
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS control (
    id INTEGER PRIMARY KEY CHECK (id = 1), paused INTEGER NOT NULL DEFAULT 0,
    ceiling INTEGER NOT NULL DEFAULT 50000, spent INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO control(id) VALUES (1);
CREATE TABLE IF NOT EXISTS worker_listings (
    id TEXT PRIMARY KEY, mode TEXT NOT NULL, county TEXT NOT NULL, expires TEXT NOT NULL,
    publish_ok INTEGER NOT NULL, ai_ok INTEGER NOT NULL, derived_ok INTEGER NOT NULL,
    pilot_ok INTEGER NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0,
    updated_fingerprint TEXT NOT NULL, inputs TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_images (
    id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES worker_listings(id),
    image_version TEXT NOT NULL, model TEXT NOT NULL, prompt TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
    reservation TEXT, lease_until TEXT, fixture TEXT NOT NULL, result TEXT,
    UNIQUE(listing_id, image_version, model, prompt)
);
CREATE TABLE IF NOT EXISTS worker_gallery (
    listing_id TEXT NOT NULL REFERENCES worker_listings(id),
    image_id INTEGER NOT NULL UNIQUE REFERENCES worker_images(id),
    PRIMARY KEY(listing_id, image_id)
);
CREATE TABLE IF NOT EXISTS worker_attempts (
    id TEXT PRIMARY KEY, image_id INTEGER NOT NULL REFERENCES worker_images(id),
    listing_id TEXT NOT NULL REFERENCES worker_listings(id),
    state TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, simulated_micros INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_results (
    listing_id TEXT PRIMARY KEY REFERENCES worker_listings(id),
    fingerprint TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_cache (
    listing_id TEXT PRIMARY KEY REFERENCES worker_listings(id), hits INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS worker_image_queue ON worker_images(state, attempts, id);
CREATE INDEX IF NOT EXISTS worker_image_lease ON worker_images(lease_until) WHERE state='processing';
CREATE INDEX IF NOT EXISTS worker_attempt_listing ON worker_attempts(listing_id);
"""


def fingerprint(value):
    return sha256(json.dumps(value, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def pilot_ids(rows, size=100):
    """Round-robin across county, property type, condition, and gallery-size bands."""
    groups = defaultdict(deque)
    for row in sorted(rows, key=lambda row: row['id']):
        group = (row['county'], row.get('property_type', ''), row.get('condition', ''),
                 min(len(row.get('media', [])) // 20, 4))
        groups[group].append(row['id'])
    buckets = deque(groups[key] for key in sorted(groups))
    chosen = set()
    while buckets and len(chosen) < size:
        bucket = buckets.popleft()
        chosen.add(bucket.popleft())
        if bucket:
            buckets.append(bucket)
    return chosen


class Worker:
    def __init__(self, path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys = ON')
        self.connection.executescript(SCHEMA)

    def close(self):
        self.connection.close()

    @contextmanager
    def _write(self):
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def controls(self, *, paused=None, ceiling=None):
        if ceiling is not None and (type(ceiling) is not int or ceiling < 0):
            raise ValueError('The budget ceiling must be a nonnegative integer of microdollars')
        with self._write() as db:
            if paused is not None:
                db.execute('UPDATE control SET paused = ? WHERE id = 1', (paused,))
            if ceiling is not None:
                db.execute('UPDATE control SET ceiling = ? WHERE id = 1', (ceiling,))

    def sync(self, manifest, *, pilot=False, model=MODEL, prompt=PROMPT):
        from buyer_publish import rights

        mode = manifest['mode']
        if mode not in {'preview', 'approved'}:
            raise ValueError('A known inventory mode is required')
        listings = []
        for row in manifest['homes']:
            if row.get('county') not in COUNTIES or row.get('status') != 'Active' or row.get('withdrawn'):
                continue
            permissions, expires = rights(manifest, row)
            publish_ok = {'internet', 'price'} <= permissions
            ai_ok = 'ai' in permissions
            listings.append((row, expires, publish_ok, ai_ok, 'derived' in permissions))
        if len({row['id'] for row, *_ in listings}) != len(listings):
            raise ValueError('Listing IDs must be unique within the manifest')
        chosen = pilot_ids([row for row, _, _, ai_ok, _ in listings if ai_ok]) if pilot else None
        with self._write() as db:
            # A snapshot is authoritative for its own mode. Rows absent from it lose dispatch rights.
            db.execute('UPDATE worker_listings SET withdrawn = 1 WHERE mode = ?', (mode,))
            for row, expires, publish_ok, ai_ok, derived_ok in listings:
                self._sync_listing(db, row, mode, expires, publish_ok, ai_ok, derived_ok,
                                   chosen is None or row['id'] in chosen, model, prompt)

    @staticmethod
    def _sync_listing(db, row, mode, expires, publish_ok, ai_ok, derived_ok, pilot_ok, model, prompt):
        from buyer_analysis import validate_scope

        listing_id = row['id']
        previous = db.execute('SELECT mode, updated_fingerprint FROM worker_listings WHERE id = ?',
                              (listing_id,)).fetchone()
        if previous and previous['mode'] != mode:
            raise ValueError('Preview and approved listings must use different IDs')
        photos = {}
        if ai_ok:
            for photo in row.get('media', []):
                version = photo.get('image_version')
                if photo.get('process') is True and isinstance(version, str) and 0 < len(version) <= 128:
                    photos[version] = {
                        'category': photo.get('fixture_category', 'Exterior'),
                        'failure': photo.get('fixture_failure'),
                    } if mode == 'preview' else {}
        inputs = {'price': row.get('price'), **{
            key: row.get('analysis_inputs', {}).get(key)
            for key in ('comps_version', 'comp_prices', 'price_book_version', 'price_book')
        }}
        update = fingerprint((inputs, model, prompt, sorted(photos)))
        if mode == 'preview' and ai_ok and inputs['price_book'] and (
            not previous or update != previous['updated_fingerprint']
        ):
            validate_scope([{**entry, 'room': room} for room, entry in inputs['price_book'].items()],
                           inputs['price_book_version'], frozenset(photos))
        db.execute("""
            INSERT INTO worker_listings VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET expires=excluded.expires, publish_ok=excluded.publish_ok,
                county=excluded.county, ai_ok=excluded.ai_ok, derived_ok=excluded.derived_ok,
                pilot_ok=excluded.pilot_ok, withdrawn=0,
                updated_fingerprint=excluded.updated_fingerprint, inputs=excluded.inputs
        """, (listing_id, mode, row['county'], expires, publish_ok, ai_ok, derived_ok, pilot_ok, 0,
              update, json.dumps(inputs)))
        if previous and update == previous['updated_fingerprint']:
            return
        db.execute('DELETE FROM worker_gallery WHERE listing_id = ?', (listing_id,))
        hits = 0
        for version, fixture in photos.items():
            cursor = db.execute("""
                INSERT OR IGNORE INTO worker_images(listing_id,image_version,model,prompt,fixture)
                VALUES (?,?,?,?,?)
            """, (listing_id, version, model, prompt, json.dumps(fixture)))
            image = db.execute("""
                SELECT id, state FROM worker_images WHERE listing_id = ? AND image_version = ?
                AND model = ? AND prompt = ?
            """, (listing_id, version, model, prompt)).fetchone()
            db.execute('INSERT INTO worker_gallery VALUES (?,?)', (listing_id, image['id']))
            hits += cursor.rowcount == 0 and image['state'] == 'complete'
        if hits:
            db.execute("""
                INSERT INTO worker_cache VALUES (?,?)
                ON CONFLICT(listing_id) DO UPDATE SET hits=hits+excluded.hits
            """, (listing_id, hits))
        Worker._rebuild(db, listing_id)

    @staticmethod
    def _rebuild(db, listing_id, now=None):
        from buyer_analysis import purchase_scenario, scopes

        now = now or utcnow()
        row = db.execute("""SELECT * FROM worker_listings WHERE id = ?
                         AND withdrawn = 0 AND ai_ok = 1 AND expires > ?""", (listing_id, now)).fetchone()
        if not row:
            return
        if db.execute("""
            SELECT 1 FROM worker_images JOIN worker_gallery ON worker_gallery.image_id=worker_images.id
            WHERE worker_gallery.listing_id=? AND state IN ('queued','processing','retryable') LIMIT 1
        """, (listing_id,)).fetchone():
            return
        gallery = db.execute("""
            SELECT state, result, image_version FROM worker_images
            JOIN worker_gallery ON worker_gallery.image_id=worker_images.id
            WHERE worker_gallery.listing_id=?
        """, (listing_id,)).fetchall()
        evidence = defaultdict(list)
        for image in gallery:
            if image['state'] == 'complete':
                evidence[json.loads(image['result'])['category']].append(image['image_version'])
        if not evidence:
            return
        inputs = json.loads(row['inputs'])
        prices = [p for p in inputs.get('comp_prices') or [] if type(p) is int and p > 0]
        resale = round(sum(prices) / len(prices)) if len(prices) >= 2 else None
        # The fixture book reprices one sourced allowance per observed category.
        # Publication still requires reviewed scope and independent evidence grants.
        cost_lines = []
        for room, images in sorted(evidence.items()):
            source = (inputs.get('price_book') or {}).get(room)
            if source:
                cost_lines.append({**source, 'room': room, 'evidence_photo_ids': images})
        partial = (sum(map(len, evidence.values())) < len(gallery) or len(cost_lines) < len(evidence))
        budget = scopes(cost_lines, inputs['price_book_version'], partial,
                        evidence_ids=[image for ids in evidence.values() for image in ids]) if cost_lines else None
        selected = next((choice for choice in budget['budgets']
                         if choice['choices'] == budget['default']), None) if budget else None
        renovation = (selected['low'] + selected['high']) / 2 if selected and selected['low'] is not None else None
        asking = inputs.get('price')
        scenario = purchase_scenario(asking, resale, selected['low'], selected['high']) if (
            renovation is not None and resale is not None and type(asking) is int and asking > 0
        ) else None
        result = {
            'asking': asking, 'resale': resale, 'renovation': renovation, 'budget': budget, 'scenario': scenario,
            'gross_after_purchase_and_work': (
                resale - asking - renovation if resale is not None and renovation is not None
                and type(asking) is int else None
            ),
            'partial': partial, 'comps_version': inputs.get('comps_version'),
            'price_book_version': inputs.get('price_book_version'),
        }
        db.execute("""
            INSERT INTO worker_results VALUES (?,?,?)
            ON CONFLICT(listing_id) DO UPDATE SET fingerprint=excluded.fingerprint, payload=excluded.payload
        """, (listing_id, row['updated_fingerprint'], json.dumps(result)))

    @staticmethod
    def _reap(db, now):
        affected = db.execute("""
            SELECT DISTINCT listing_id FROM worker_images WHERE state='processing' AND lease_until <= ?
        """, (now,)).fetchall()
        if not affected:
            return
        db.execute("""
            UPDATE worker_attempts SET state='interrupted'
            WHERE id IN (SELECT reservation FROM worker_images WHERE state='processing' AND lease_until <= ?)
        """, (now,))
        db.execute("""
            UPDATE worker_images SET state=CASE WHEN attempts >= ? THEN 'failed' ELSE 'retryable' END,
                reservation=NULL, lease_until=NULL WHERE state='processing' AND lease_until <= ?
        """, (MAX_ATTEMPTS, now))
        for row in affected:
            Worker._rebuild(db, row['listing_id'], now)

    def _claim(self):
        with self._write() as db:
            now = utcnow()
            self._reap(db, now)
            control = db.execute('SELECT * FROM control').fetchone()
            if control['paused'] or control['spent'] + ATTEMPT_ESTIMATE > control['ceiling']:
                return None
            image = db.execute("""
                SELECT image.* FROM worker_images image JOIN worker_gallery gallery ON gallery.image_id=image.id
                JOIN worker_listings listing ON listing.id=gallery.listing_id
                WHERE listing.mode='preview' AND listing.withdrawn=0 AND listing.ai_ok=1
                AND listing.pilot_ok=1 AND listing.expires > ? AND image.state IN ('queued','retryable')
                ORDER BY image.attempts, image.id LIMIT 1
            """, (now,)).fetchone()
            if not image:
                return None
            reservation = uuid.uuid4().hex
            lease_until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            db.execute("""
                UPDATE worker_images SET state='processing', attempts=attempts+1, reservation=?, lease_until=?
                WHERE id=?
            """, (reservation, lease_until, image['id']))
            db.execute('INSERT INTO worker_attempts VALUES (?,?,?,?,0,0,?)',
                       (reservation, image['id'], image['listing_id'], 'reserved', ATTEMPT_ESTIMATE))
            db.execute('UPDATE control SET spent=spent+? WHERE id=1', (ATTEMPT_ESTIMATE,))
            return image, reservation

    def _finish(self, image, reservation, outcome):
        state, result, input_tokens, output_tokens, cost = outcome
        with self._write() as db:
            now = utcnow()
            current = db.execute('SELECT state FROM worker_attempts WHERE id=?', (reservation,)).fetchone()
            if not current or current['state'] != 'reserved':
                return
            db.execute("""
                UPDATE worker_attempts SET state=?, input_tokens=?, output_tokens=?, simulated_micros=? WHERE id=?
            """, (state, input_tokens, output_tokens, cost, reservation))
            db.execute('UPDATE control SET spent=spent+? WHERE id=1', (cost - ATTEMPT_ESTIMATE,))
            active = db.execute("""
                SELECT 1 FROM worker_listings listing JOIN worker_gallery gallery ON gallery.listing_id=listing.id
                WHERE gallery.image_id=? AND listing.withdrawn=0 AND listing.ai_ok=1 AND listing.expires > ?
            """, (image['id'], now)).fetchone()
            if not active:
                state, result = 'queued', None
            elif state == 'retryable' and image['attempts'] + 1 >= MAX_ATTEMPTS:
                state = 'failed'
            db.execute("""
                UPDATE worker_images SET state=?, result=?, reservation=NULL, lease_until=NULL,
                    attempts=CASE WHEN ? THEN attempts ELSE attempts-1 END WHERE id=? AND reservation=?
            """, (state, json.dumps(result) if active and result else None, bool(active), image['id'], reservation))
            if active:
                self._rebuild(db, image['listing_id'], now)

    def run(self, chunk=16):
        if type(chunk) is not int or not 1 <= chunk <= 1000:
            raise ValueError('Chunk size must be between 1 and 1,000')
        completed_attempts = 0
        for _ in range(chunk):
            claim = self._claim()
            if not claim:
                break
            image, reservation = claim
            outcome = fixture_classify(json.loads(image['fixture']), image['attempts'] + 1)
            self._finish(image, reservation, outcome)
            completed_attempts += 1
        return completed_attempts

    def ready(self, listing_id):
        """Read a current private fixture result only while publish and derivation rights remain."""
        row = self.connection.execute("""
            SELECT payload FROM worker_results result JOIN worker_listings listing ON listing.id=result.listing_id
            WHERE listing.id=? AND listing.withdrawn=0 AND listing.publish_ok=1 AND listing.ai_ok=1
              AND listing.derived_ok=1 AND listing.expires > ?
              AND listing.updated_fingerprint=result.fingerprint
        """, (listing_id, utcnow())).fetchone()
        return json.loads(row['payload']) if row else None

    def report(self):
        now = utcnow()
        db = self.connection
        control = dict(db.execute('SELECT * FROM control').fetchone())
        summaries = db.execute("""
            SELECT listing.id, listing.county, listing.mode, listing.publish_ok, listing.ai_ok,
                listing.pilot_ok, listing.expires > ? AS effective, count(image.id) AS eligible,
                sum(image.state='complete') AS completed, sum(image.state='unavailable') AS unavailable,
                sum(image.state='retryable') AS retryable, sum(image.state='failed') AS failed,
                sum(image.state='processing') AS processing, sum(image.state='queued') AS queued,
                coalesce(sum(case when image.attempts>1 then image.attempts-1 else 0 end),0) AS retries,
                coalesce(costs.tokens,0) AS tokens, coalesce(costs.simulated,0) AS simulated,
                coalesce(costs.failures,0) AS image_failures, coalesce(cache.hits,0) AS cache_hits,
                result.payload AS result
            FROM worker_listings listing
            LEFT JOIN worker_gallery gallery ON gallery.listing_id=listing.id
            LEFT JOIN worker_images image ON image.id=gallery.image_id
            LEFT JOIN worker_results result ON result.listing_id=listing.id
                AND result.fingerprint=listing.updated_fingerprint
            LEFT JOIN worker_cache cache ON cache.listing_id=listing.id
            LEFT JOIN (
                SELECT listing_id, sum(input_tokens+output_tokens) AS tokens,
                    sum(simulated_micros) AS simulated,
                    sum(state in ('unavailable','retryable','interrupted')) AS failures
                FROM worker_attempts GROUP BY listing_id
            ) costs ON costs.listing_id=listing.id
            WHERE listing.withdrawn=0 GROUP BY listing.id ORDER BY listing.county, listing.id
        """, (now,)).fetchall()
        rows = []
        counts = Counter({state: 0 for state in STATES})
        for row in summaries:
            listing = {key: value if value is not None else 0 for key, value in dict(row).items()}
            listing['state'] = coverage_state(listing)
            counts[listing['state']] += 1
            listing.pop('result')
            rows.append(listing)
        measured = [row['simulated'] for row in rows if row['state'] == 'complete' and row['mode'] == 'preview']
        remaining = sum(row['queued'] + row['retryable'] for row in rows
                        if row['effective'] and row['ai_ok'] and row['pilot_ok'])
        return {
            'listings': rows, 'states': dict(counts), 'control': control,
            'publication_eligible': sum(row['publish_ok'] and row['effective'] for row in rows),
            'ai_eligible': sum(row['ai_ok'] and row['effective'] for row in rows),
            'measured_micros_per_home': sum(measured) / len(measured) if measured else None,
            'remaining_micros': remaining * ATTEMPT_ESTIMATE,
        }


def coverage_state(row):
    if not row['ai_ok'] or not row['effective']:
        return 'rights disabled'
    if not row['pilot_ok']:
        return 'out of pilot'
    if not row['eligible']:
        return 'missing media'
    if row['processing']:
        return 'processing'
    if row['queued'] or row['retryable']:
        return 'partial' if row['completed'] or row['retryable'] else 'queued'
    if not row['completed']:
        return 'failed'
    if row['completed'] < row['eligible']:
        return 'partial'
    if not row['result'] or json.loads(row['result'])['resale'] is None:
        return 'insufficient comps'
    return 'complete'


def fixture_classify(photo, attempt):
    if photo.get('failure') == 'unavailable':
        return 'unavailable', None, 0, 0, 0
    if photo.get('failure') == 'always' or photo.get('failure') == 'once' and attempt == 1:
        return 'retryable', None, 300, 0, 70
    return 'complete', {'category': str(photo['category'])}, 300, 24, 80


def register_worker(app, admin_required, validate_csrf):
    from flask import abort, g, redirect, render_template, request, url_for

    if os.environ.get('BUYER_BENCHMARK_METRICS') == '1':
        @app.before_request
        def record_catalog_metrics():
            g.catalog_metrics_token = catalog_metrics.set({'reads': 0, 'milliseconds': 0})

        @app.after_request
        def expose_catalog_metrics(response):
            metrics = catalog_metrics.get()
            response.headers.add('Server-Timing',
                                 f"buyer-db-reads;dur={metrics['reads']}, buyer-db;dur={metrics['milliseconds']:.3f}")
            return response

        @app.teardown_request
        def reset_catalog_metrics(_error):
            token = g.pop('catalog_metrics_token', None)
            if token is not None:
                catalog_metrics.reset(token)

    @app.route('/admin/analysis', methods=['GET', 'POST'])
    @admin_required
    def buyer_analysis_dashboard():
        path = app.config.get('BUYER_WORKER_DB') or os.environ.get('BUYER_WORKER_DB')
        if not path:
            if request.method == 'POST':
                abort(503)
            return render_template('buyer_worker.html', report=None)
        with closing(Worker(path)) as worker:
            if request.method == 'POST':
                validate_csrf()
                action = request.form.get('action')
                if action not in {'pause', 'resume'}:
                    abort(400)
                worker.controls(paused=action == 'pause')
                return redirect(url_for('buyer_analysis_dashboard'), code=303)
            report = worker.report()
            page = request.args.get('page', 1, type=int)
            pages = max(1, (len(report['listings']) + 49) // 50)
            page = max(1, min(page, pages))
            report['listings'] = report['listings'][(page - 1) * 50:page * 50]
            return render_template('buyer_worker.html', report=report, page=page, pages=pages)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=os.environ.get('BUYER_WORKER_DB'))
    subparsers = parser.add_subparsers(dest='command', required=True)
    for command in ('sync', 'plan'):
        sync = subparsers.add_parser(command)
        sync.add_argument('manifest', type=Path)
        sync.add_argument('--pilot', action='store_true')
        sync.add_argument('--model', default=MODEL)
        sync.add_argument('--prompt', default=PROMPT)
    run = subparsers.add_parser('run')
    run.add_argument('--chunk', type=int, default=16)
    run.add_argument('--fixture', required=True, action='store_true')
    controls = subparsers.add_parser('control')
    controls.add_argument('--ceiling-micros', type=int)
    pause_options = controls.add_mutually_exclusive_group()
    pause_options.add_argument('--pause', dest='paused', action='store_true', default=None)
    pause_options.add_argument('--resume', dest='paused', action='store_false')
    subparsers.add_parser('report')
    args = parser.parse_args()
    if not args.db and args.command != 'plan':
        parser.error('Use --db or set BUYER_WORKER_DB')
    with closing(Worker(':memory:' if args.command == 'plan' else args.db)) as worker:
        if args.command in ('sync', 'plan'):
            worker.sync(json.loads(args.manifest.read_text()), pilot=args.pilot, model=args.model, prompt=args.prompt)
        elif args.command == 'run':
            print(f'Fixture attempts in this chunk: {worker.run(args.chunk)}')
        elif args.command == 'control':
            worker.controls(paused=args.paused, ceiling=args.ceiling_micros)
        print(json.dumps(worker.report()))


if __name__ == '__main__':
    main()
