"""Signed-out buyer routes over a separate catalog and submissions store."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request, session, url_for

from buyer_analysis import detail_analysis
from buyer_catalog import Catalog
from buyer_media import fetch_mls_photo
from buyer_property_types import PROPERTY_GROUPS, normalized, type_filter
from buyer_submissions import Inbox

DEFAULT_DATA = Path('/tmp/flip-buyer')
CONTACT_FIELDS = ('name', 'channel', 'contact', 'question')
TOUR_FIELDS = ('preferred_time', 'alternative_time', 'window_minutes', 'representation')
PARTNER_FIELDS = ('name', 'license', 'brokerage', 'markets', 'experience', 'availability', 'channel', 'contact')
SEARCH_FIELDS = ('city', 'market', 'min_price', 'max_price', 'beds', 'baths', 'property_type',
                 'min_sqft', 'min_lot', 'min_year', 'dom', 'reduced', 'auction', 'bank_owned',
                 'photos_only', 'page')
MARKET_LABELS = {'sale': 'For sale', 'rent': 'For rent', 'sold': 'Sold'}
LEAD_ACTIONS = ('contacted', 'qualified conversation', 'tour requested', 'tour confirmed', 'tour held',
                'representation agreement', 'offer', 'closing', 'closed', 'opted out')
PARTNER_ACTIONS = ('verified', 'declined')
PREVIEW_IDENTITY = {
    'name': 'Roozbeh', 'brokerage': 'Sample Buyer Brokerage', 'license': 'Sample license',
    'disclosure': 'Preview identity for testing.', 'recipient': 'roozbeh-preview',
}


def setting(name, default=''):
    return os.environ.get(name, default).strip()


def identity_for(mode):
    if mode == 'preview':
        return PREVIEW_IDENTITY
    result = {key: setting(f'BUYER_IDENTITY_{key.upper()}') for key in PREVIEW_IDENTITY}
    if setting('BUYER_IDENTITY_VERIFIED') != 'true' or not all(result.values()):
        return None
    return result


def search_parameters(args):
    filters = {key: args.get(key, '').strip()[:100] for key in SEARCH_FIELDS}
    types = args.getlist('property_type') if hasattr(args, 'getlist') else [filters['property_type']]
    if len(types) > 16:
        raise ValueError('Choose the available property types.')
    selected_types = []
    if args.get('all_property_types') != '1':
        for item in types:
            value = item.strip()
            if len(value) <= 100 and normalized(value) not in {'', 'any'}:
                selected_types.append(type_filter(value))
    filters['property_type'] = list(dict.fromkeys(selected_types))
    for key in ('min_price', 'max_price', 'beds', 'min_sqft', 'min_lot', 'min_year', 'dom', 'page'):
        value = filters[key]
        if value and (not value.isascii() or not value.isdecimal() or len(value) > 9):
            raise ValueError('Enter a nonnegative whole number.')
        filters[key] = int(value) if value else None
    market = filters['market'] or 'sale'
    if market not in MARKET_LABELS:
        raise ValueError('Choose for sale, for rent or sold.')
    filters['market'] = market
    bath_count = filters['baths']
    if bath_count and not re.fullmatch(r'[0-9]{1,2}(?:\.[05])?', bath_count):
        raise ValueError('Enter a valid minimum bathroom count.')
    filters['baths'] = (float(bath_count) if bath_count.endswith('.5') else int(float(bath_count))) if bath_count else None
    if filters['dom'] not in (None, 45, 60):
        raise ValueError('Select an available days-on-market filter.')
    if filters['max_price'] is not None and (filters['min_price'] or 0) > filters['max_price']:
        raise ValueError('Maximum price must be at least the minimum price.')
    for key in ('reduced', 'auction', 'bank_owned', 'photos_only'):
        filters[key] = filters[key] == '1'
    filters['page'] = max(1, filters['page'] or 1)
    return filters


def errors_for(kind, data):
    required = ('name', 'channel', 'contact')
    if kind == 'partner':
        required = PARTNER_FIELDS
    elif kind == 'request-tour':
        required += ('preferred_time', 'window_minutes', 'representation')
    errors = {key: 'Enter a value.' for key in required if not data[key]}
    for key, value in data.items():
        maximum = 3000 if key in {'question', 'experience'} else 200
        if len(value) > maximum:
            errors[key] = f'Use {maximum:,} characters or fewer.'
    if data['channel'] not in {'email', 'phone'}:
        errors['channel'] = 'Choose email or phone.'
    elif data['channel'] == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', data['contact']):
        errors['contact'] = 'Enter a valid email address.'
    elif data['channel'] == 'phone' and (not re.fullmatch(r'[+0-9(). -]+', data['contact'])
                                           or not 10 <= len(re.sub(r'\D', '', data['contact'])) <= 15):
        errors['contact'] = 'Enter a phone number with area code.'
    if kind == 'partner' and not re.fullmatch(r'\d{8}', data['license'], re.ASCII):
        errors['license'] = 'Enter the eight-digit California DRE license number.'
    if kind == 'request-tour':
        if data['representation'] not in {'yes', 'no', 'unsure'}:
            errors['representation'] = 'Choose your representation status.'
        if data['window_minutes'] not in {'30', '60', '120'}:
            errors['window_minutes'] = 'Choose a window duration.'
        pacific = ZoneInfo('America/Los_Angeles')
        for key in ('preferred_time', 'alternative_time'):
            if data[key]:
                try:
                    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d', data[key]):
                        raise ValueError
                    if datetime.fromisoformat(data[key]).replace(tzinfo=pacific) <= datetime.now(pacific):
                        raise ValueError
                except ValueError:
                    errors[key] = 'Choose a future date and time (Pacific time).'
        if data['preferred_time'] and data['preferred_time'] == data['alternative_time']:
            errors['alternative_time'] = 'Choose a different alternative time.'
    return errors


def register_public(app, admin_required, validate_csrf):
    bp = Blueprint('buyer', __name__)
    mode = setting('BUYER_MODE', 'approved')
    if mode not in {'preview', 'approved'}:
        raise RuntimeError('Invalid BUYER_MODE')
    identity = identity_for(mode)
    if mode == 'approved' and (len(app.secret_key or '') < 32 or not app.config['SESSION_COOKIE_SECURE']):
        raise RuntimeError('Approved buyer mode requires a production secret and secure cookies')
    catalog = Catalog(setting('BUYER_CATALOG', str(DEFAULT_DATA / 'catalog.sqlite')))
    inbox = Inbox(setting('BUYER_SUBMISSIONS', str(DEFAULT_DATA / 'submissions.sqlite')))
    # Lazy initialization keeps legacy deployments and catalog-only requests independent of inbox disk.
    app.extensions['buyer_catalog'] = catalog
    app.extensions['buyer_inbox'] = inbox
    app.config.setdefault('MAX_CONTENT_LENGTH', 32 * 1024)

    @bp.app_context_processor
    def buyer_context():
        return {'buyer_mode': mode, 'buyer_identity': identity}

    @bp.after_request
    def control_cache(response):
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Street tiles receive only the site origin. Other buyer routes send no referrer.
        response.headers['Referrer-Policy'] = (
            'strict-origin' if request.endpoint == 'buyer.homes' else 'no-referrer'
        )
        if mode == 'preview':
            response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response

    def home_or_404(home_id):
        home = catalog.get(mode, home_id)
        if home is None:
            abort(404)
        return home

    def track(home, event, entry):
        try:
            inbox.initialize()
            inbox.event(mode, home, event, entry)
        except (OSError, sqlite3.Error):
            app.logger.warning('Buyer event storage unavailable')

    def filter_query(filters, *, include_page=False):
        return {
            key: 1 if value is True else value
            for key, value in filters.items()
            if value is not False and value not in ('', None, [])
            and (key != 'page' or (include_page and value > 1))
            and (key != 'market' or value != 'sale')
        }

    def listing_url(home_id, query):
        return url_for('buyer.detail', home_id=home_id, from_view='search', **query)

    @bp.get('/')
    def landing():
        _, cities, _, _ = catalog.search(mode, {}, 1, 0)
        return render_template('landing.html', cities=cities)

    @bp.get('/homes')
    def homes():
        error = None
        try:
            filters = search_parameters(request.args)
        except ValueError as exc:
            error = str(exc)
            filters = search_parameters({})
        page = filters['page']
        homes, cities, capabilities, count = catalog.search(mode, filters, page)
        query = filter_query(filters)
        link_query = filter_query(filters, include_page=True)
        links = {home['id']: listing_url(home['id'], link_query) for home in homes}
        map_url = url_for('buyer.map_data', **link_query) if count else None
        return render_template('buyer_search.html', homes=homes, cities=cities, capabilities=capabilities,
                               count=count, filters=filters, error=error, page=page, query=query,
                               last_page=max(1, (count + 11) // 12), links=links, map_url=map_url,
                               markets=MARKET_LABELS, property_groups=PROPERTY_GROUPS), 400 if error else 200

    @bp.get('/homes/property-type-count')
    def property_type_count():
        try:
            filters = search_parameters(request.args)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(count=catalog.count(mode, filters))

    @bp.get('/homes/map-data')
    def map_data():
        try:
            filters = search_parameters(request.args)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        link_query = filter_query(filters, include_page=True)
        return jsonify([
            {'point': point, 'label': label, 'price': price, 'url': listing_url(home_id, link_query)}
            for home_id, price, label, point in catalog.matching_locations(mode, filters)
        ])

    @bp.get('/homes/<home_id>')
    def detail(home_id):
        home = home_or_404(home_id)
        try:
            filters = search_parameters(request.args)
        except ValueError:
            filters = {}
        query = filter_query(filters, include_page=True)
        entry = request.args.get('from_view', '')
        track(home_id, 'view', entry if entry in {'search', 'homepage'} else 'detail')
        return render_template('buyer_detail.html', home=home, back_url=url_for('buyer.homes', **query),
                               analysis=detail_analysis(home, request.args))

    @bp.get('/homes/<home_id>/media/<media_id>')
    def media(home_id, media_id):
        asset = catalog.media(mode, home_id, media_id)
        if asset is None:
            abort(404)
        content, mime = asset
        if mime == 'application/x-mls-photo-reference':
            try:
                content = fetch_mls_photo(content.decode('utf-8'))
            except (ValueError, OSError):
                abort(404)
            if catalog.media(mode, home_id, media_id) is None:
                abort(404)
            mime = 'image/jpeg'
        return Response(content, mimetype=mime)

    def digest(value):
        return hmac.new(app.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()

    def handle_form(kind, home=None):
        if not identity:
            return render_template('buyer_unavailable.html'), 503
        error = None
        errors = {}
        home_id = home['id'] if home else ''
        entry = request.values.get('entry', 'detail' if home else 'navigation')
        if entry not in ({'detail', 'estimate'} if home else {'navigation'}):
            entry = 'detail' if home else 'navigation'
        session_key = session.setdefault('buyer_form_key', secrets.token_hex(16))
        fields = PARTNER_FIELDS if not home else CONTACT_FIELDS + (TOUR_FIELDS if kind == 'request-tour' else ())
        data = {key: request.form.get(key, '').strip() for key in fields}
        notice = (f"{identity['name']} will use these details to respond to your "
                  f"{'application' if not home else 'request'}. This form requests one response.")
        status = 200
        if request.method == 'POST':
            validate_csrf()
            token, _, signature = request.form.get('submission_id', '').partition('.')
            if not re.fullmatch(r'[a-f0-9]{32}', token) or not hmac.compare_digest(
                    signature, digest(f'{session_key}:{mode}:{kind}:{home_id}:{token}')):
                abort(400)
            errors = errors_for(kind, data)
            if errors:
                status = 422
            else:
                payload = {**data, 'entry': entry, 'notice': notice, 'notice_version': '2026-11-21.1',
                           'recipient': identity['recipient'], 'recipient_name': identity['name']}
                if home:
                    payload.update(home_id=home_id, home_label=home['location'],
                                   home_price=home['price'], time_zone='America/Los_Angeles')
                try:
                    inbox.initialize()
                    result = inbox.submit(token, kind, mode, digest(request.remote_addr or 'unknown'),
                                          session_key, payload, lambda: not home or bool(catalog.get(mode, home_id)))
                except (OSError, sqlite3.Error):
                    app.logger.warning('Buyer submission storage unavailable')
                    result = 'unavailable'
                if result == 'saved':
                    return render_template('buyer_receipt.html', kind=kind, reference=token,
                                           home=home), 201
                if result == 'withdrawn':
                    abort(404)
                if result == 'invalid':
                    abort(400)
                error = ('Please wait before sending another request.' if result == 'limited'
                         else 'We could not save your request. Your entries are here so you can try again.')
                status = 429 if result == 'limited' else 503
        else:
            token = secrets.token_hex(16)
            if home:
                track(home_id, f'{kind} started', entry)
                if kind == 'contact' and entry == 'estimate':
                    data['question'] = 'Can you help me verify the renovation budget?'
        submission_id = f'{token}.{digest(f"{session_key}:{mode}:{kind}:{home_id}:{token}")}'
        template = 'buyer_form.html' if home else 'buyer_partner.html'
        return render_template(template, home=home, kind=kind, error=error, errors=errors, entry=entry,
                               data=data, notice=notice, submission_id=submission_id), status

    @bp.route('/homes/<home_id>/<kind>', methods=['GET', 'POST'])
    def inquiry(home_id, kind):
        if kind not in {'contact', 'request-tour'}:
            abort(404)
        home = home_or_404(home_id)
        if home.get('market') == 'sold':
            abort(404)
        return handle_form(kind, home)

    @bp.route('/agents/partner', methods=['GET', 'POST'])
    def partner():
        return handle_form('partner')

    @bp.get('/admin/leads')
    @admin_required
    def leads():
        inbox.initialize()
        return render_template('buyer_inbox.html', title='Buyer inquiries', rows=inbox.queue(mode, False),
                               activity=inbox.activity(mode))

    @bp.get('/admin/partners')
    @admin_required
    def partners():
        inbox.initialize()
        return render_template('buyer_inbox.html', title='Partner applications', rows=inbox.queue(mode, True))

    @bp.route('/admin/submissions/<submission_id>', methods=['GET', 'POST'])
    @admin_required
    def record(submission_id):
        inbox.initialize()
        row = inbox.detail(mode, submission_id)
        if not row:
            abort(404)
        actions = PARTNER_ACTIONS if row['kind'] == 'partner' else LEAD_ACTIONS
        if request.method == 'POST':
            validate_csrf()
            action = request.form.get('status', '')
            if action == 'tour confirmed' and (request.form.get('access_verified') != 'yes' or row['kind'] != 'request-tour' or row['status'] not in
                                               {'tour requested', 'contacted', 'qualified conversation'}):
                abort(409, 'Verify access for a pending tour before confirming.')
            if action == 'tour held' and row['status'] != 'tour confirmed':
                abort(409, 'Confirm the showing before recording a held tour.')
            if not inbox.transition(mode, submission_id, action, actions, expected=row['status']):
                abort(409)
            return redirect(url_for('buyer.record', submission_id=submission_id), 303)
        return render_template('buyer_inbox.html', title='Submission details', record=row, actions=actions)

    app.register_blueprint(bp)
