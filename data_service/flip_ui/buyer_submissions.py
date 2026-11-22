"""Durable form submissions, private workflow history and notification outbox."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from buyer_catalog import utcnow


class Inbox:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        return closing(db)

    def initialize(self):
        with self.connect() as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS submissions (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, created TEXT NOT NULL,
                    status TEXT NOT NULL, fingerprint TEXT NOT NULL, session_key TEXT NOT NULL,
                    mode TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS submissions_rate ON submissions(fingerprint, created);
                CREATE INDEX IF NOT EXISTS submissions_queue ON submissions(mode, kind, created);
                CREATE TABLE IF NOT EXISTS history (
                    submission_id TEXT NOT NULL, created TEXT NOT NULL, event TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox (
                    submission_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, updated TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    day TEXT NOT NULL, mode TEXT NOT NULL, home TEXT NOT NULL,
                    event TEXT NOT NULL, entry TEXT NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(day, mode, home, event, entry));
            """)
        self.path.chmod(0o600)

    def submit(self, submission_id, kind, mode, fingerprint, session_key, payload, eligible):
        now = datetime.now(timezone.utc)
        with self.connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            if not eligible():
                return 'withdrawn'
            existing = db.execute('SELECT kind, mode, session_key, payload FROM submissions WHERE id=?',
                                  (submission_id,)).fetchone()
            if existing:
                valid = (existing['kind'], existing['mode'], existing['session_key'],
                         json.loads(existing['payload']).get('home_id')) == (
                            kind, mode, session_key, payload.get('home_id'))
                return 'saved' if valid else 'invalid'
            recent = db.execute('SELECT count(*) FROM submissions WHERE fingerprint=? AND created>?',
                                (fingerprint, (now - timedelta(hours=1)).isoformat())).fetchone()[0]
            if recent >= 12:
                return 'limited'
            status = {'contact': 'new', 'request-tour': 'tour requested', 'partner': 'pending'}[kind]
            db.execute('INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?)', (
                submission_id, kind, now.isoformat(), status, fingerprint, session_key, mode, json.dumps(payload),
            ))
            db.execute('INSERT INTO outbox(submission_id) VALUES (?)', (submission_id,))
            if payload.get('home_id'):
                self._event(db, mode, payload['home_id'], f'{kind} submitted', payload['entry'])
        return 'saved'

    def _event(self, db, mode, home, event, entry):
        db.execute("""
            INSERT INTO events VALUES (?,?,?,?,?,1)
            ON CONFLICT(day,mode,home,event,entry) DO UPDATE SET count=count+1
        """, (utcnow()[:10], mode, home, event, entry))

    def event(self, mode, home, event, entry):
        with self.connect() as db, db:
            self._event(db, mode, home, event, entry)

    def activity(self, mode):
        with self.connect() as db:
            return db.execute('''SELECT home, event, entry, sum(count) AS total FROM events
                WHERE mode=? AND day>=date('now','-30 days')
                GROUP BY home, event, entry ORDER BY home, event, entry LIMIT 200''', (mode,)).fetchall()

    def queue(self, mode, partner):
        operator = '=' if partner else '!='
        with self.connect() as db:
            rows = db.execute(f"""SELECT id, kind, created, status, payload FROM submissions
                                 WHERE mode=? AND kind {operator} 'partner'
                                 ORDER BY created DESC LIMIT 200""", (mode,)).fetchall()
        return [{**dict(row), 'data': json.loads(row['payload'])} for row in rows]

    def detail(self, mode, submission_id):
        with self.connect() as db:
            row = db.execute("""SELECT id, kind, created, status, payload, state AS delivery
                                FROM submissions JOIN outbox ON id=submission_id WHERE id=? AND mode=?""",
                             (submission_id, mode)).fetchone()
            history = db.execute('SELECT created, event FROM history WHERE submission_id=? ORDER BY rowid',
                                 (submission_id,)).fetchall()
        return {**dict(row), 'data': json.loads(row['payload']), 'history': history} if row else None

    def transition(self, mode, submission_id, status, actions, expected):
        if status not in actions:
            return False
        with self.connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM submissions WHERE id=? AND mode=?', (submission_id, mode)).fetchone()
            if row is None:
                return False
            if status == row['status']:
                return True
            if row['status'] != expected or row['status'] in {'opted out', 'closed', 'declined'}:
                return False
            db.execute('UPDATE submissions SET status=? WHERE id=?', (status, submission_id))
            db.execute('INSERT INTO history VALUES (?,?,?)', (submission_id, utcnow(), status))
        return True

    def deliver(self, send, limit=50):
        # A single operator-run dispatcher claims rows before sending. Delivery is at least once;
        # the stable message ID allows downstream deduplication after an ambiguous transport error.
        with self.connect() as db, db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("""SELECT id, mode, kind FROM submissions JOIN outbox ON id=submission_id
                WHERE state='pending' OR (state='sending' AND updated<?) ORDER BY created LIMIT ?""",
                ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(), limit)).fetchall()
            db.executemany("UPDATE outbox SET state='sending', attempts=attempts+1, updated=? WHERE submission_id=?",
                           [(utcnow(), row['id']) for row in rows])
        delivered = 0
        for row in rows:
            try:
                send(dict(row))
            except Exception:
                state = 'pending'
            else:
                state = 'delivered'
                delivered += 1
            with self.connect() as db, db:
                db.execute('UPDATE outbox SET state=?, updated=? WHERE submission_id=?',
                           (state, utcnow(), row['id']))
        return delivered
