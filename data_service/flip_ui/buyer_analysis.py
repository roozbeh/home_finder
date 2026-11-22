"""Deterministic, versioned buyer budgets and comparable evidence.

The publisher passes already cleared normalized records. These helpers return
display projections, never private reviewer notes, documents, or feed IDs.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from itertools import product
from math import asin, cos, radians, sin, sqrt

SOURCE_TYPES = {
    'model': 'Model estimate',
    'agent': 'Agent estimate',
    'benchmark': 'Regional benchmark',
    'contractor_bid': 'Contractor bid',
    'paid_invoice': 'Paid invoice',
}
EVIDENCE_TYPES = {
    'observed': 'Observed condition',
    'seller_reported': 'Seller-reported',
    'inspection': 'Inspection question',
}
CONFIDENCE = {'low', 'medium', 'high'}


def money(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def decimal(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Expected a finite number') from exc
    if not number.is_finite():
        raise ValueError('Expected a finite number')
    return number


def numeric(value):
    """JSON-safe and lossless for the decimal precisions used in this UI."""
    return int(value) if value == value.to_integral_value() else float(value)


def exact_date(value):
    if not isinstance(value, str) or len(value) < 10:
        raise ValueError('Use an ISO calendar date')
    return date.fromisoformat(value[:10])


def short_text(value, field, limit=180):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit or any(ord(ch) < 32 for ch in value):
        raise ValueError(f'Enter valid {field}')
    return value.strip()


def validate_scope(items, version, evidence_ids):
    """Validate all alternatives, including choices suppressed by the default."""
    version = short_text(version, 'budget version', 40)
    if not isinstance(items, list) or not items or len(items) > 150:
        raise ValueError('Enter one to 150 cost items or observations')
    defaults, options, notes, lines = {}, {}, [], []
    for item in items:
        room = short_text(item.get('room'), 'room', 60)
        task = short_text(item.get('task'), 'work or observation', 240)
        if item.get('evidence_type') in EVIDENCE_TYPES and 'unit_low' not in item:
            notes.append(dict(room=room, task=task, kind=EVIDENCE_TYPES[item['evidence_type']]))
            continue
        unit = short_text(item.get('unit'), 'unit', 40)
        source_type = item.get('source_type')
        confidence = item.get('confidence')
        if source_type not in SOURCE_TYPES or confidence not in CONFIDENCE:
            raise ValueError('Choose a source type and confidence')
        source = short_text(item.get('source'), 'public-safe source')
        geography = short_text(item.get('geography'), 'pricing geography', 80)
        source_date = exact_date(item.get('source_date')).isoformat()
        quantity = decimal(item.get('quantity'))
        unit_low, unit_high = decimal(item.get('unit_low')), decimal(item.get('unit_high'))
        if quantity <= 0 or quantity > 1_000_000 or not 0 <= unit_low <= unit_high <= 10_000_000:
            raise ValueError('Enter a positive quantity and an ordered unit price range')
        if quantity != quantity.quantize(Decimal('.0001')) or any(
            price != price.quantize(Decimal('.01')) for price in (unit_low, unit_high)
        ):
            raise ValueError('Use up to four quantity decimals and price amounts in cents')
        group, choice = item.get('alternative_group'), item.get('alternative')
        included = item.get('included')
        if type(included) is not bool:
            raise ValueError('Set inclusion on every priced line')
        evidence = item.get('evidence_photo_ids', [])
        if not isinstance(evidence, list) or any(not isinstance(photo, str) or not photo for photo in evidence):
            raise ValueError('Supply a list of evidence photo identifiers')
        if group:
            group = short_text(group, 'alternative group', 50)
            choice = short_text(choice, 'alternative ID', 50)
            label = short_text(item.get('alternative_label'), 'alternative label', 70)
            mapping = options.setdefault(group, {})
            if mapping.setdefault(choice, label) != label:
                raise ValueError('Each alternative must have one label')
            if included and defaults.setdefault(group, choice) != choice:
                raise ValueError('Select one default per alternative group')
        elif choice:
            raise ValueError('Each alternative needs a group')
        elif not included:
            notes.append(dict(room=room, task=task, kind='Separate allowance · excluded from budget',
                              low=numeric(quantity * unit_low), high=numeric(quantity * unit_high),
                              source_kind=SOURCE_TYPES[source_type], source=source, source_date=source_date,
                              geography=geography, confidence=confidence))
        lines.append({
            'room': room, 'task': task, 'quantity': numeric(quantity), 'unit': unit,
            'unit_low': numeric(unit_low), 'unit_high': numeric(unit_high),
            'source_kind': SOURCE_TYPES[source_type], 'source': source, 'source_date': source_date,
            'geography': geography, 'confidence': confidence, 'evidence_count': len(set(evidence).intersection(evidence_ids)),
            'low': quantity * unit_low, 'high': quantity * unit_high, 'group': group,
            'choice': choice, 'included': included,
        })
    if defaults.keys() != options.keys():
        raise ValueError('Select a default for every alternative group')
    if product_size(options) > 64:
        raise ValueError('Use no more than 64 combinations')
    return version, defaults, options, notes, lines


def product_size(options):
    count = 1
    for choices in options.values():
        count *= len(choices)
    return count


def subtotal(items, key):
    return sum((item[key] for item in items), Decimal(0))


def scopes(cost_items, version, partial_budget, address_ok=True, evidence_ids=()):
    # Even approved scope prose can contain clues unique to a property.
    if not address_ok:
        return None
    version, defaults, options, notes, lines = validate_scope(cost_items, version, frozenset(evidence_ids))
    budgets = []
    for combination in product(*options.values()):
        choices = dict(zip(options, combination))
        rooms = defaultdict(list)
        for item in lines:
            if (item['group'] and choices[item['group']] == item['choice']) or (
                not item['group'] and item['included']
            ):
                rooms[item['room']].append(item)
        room_rows = []
        for room, items in rooms.items():
            room_rows.append({
                'room': room,
                'low': numeric(subtotal(items, 'low')), 'high': numeric(subtotal(items, 'high')),
                'items': [
                    {**{key: item[key] for key in (
                        'task', 'quantity', 'unit', 'unit_low', 'unit_high', 'source_kind', 'source',
                        'source_date', 'geography', 'confidence', 'evidence_count',
                    )}, 'low': numeric(item['low']), 'high': numeric(item['high'])}
                    for item in items
                ],
            })
        selected = [item for items in rooms.values() for item in items]
        budgets.append({
            'choices': choices, 'low': numeric(subtotal(selected, 'low')) if selected else None,
            'high': numeric(subtotal(selected, 'high')) if selected else None, 'rooms': room_rows,
        })
    return {'version': version, 'partial': bool(partial_budget or not lines),
            'default': defaults, 'options': options, 'budgets': budgets, 'notes': notes}


UNKNOWN_FEES = (
    ('Insurance', 'Hold'), ('HOA', 'Hold'), ('Utilities', 'Hold'),
    ('Renovation financing', 'Financing'), ('Contingency', 'Renovation'),
    ('Staging', 'Resale'), ('Marketing', 'Resale'), ('Photography', 'Resale'),
    ('Notary', 'Settlement'), ('Attorney', 'Settlement'),
    ('Title and escrow', 'Settlement'), ('Transfer costs', 'Settlement'),
)


def purchase_scenario(asking, resale, renovation_low, renovation_high, target=0, assumptions=None):
    """Reconcile a scenario against its cost ledger with explicit unpriced items."""
    assumptions = assumptions or {}
    asking, resale = decimal(asking), decimal(resale)
    low, high, target = map(decimal, (renovation_low, renovation_high, target))
    months = decimal(assumptions.get('months', 10))
    finance = decimal(assumptions.get('annual_interest', '.10')) * months / 12
    tax = decimal(assumptions.get('annual_tax', '.011')) * months / 12
    buy = decimal(assumptions.get('purchase_fee', '.01'))
    sell = decimal(assumptions.get('sale_fee', '.065'))
    work = (low + high) / 2
    if not (asking > 0 and resale > 0 and 0 <= low <= high and target >= 0 and months > 0
            and min(finance, tax, buy) >= 0 and 0 <= sell < 1):
        raise ValueError('Scenario inputs are invalid')
    ledger = []
    for label, category, amount, basis in (
        ('Asking price', 'Purchase', asking, 'Published asking price'),
        ('Purchase closing bundle', 'Purchase', asking * buy, f'{buy * 100:g}% of purchase'),
        ('Acquisition loan interest', 'Financing', asking * finance,
         f'{decimal(assumptions.get("annual_interest", ".10")) * 100:g}% annual simple interest on full purchase price, {months:g} months'),
        ('Property tax', 'Hold', asking * tax,
         f'{decimal(assumptions.get("annual_tax", ".011")) * 100:g}% annual tax on purchase price, {months:g} months'),
        ('Selected renovation work', 'Renovation', work, 'Midpoint of the selected scope'),
        ('Resale closing bundle', 'Resale', resale * sell, f'{sell * 100:g}% of resale'),
    ):
        ledger.append(dict(label=label, category=category, amount=money(amount), basis=basis))
    extras = assumptions.get('extras', {})
    extra_total = Decimal(0)
    for label, category in UNKNOWN_FEES:
        value = extras.get(label)
        if value in ('purchase_bundle', 'resale_bundle'):
            basis = 'Included in the ' + ('purchase' if value == 'purchase_bundle' else 'resale') + ' closing bundle'
            amount = None
        elif value is None:
            basis, amount = 'Unknown', None
        else:
            amount = decimal(value)
            if amount < 0:
                raise ValueError('Supplemental costs cannot be negative')
            extra_total += amount
            basis = 'Separate scenario input'
        ledger.append(dict(label=label, category=category,
                           amount=money(amount) if amount is not None else None, basis=basis))
    multiple = 1 + buy + finance + tax
    available = resale * (1 - sell) - work - extra_total
    net_at_asking = available - asking * multiple
    displayed_cost = sum(line['amount'] or 0 for line in ledger)
    reconciled_cost = money(resale - net_at_asking)
    if displayed_cost != reconciled_cost:
        ledger.append(dict(label='Whole-dollar rounding', category='Settlement',
                           amount=reconciled_cost - displayed_cost, basis='Reconciles rounded ledger entries'))
    return dict(
        version='buyer-ledger-1', resale=money(resale), renovation=money(work), asking=money(asking),
        result=money(net_at_asking), total_cost=reconciled_cost, break_even=money(available / multiple),
        target=money(target), target_offer=money((available - target) / multiple), ledger=ledger,
        incomplete=any(line['basis'] == 'Unknown' for line in ledger),
    )


def distance_miles(subject, candidate):
    """Calculate subject-relative distance when usable coordinates exist."""
    if subject.get('_location_visible') is False or candidate.get('_location_visible') is False:
        return None
    a, b = subject.get('coordinates'), candidate.get('coordinates')
    if not a or not b:
        supplied = candidate.get('distance_miles')
        if supplied is not None and candidate.get('subject_id') == subject.get('id'):
            distance = float(supplied)
            return distance if 0 <= distance <= 100 else None
        return None
    lat_a, lon_a = map(radians, a)
    lat_b, lon_b = map(radians, b)
    haversine = sin((lat_a - lat_b) / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin((lon_a - lon_b) / 2) ** 2
    return 3958.8 * 2 * asin(sqrt(min(1.0, haversine)))


def matching_sales(subject, candidates, as_of=None):
    if not subject.get('property_type') or not subject.get('sqft'):
        return []
    if subject.get('_location_visible') is False:
        return []
    as_of = as_of or (exact_date(subject['analysis_date']) if subject.get('analysis_date') else date.today())
    ranked = []
    for candidate in candidates:
        if candidate.get('withdrawn') or candidate.get('property_type') != subject['property_type']:
            continue
        if subject.get('parcel_id') and subject['parcel_id'] == candidate.get('parcel_id'):
            continue
        if type(candidate.get('price')) is not int or candidate['price'] <= 0:
            continue
        try:
            closed = exact_date(candidate['sale_date'])
            available = exact_date(candidate.get('available_date', candidate['sale_date']))
            age = (as_of - closed).days
            size = abs(candidate['sqft'] / subject['sqft'] - 1)
            distance = distance_miles(subject, candidate)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not 0 <= age <= 730 or not closed <= available <= as_of or not 0 <= size <= .25:
            continue
        sbeds, cbeds = subject.get('beds'), candidate.get('beds')
        if isinstance(sbeds, int) and isinstance(cbeds, int) and abs(sbeds - cbeds) > 1:
            continue
        if distance is None or distance > 3:
            continue
        finished = candidate.get('finish') in {'updated', 'renovated'}
        score = 2 * size + age / 1460 + distance / 3 + (.5 if not finished else 0)
        ranked.append((score, closed, str(candidate.get('id', '')), distance, size, finished, candidate))
    return sorted(ranked, key=lambda row: row[:3])


def match_projection(ranked):
    _, closed, _, distance, size, finished, row = ranked
    labels = ['Same property type', f'{size:.0%} size difference']
    if finished:
        labels.append('Updated finish')
    return {'sale_date': closed.isoformat(), 'sqft': row['sqft'], 'distance': round(distance, 2),
            'price': row['price'], 'reason': ' · '.join(labels), 'expires': row.get('expires')}


def comparable_sales(subject, sales, as_of=None):
    """Display the actual resale inputs. Favor documented renovated finishes."""
    ranked = [item for item in matching_sales(subject, sales, as_of) if item[5]]
    return [match_projection(item) for item in ranked[:5]]


def project_matches(subject, projects, as_of=None):
    """Observed paired transactions are separate from verified flip economics."""
    result = []
    for ranked in matching_sales(subject, projects, as_of):
        row = ranked[-1]
        if row.get('pair_verified') is not True or type(row.get('purchase_price')) is not int or row['purchase_price'] <= 0:
            continue
        try:
            days = (ranked[1] - exact_date(row.get('purchase_date'))).days
        except ValueError:
            continue
        if days <= 0:
            continue
        projection = match_projection(ranked)
        spread = row['price'] - row['purchase_price']
        projection.update(
            purchase_price=row['purchase_price'], hold_days=days, gross_spread=spread,
            flip_status='Verified renovation project' if row.get('flip_verified') is True else 'Flip status unverified',
            net_result=None, net_basis='Project spending unavailable',
        )
        costs = row.get('verified_costs', {})
        if (row.get('flip_verified') is True and row.get('verified_costs_public') is True
                and costs.get('basis') == 'paid_invoice' and all(
                    type(costs.get(key)) is int and costs[key] >= 0
                    for key in ('renovation', 'financing', 'hold', 'purchase_fees', 'resale_fees')
                )):
            projection['net_result'] = spread - sum(costs[key] for key in (
                'renovation', 'financing', 'hold', 'purchase_fees', 'resale_fees',
            ))
            projection['net_basis'] = 'Documented transaction costs and paid invoices'
        result.append(projection)
        if len(result) == 3:
            break
    return result


def detail_analysis(home, args):
    """Select one server-reconciled budget from the published options."""
    allowed_scopes = home.get('scopes')
    if not allowed_scopes:
        return {}
    selection, errors = {}, []
    for index, (group, choices) in enumerate(allowed_scopes['options'].items()):
        choice = args.get(f'scope_{index}', allowed_scopes['default'][group])
        if choice not in choices:
            errors.append('Choose one of the available scopes.')
            choice = allowed_scopes['default'][group]
        selection[group] = choice
    budget = next(budget for budget in allowed_scopes['budgets'] if budget['choices'] == selection)
    result = {'budget': budget, 'selection': selection, 'errors': errors}
    if home.get('resale') and budget['low'] is not None:
        try:
            target = int(args.get('profit_target', '25000'))
            if not 0 <= target <= 10_000_000:
                raise ValueError
        except ValueError:
            errors.append('Enter a whole-dollar profit target between $0 and $10 million.')
            target = 25_000
        result['scenario'] = purchase_scenario(home['price'], home['resale'], budget['low'], budget['high'], target)
    return result
