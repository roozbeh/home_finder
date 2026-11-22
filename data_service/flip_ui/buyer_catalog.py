"""Read-only access to the publication artifact. No raw-feed access."""
import json
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


catalog_metrics = ContextVar('catalog_metrics', default=None)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def current_payload(value, now):
    data = json.loads(value)
    for expiry, fields in (
        ('sales_expires', ('sales', 'resale')),
        ('projects_expires', ('projects',)),
    ):
        if data.pop(expiry, now) <= now:
            for key in fields:
                data.pop(key, None)
    return data


class Catalog:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def _connect(self):
        metrics = catalog_metrics.get()
        started = perf_counter() if metrics is not None else None
        with closing(sqlite3.connect(f'{self.path.as_uri()}?mode=ro', uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            if metrics is not None:
                def count_reads(statement):
                    if statement.lstrip().upper().startswith('SELECT'):
                        metrics['reads'] += 1
                connection.set_trace_callback(count_reads)
            yield connection
        if metrics is not None:
            metrics['milliseconds'] += (perf_counter() - started) * 1_000

    @staticmethod
    def _matching(mode, filters):
        where = ['expires > ?', 'mode = ?', "coalesce(json_extract(payload, '$.market'), 'sale') = ?"]
        params = [utcnow(), mode, filters.get('market', 'sale')]
        for key, operator, column in (
            ('city', '=', 'city'), ('min_price', '>=', 'price'),
            ('max_price', '<=', 'price'), ('beds', '>=', 'beds'),
            ('dom', '>=', 'dom'), ('baths', '>=', "json_extract(payload, '$.baths')"),
            ('property_type', '=', "json_extract(payload, '$.property_type')"),
            ('min_sqft', '>=', "json_extract(payload, '$.sqft')"),
            ('min_lot', '>=', "json_extract(payload, '$.lot_sqft')"),
            ('min_year', '>=', "json_extract(payload, '$.year_built')"),
        ):
            if filters.get(key) not in ('', None):
                where.append(f'{column} {operator} ?')
                params.append(filters[key])
        for key in ('reduced', 'auction', 'bank_owned'):
            if filters.get(key):
                where.append(f'{key} = 1')
        if filters.get('photos_only'):
            where.append("json_array_length(payload, '$.photos') > 0")
        return 'WHERE ' + ' AND '.join(where), params

    def search(self, mode, filters, page, size=12):
        if not self.path.is_file():
            return [], [], [], 0
        matched, params = self._matching(mode, filters)
        base, base_params = self._matching(mode, {'market': filters.get('market', 'sale')})
        with self._connect() as db:
            cities = db.execute(f'SELECT city, count(*) FROM homes {base} GROUP BY city ORDER BY city', base_params).fetchall()
            capabilities = db.execute(f'SELECT count(dom), count(reduced), count(auction), count(bank_owned) FROM homes {base}', base_params).fetchone()
            count = db.execute(f'SELECT count(*) FROM homes {matched}', params).fetchone()[0]
            rows = db.execute(
                f'SELECT payload FROM homes {matched} ORDER BY price, id LIMIT ? OFFSET ?',
                (*params, size, (page - 1) * size),
            ).fetchall()
        return [current_payload(row[0], params[0]) for row in rows], cities, list(capabilities), count

    def property_types(self, mode, market):
        if not self.path.is_file():
            return []
        matched, params = self._matching(mode, {'market': market})
        with self._connect() as db:
            return [row[0] for row in db.execute(f"""
                SELECT DISTINCT json_extract(payload, '$.property_type') AS type
                FROM homes {matched} ORDER BY type COLLATE NOCASE
            """, params) if row[0]]

    def matching_locations(self, mode, filters):
        if not self.path.is_file():
            return []
        matched, params = self._matching(mode, filters)
        with self._connect() as db:
            rows = db.execute(f"""
                SELECT id, price, json_extract(payload, '$.location') AS label,
                       json_extract(payload, '$.coordinates') AS coordinates
                FROM homes {matched}
                  AND json_type(payload, '$.coordinates') = 'array'
                ORDER BY id
            """, params).fetchall()
        return [(row['id'], row['price'], row['label'], json.loads(row['coordinates'])) for row in rows]

    def get(self, mode, public_id):
        if not self.path.is_file():
            return None
        now = utcnow()
        with self._connect() as db:
            row = db.execute('SELECT payload FROM homes WHERE id = ? AND mode = ? AND expires > ?',
                             (public_id, mode, now)).fetchone()
        return current_payload(row[0], now) if row else None

    def media(self, mode, public_id, media_id):
        if not self.path.is_file():
            return None
        with self._connect() as db:
            row = db.execute(
                '''SELECT media.content, media.mime FROM media JOIN homes ON homes.id = media.home_id
                   WHERE homes.id = ? AND media.id = ? AND mode = ? AND expires > ?''',
                (public_id, media_id, mode, utcnow()),
            ).fetchone()
        return tuple(row) if row else None
