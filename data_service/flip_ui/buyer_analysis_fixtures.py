"""Private dated Fairfield arithmetic and fictional public equivalents."""
def cost_items(geography='Fairfield, CA'):
    model = {
        'source_type': 'model', 'source': 'Dated November 2026 assessment',
        'source_date': '2026-11-21', 'geography': geography, 'confidence': 'low',
        'included': True,
    }
    return [
        {**model, 'room': 'Bathrooms', 'task': 'Cosmetic refresh of the existing finish',
         'quantity': 2, 'unit': 'bath', 'unit_low': 4000, 'unit_high': 8000, 'evidence_photo_ids': ['15', '21'],
         'alternative_group': 'bathrooms', 'alternative': 'cosmetic', 'alternative_label': 'Cosmetic refresh'},
        {**model, 'room': 'Bathrooms', 'task': 'Full bath remodel',
         'quantity': 2, 'unit': 'bath', 'unit_low': 25_000, 'unit_high': 40_000, 'evidence_photo_ids': ['15', '21'],
         'source_type': 'agent', 'source': 'November 19 agent interview, regional scope check',
         'source_date': '2026-11-19', 'included': False, 'alternative_group': 'bathrooms',
         'alternative': 'full', 'alternative_label': 'Full remodel'},
        {**model, 'room': 'Bathrooms', 'task': 'Vanity component', 'quantity': 1, 'unit': 'vanity',
         'unit_low': 2000, 'unit_high': 4000, 'evidence_photo_ids': ['15'],
         'included': False},
        {**model, 'room': 'Kitchen', 'task': 'Cabinetry allowance', 'quantity': 1, 'unit': 'allowance',
         'unit_low': 8000, 'unit_high': 15_000, 'evidence_photo_ids': ['5', '6', '7', '8']},
        {**model, 'room': 'Roof', 'task': 'Provisional roof allowance',
         'quantity': 1, 'unit': 'allowance', 'unit_low': 8500, 'unit_high': 12_000},
        {'room': 'Roof', 'task': 'Seller reports replacement is needed. Request a roofer’s quote.',
         'evidence_type': 'seller_reported'},
        {'room': 'Systems', 'task': 'Confirm remaining system life and any concealed work during inspection.',
         'evidence_type': 'inspection'},
        {**model, 'room': 'Other dated allowances', 'task': 'Residual dated allowance pending itemized review',
         'quantity': 1, 'unit': 'allowance', 'unit_low': 12_250, 'unit_high': 20_250},
    ]


def evidence_fixture(subject_id='preview-fairfield', *, cases_for=None):
    source = dict(city='Fairfield', property_type='Single family', sqft=1426, beds=3,
                  finish='renovated', subject_id=subject_id,
                  sale_date='2026-10-15', expires='2099-01-01T00:00:00+00:00')
    if cases_for:
        source['sale_date'] = cases_for
    sales = [
        {**source, 'id': 'preview-closed-1', 'distance_miles': .33, 'price': 530_000},
        {**source, 'id': 'preview-closed-2', 'distance_miles': .27, 'price': 560_000},
        {**source, 'id': 'preview-unit-3', 'distance_miles': .10, 'price': 390_000, 'sqft': 662, 'beds': 1},
    ]
    projects = [
        {**source, 'id': 'preview-project-1', 'distance_miles': .36, 'price': 565_000, 'sqft': 1474,
         'purchase_date': '2026-07-30', 'purchase_price': 415_000, 'pair_verified': True,
         'flip_verified': False},
    ]
    return sales, projects
