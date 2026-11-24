"""Photo-derived quantities and independent renovation area totals."""
from cost_backfill import DEFAULT_RATES, area_photos, load_rates, price_scope, validate_scope


def area_result(area, code, kind, low, high, photo_id):
    photo = {'id': photo_id, 'category': area}
    scope = validate_scope({
        'condition': 'Most finishes can stay',
        'tasks': [{'code': code, 'photo_ids': [photo_id], 'evidence': 'Visible wear',
                   'work': 'Replace worn material only', 'quantity_kind': kind,
                   'quantity_low': low, 'quantity_high': high,
                   'quantity_basis': 'Count or scale from visible fixtures'}],
        'unknown': [],
    }, area, [photo], {'sqft': 1400})
    return {**scope, 'incomplete_coverage': False}


def test_independent_area_estimates_reconcile():
    book = load_rates(DEFAULT_RATES)
    bathroom = area_result('bathroom', 'replace_toilet', 'visible_count', 1, 1, 1)
    kitchen = area_result('kitchen', 'replace_lvp_flooring', 'photo_allowance', 80, 120, 2)
    result = price_scope({'bathroom': bathroom, 'kitchen': kitchen}, book, evidence_incomplete=False)
    assert result['state'] == 'partial'
    assert result['area_estimates']['bathroom']['low'] == 380
    assert result['area_estimates']['kitchen']['high'] == 120 * 15
    assert result['renovation_low'] == 380 + 80 * 9
    assert result['renovation_high'] == 1150 + 120 * 15
    assert all(item['photo_ids'] and item['source'] for item in result['observations'])


def test_unknown_floor_area_stays_unpriced_and_preserved():
    book = load_rates(DEFAULT_RATES)
    scope = area_result('living_room', 'refinish_hardwood', 'unknown', None, None, 3)
    result = price_scope({'living_room': scope}, book, evidence_incomplete=False)
    assert result['state'] == 'partial'
    assert result['renovation_low'] is None
    assert result['observations'][0]['unpriced'] == 'Quantity needs verification'


def test_groups_do_not_share_photo_references():
    groups = area_photos([{'id': 1, 'category': 'kitchen'}, {'id': 2, 'category': 'garage'}])
    assert {'garage': [2], 'kitchen': [1]} == {
        area: [photo['id'] for photo in photos] for area, photos in groups.items()
    }
