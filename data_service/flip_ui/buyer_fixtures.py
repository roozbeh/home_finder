"""Fictional property examples. Media were generated for this preview."""
from pathlib import Path

from buyer_analysis_fixtures import cost_items, evidence_fixture
from buyer_publish import FIELDS

FAIRFIELD = 'r4q7w3n6fa1p'
PENDING = 'h6f9v8n2s7mb'
SUPPRESSED = 'c5n1h3q6vk8t'
LIMITED = 'z2k7f6d4ms8q'
PRIVATE = 'PRIVATE-FIELD-CANARY'


def fixture_manifest(asset_dir=None):
    flags = {key: True for key in (*FIELDS, 'internet')}
    grant = {'allow': flags, 'effective': '2020-01-01T00:00:00+00:00',
             'refresh_by': '2099-01-01T00:00:00+00:00', 'basis': 'Fictional preview data'}
    common = {'source': 'fixture', 'policy': grant, 'status': 'Active', 'beds': 3, 'baths': 2,
              'property_type': 'Single family', 'updated': '2026-11-21', 'sqft': 1426,
              'credit': {'source': 'Fictional listing feed', 'brokerage': 'Sample Listing Brokerage',
                         'agent': 'Sample Listing Agent', 'private_email': PRIVATE},
              'PRIVATE_REMARKS': PRIVATE, 'SHOWING_INSTRUCTIONS': PRIVATE,
              'UNEXPECTED_MLS_COLUMN': PRIVATE}
    photos = [{'display': True, 'sanitized_file': 'ranch.jpg'}]
    if asset_dir and (Path(asset_dir) / 'kitchen.jpg').is_file():
        photos.append({'display': True, 'sanitized_file': 'kitchen.jpg', 'address_safe': True})
    sales, projects = evidence_fixture(FAIRFIELD)
    return {'mode': 'preview', 'sources': {'fixture': grant}, 'sales': [
                {**sale, 'source': 'fixture', 'policy': grant, 'street': PRIVATE} for sale in sales
            ], 'projects': [{**project, 'source': 'fixture', 'policy': grant} for project in projects],
            'homes': [
                {**common, 'id': FAIRFIELD, 'city': 'Fairfield', 'county': 'Solano', 'price': 460000,
                 'address': 'Example Ranch · Fairfield', 'coordinates': [38.247, -122.037],
                 'analysis_date': '2026-11-21', 'days_on_market': 65, 'original_price': 479000,
                 'auction': False, 'bank_owned': False, 'media': photos, 'analysis_origin': 'reviewer',
                 'cost_items': cost_items(), 'cost_dependency_free': True, 'cost_version': 'fairfield-synthetic-v1', 'partial_budget': True},
                {**common, 'id': PENDING, 'city': 'San Mateo', 'county': 'San Mateo', 'price': 925000,
                 'address': 'Example Bungalow · San Mateo', 'coordinates': [37.58, -122.32],
                 'beds': 2, 'baths': 1, 'sqft': 1120, 'days_on_market': 18, 'auction': False,
                 'bank_owned': False},
                {**common, 'id': SUPPRESSED, 'city': 'Oakland', 'county': 'Alameda', 'price': 685000,
                 'address': PRIVATE + '-ADDRESS', 'coordinates': [37.77777, -122.22222],
                 'reviewed_public_remarks': PRIVATE, 'original_price': 710000, 'days_on_market': 51,
                 'bank_owned': True, 'media': photos[:1],
                 'policy': {**grant, 'allow': {**flags, 'address': False, 'valuation': False}}},
                {**common, 'id': LIMITED, 'city': 'Richmond', 'county': 'Contra Costa', 'price': 575000,
                 'address': 'Example Cottage · Richmond', 'coordinates': [37.936, -122.341],
                 'days_on_market': 46, 'auction': True, 'media': photos,
                 'policy': {**grant, 'allow': {**flags, 'media': False}}},
            ]}
