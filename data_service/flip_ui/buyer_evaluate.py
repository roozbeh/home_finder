"""Replay forecasts against parcel-separated, later sales with dated evidence."""
import argparse
import json
from pathlib import Path
from statistics import mean, median

from buyer_analysis import comparable_sales, exact_date, project_matches, purchase_scenario


def forecast(mid, low, high, renovation, model):
    return {'mid': mid, 'low': low, 'high': high, 'renovation': renovation, 'model': model}


def adjustment(price, sale_sqft, subject_sqft):
    # Size changes value less than proportionally; apply a documented, simple baseline.
    return round(price * (1 + .5 * (subject_sqft / sale_sqft - 1)))


def replay(case, sales, projects, cutoff, test_parcels):
    subject, as_of = case['subject'], exact_date(case['forecast_date'])
    if as_of < cutoff:
        return None
    current = case.get('saved_estimator')
    if (not current or not current.get('evidence_dates')
            or max(map(exact_date, current['evidence_dates'])) > as_of
            or exact_date(current['saved_at']) > as_of
            or 'training_parcels' not in current
            or test_parcels.intersection(current['training_parcels'])):
        return None
    if not 0 < current['low'] <= current['mid'] <= current['high'] or current['renovation'] < 0:
        return None
    def eligible(row):
        return (bool(row.get('parcel_id')) and row['parcel_id'] not in test_parcels
                and exact_date(row['available_date']) < cutoff
                and exact_date(row['sale_date']) < cutoff)
    selected = comparable_sales(subject, [row for row in sales if eligible(row)], as_of)
    if len(selected) < 2:
        return None
    values = [adjustment(sale['price'], sale['sqft'], subject['sqft']) for sale in selected]
    baseline = forecast(round(median(values)), round(min(values) * .95), round(max(values) * 1.05),
                        current['renovation'], 'adjusted comps')
    paired = project_matches(subject, [row for row in projects if eligible(row) and row.get('flip_verified') is True], as_of)
    if len(paired) < 2:
        return None
    project_values = [adjustment(row['price'], row['sqft'], subject['sqft']) for row in paired]
    history_values = values + project_values
    enhanced = forecast(round(median(history_values)), round(min(history_values) * .95),
                        round(max(history_values) * 1.05), current['renovation'], 'comps and verified projects')
    # Only invoices observed during training can change the history-based work estimate.
    invoices = [
        row['verified_costs']['renovation']
        for row in projects
        if eligible(row) and row.get('flip_verified') is True and row.get('pair_verified') is True
        and row['property_type'] == subject['property_type']
        and .75 <= row['sqft'] / subject['sqft'] <= 1.25
        and row.get('verified_costs', {}).get('basis') == 'paid_invoice'
        and type(row['verified_costs'].get('renovation')) is int
        and row['verified_costs']['renovation'] >= 0
        and row['verified_costs'].get('available_date')
        and exact_date(row['verified_costs']['available_date']) < cutoff
    ]
    if len(invoices) >= 2:
        enhanced['renovation'] = round(median(invoices))
    return {'saved_estimator': current, 'adjusted_comps': baseline, 'history_enhanced': enhanced}


def evaluate(cases, sales, projects, cutoff, through):
    cutoff, through = exact_date(cutoff), exact_date(through)
    if through <= cutoff:
        raise ValueError('Evaluation end date must follow the holdout date')
    candidates = [case for case in cases if cutoff <= exact_date(case['forecast_date']) <= through]
    parcel_groups = {case['subject']['parcel_id'] for case in candidates}
    grouped = {}
    for case in sorted(candidates, key=lambda item: exact_date(item['forecast_date'])):
        grouped.setdefault(case['subject']['parcel_id'], case)
    results = {}
    open_cases, unsupported = 0, 0
    for case in grouped.values():
        actual = case.get('actual') or {}
        outcome_date = actual.get('sale_date')
        if not outcome_date or exact_date(outcome_date) > through:
            open_cases += 1
            continue
        if exact_date(outcome_date) <= exact_date(case['forecast_date']):
            raise ValueError('An outcome must close after its forecast date')
        predictions = replay(case, sales, projects, cutoff, parcel_groups)
        if predictions is None:
            unsupported += 1
            continue
        for method, prediction in predictions.items():
            buckets = results.setdefault(method, {
                'resale_error': [], 'in_range': [], 'false_profitable': [],
                'renovation_error': [], 'profit_without_verified_outcome': 0,
            })
            price = actual['sale_price']
            buckets['resale_error'].append(abs(price - prediction['mid']))
            buckets['in_range'].append(prediction['low'] <= price <= prediction['high'])
            spending = actual.get('verified_costs', {})
            if (spending.get('basis') == 'paid_invoice' and type(spending.get('renovation')) is int
                    and exact_date(spending['available_date']) <= through):
                buckets['renovation_error'].append(abs(prediction['renovation'] - spending['renovation']))
            estimate = purchase_scenario(case['subject']['price'], prediction['mid'],
                                         prediction['renovation'], prediction['renovation'])
            if estimate['result'] > 0:
                if type(actual.get('documented_net')) is int and exact_date(actual['net_available_date']) <= through:
                    buckets['false_profitable'].append(actual['documented_net'] < 0)
                else:
                    buckets['profit_without_verified_outcome'] += 1
    metrics = {}
    for method, buckets in results.items():
        metrics[method] = {
            'matched_resales': len(buckets['resale_error']),
            'resale_mae': round(mean(buckets['resale_error']), 2),
            'range_coverage': round(mean(buckets['in_range']), 4),
            'profitable_calls_with_verified_outcome': len(buckets['false_profitable']),
            'false_profitable_calls': sum(buckets['false_profitable']),
            'profitable_calls_without_verified_outcome': buckets['profit_without_verified_outcome'],
            'invoice_backed_cases': len(buckets['renovation_error']),
            'renovation_mae': round(mean(buckets['renovation_error']), 2) if buckets['renovation_error'] else None,
        }
    return {
        'cutoff': cutoff.isoformat(), 'through': through.isoformat(), 'candidate_parcels': len(parcel_groups),
        'same_parcel_repeats_grouped': len(candidates) - len(parcel_groups), 'unresolved_or_unsold': open_cases,
        'closed_without_common_forecast': unsupported, 'metrics': metrics,
    }


def demo_dataset():
    def candidate(index, price, sqft, lat, sale_date, **extra):
        return dict(id=f'historical-{index}', parcel_id=f'historical-{index}', price=price, sqft=sqft,
                    property_type='Single family', beds=3, coordinates=[lat, -122.11], finish='renovated',
                    sale_date=sale_date, available_date=sale_date, **extra)

    sales = [
        candidate(1, 540_000, 1430, 37.512, '2026-01-12'),
        candidate(2, 568_000, 1490, 37.517, '2026-02-05'),
        candidate(9, 1_990_000, 1426, 37.515, '2026-11-15'),
    ]
    projects = [
        candidate(3, 585_000, 1445, 37.516, '2026-02-04', pair_verified=True, flip_verified=True,
                  purchase_price=430_000, purchase_date='2025-11-06',
                  verified_costs={'basis': 'paid_invoice', 'renovation': 45_000, 'available_date': '2026-03-01'}),
        candidate(4, 569_000, 1390, 37.522, '2026-01-19', pair_verified=True, flip_verified=True,
                  purchase_price=425_000, purchase_date='2025-10-12',
                  verified_costs={'basis': 'paid_invoice', 'renovation': 51_000, 'available_date': '2026-02-10'}),
    ]
    cases = []
    for i, (date, sale) in enumerate((('2026-09-01', 545_000), ('2026-09-11', 569_000), ('2026-09-20', None))):
        case = {
            'subject': {'parcel_id': f'forecast-{i}', 'price': 390_000 + i * 10_000,
                        'property_type': 'Single family', 'sqft': 1426 + i * 8, 'beds': 3,
                        'coordinates': [37.515, -122.11]},
            'forecast_date': date,
            'saved_estimator': {'mid': 571_000 - i * 10_000, 'low': 550_000, 'high': 592_000,
                                'renovation': 37_000, 'saved_at': date, 'evidence_dates': ['2026-02-05'],
                                'training_parcels': ['historical-1', 'historical-2']},
        }
        if sale:
            case['actual'] = {
                'sale_date': '2026-11-03', 'sale_price': sale, 'documented_net': -10_000 + i * 20_000,
                'net_available_date': '2026-11-15',
                'verified_costs': {'basis': 'paid_invoice', 'renovation': 46_000 + i * 3000,
                                   'available_date': '2026-11-15'},
            }
        cases.append(case)
    cases.append({**cases[0], 'forecast_date': '2026-09-08'})
    return {'cases': cases, 'sales': sales, 'projects': projects, 'cutoff': '2026-07-01', 'through': '2026-11-21'}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('dataset', type=Path, nargs='?')
    args = cli.parse_args()
    report = evaluate(**(json.loads(args.dataset.read_text()) if args.dataset else demo_dataset()))
    report['fixture'] = args.dataset is None
    print(json.dumps(report, indent=2))
