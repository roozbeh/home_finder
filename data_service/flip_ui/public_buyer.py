"""Public listing routes backed by the canonical Atlas inventory."""
import os
import re

from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for

from buyer_analysis import detail_analysis
from pymongo.errors import PyMongoError

from buyer_atlas import AtlasCatalog
from buyer_media import fetch_mls_photo
from buyer_property_types import PROPERTY_GROUPS, normalized, type_filter
SEARCH_FIELDS = ('city', 'market', 'min_price', 'max_price', 'beds', 'baths', 'property_type',
                 'min_sqft', 'min_lot', 'min_year', 'dom', 'reduced', 'auction', 'bank_owned',
                 'photos_only', 'photo_categories', 'purchase_recommendation', 'good_investment', 'page')
MARKET_LABELS = {'sale': 'For sale', 'rent': 'For rent', 'sold': 'Sold'}


def setting(name, default=''):
    return os.environ.get(name, default).strip()


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
    for key in ('photo_categories', 'purchase_recommendation', 'good_investment'):
        if filters[key] not in ('', 'yes', 'no'):
            raise ValueError('Choose Any, Yes or No for each FlipFinder filter.')
    filters['page'] = max(1, filters['page'] or 1)
    return filters


def register_public(app):
    bp = Blueprint('buyer', __name__)
    mode = 'approved'
    catalog = None
    noindex = setting('BUYER_NOINDEX') == 'true'
    policy = setting('BUYER_ATLAS_POLICY')
    uri = setting('BUYER_ATLAS_URI')
    if policy or uri:
        if len(app.secret_key or '') < 32 or not app.config['SESSION_COOKIE_SECURE']:
            raise RuntimeError('Public buyer routes require a production secret and secure cookies')
        catalog = AtlasCatalog.from_environment(uri, policy, setting('BUYER_PUBLIC_ID_KEY'))

    @bp.errorhandler(PyMongoError)
    def atlas_unavailable(error):
        app.logger.warning('Atlas property read failed: %s', type(error).__name__)
        return 'Property listings are temporarily unavailable.', 503

    @bp.before_request
    def require_catalog():
        if catalog is None and request.endpoint not in {'buyer.landing', 'buyer.inquiry', 'buyer.partner'}:
            abort(503)

    @bp.after_request
    def control_cache(response):
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Street tiles receive only the site origin. Other buyer routes send no referrer.
        response.headers['Referrer-Policy'] = (
            'strict-origin' if request.endpoint == 'buyer.homes' else 'no-referrer'
        )
        if noindex:
            response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response

    def home_or_404(home_id):
        home = catalog.get(mode, home_id)
        if home is None:
            abort(404)
        return home

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
        cities = catalog.search(mode, {}, 1, 0)[1] if catalog else []
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

    @bp.get('/homes/<home_id>/map-preview')
    def map_preview(home_id):
        try:
            filters = search_parameters(request.args)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        home = catalog.map_home(mode, filters, home_id)
        if home is None:
            abort(404)
        photo = url_for('buyer.media', home_id=home_id, media_id=home['photos'][0]) if home['photos'] else None
        return jsonify(label=home['location'], locality=home.get('locality'), price=home['price'],
                       market=home.get('market', 'sale'), photo=photo, fictional=home.get('preview', False),
                       **({'status': home.get('status'), 'last_checked': home['updated']}
                          if home.get('inventory_stale') else {}),
                       url=listing_url(home_id, filter_query(filters, include_page=True)),
                       facts={key: home[key] for key in ('beds', 'baths', 'sqft', 'property_type', 'days_on_market')
                              if key in home})

    @bp.get('/homes/<home_id>')
    def detail(home_id):
        home = home_or_404(home_id)
        try:
            filters = search_parameters(request.args)
        except ValueError:
            filters = {}
        query = filter_query(filters, include_page=True)
        return render_template('buyer_detail.html', home=home, back_url=url_for('buyer.homes', **query),
                               analysis=detail_analysis(home, request.args), recommendation=home.get('recommendation'))

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

    @bp.route('/homes/<home_id>/<kind>', methods=['GET', 'POST'])
    def inquiry(home_id, kind):
        if kind not in {'contact', 'request-tour'}:
            abort(404)
        abort(410)

    @bp.route('/agents/partner', methods=['GET', 'POST'])
    def partner():
        abort(410)

    app.register_blueprint(bp)
