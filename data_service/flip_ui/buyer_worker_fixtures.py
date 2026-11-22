"""Generate a normalized synthetic inventory snapshot for whole-gallery QA."""
from copy import deepcopy
import json


def gallery_fixture(photo_count=70):
    policy = {
        'basis': 'Offline test fixture',
        'effective': '2020-01-01T00:00:00+00:00',
        'refresh_by': '2099-01-01T00:00:00+00:00',
        'allow': {'internet': True, 'price': True, 'ai': True, 'derived': True},
    }
    return {
        'mode': 'preview', 'sources': {'synthetic': policy},
        'homes': [{
            'id': 'gallery70_test', 'county': 'Solano', 'source': 'synthetic',
            'status': 'Active', 'policy': deepcopy(policy), 'price': 460_000,
            'property_type': 'House', 'analysis_inputs': {
                'comps_version': 'sales-1', 'comp_prices': [530_000, 560_000],
                'price_book_version': 'prices-1', 'price_book': {'Kitchen': {
                    'task': 'Refresh existing kitchen finishes', 'quantity': 1,
                    'unit': 'room', 'unit_low': 8_000, 'unit_high': 12_000,
                    'source_type': 'benchmark', 'source': 'Synthetic pricing exercise',
                    'source_date': '2026-11-21', 'geography': 'Solano County',
                    'confidence': 'low', 'included': True,
                }},
            },
            'media': [
                {'image_version': f'content-{i}', 'process': True, 'fixture_category': 'Kitchen',
                 'fixture_failure': 'once' if i == 3 else 'unavailable' if i == 4 else None}
                for i in range(photo_count)
            ],
        }],
    }


def demo_inventory():
    inventory = gallery_fixture()
    ready, missing, thin = (deepcopy(inventory['homes'][0]) for _ in range(3))
    ready.update(id='fixture_complete', county='Alameda', media=ready['media'][:3])
    missing.update(id='fixture_nophoto', county='San Mateo', media=[])
    thin.update(id='fixture_thincomps', county='Sonoma', media=thin['media'][:3])
    thin['analysis_inputs']['comp_prices'] = [530_000]
    inventory['homes'].extend((ready, missing, thin))
    return inventory


if __name__ == '__main__':
    print(json.dumps(demo_inventory()))
