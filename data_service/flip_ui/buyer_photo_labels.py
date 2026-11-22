"""Public photo category names and lookups for published MLS galleries."""
import hashlib
import json
from itertools import islice

CATEGORIES = {
    'exterior': 'Exterior', 'kitchen': 'Kitchen', 'bathroom': 'Bathrooms',
    'bedroom': 'Bedrooms', 'living_room': 'Living areas', 'dining_room': 'Dining',
    'laundry': 'Laundry', 'garage': 'Parking and garage', 'other': 'Other',
    'hallway': 'Hallways', 'outdoor': 'Outdoor spaces', 'amenities': 'Building amenities',
    'view': 'Views', 'neighborhood': 'Neighborhood', 'floor_plan': 'Floor plans',
}

BULK_MODEL = 'moondream:latest'
BULK_METHOD = 'moondream_caption_rules_v1'


def gallery_category(labels, url, aliases):
    categories = [labels.get(reference) for reference in (url, *aliases)]
    if 'floor_plan' in categories:
        return 'floor_plan'
    return next((category for category in categories if category in CATEGORIES), None)


def apply_bulk_categories(collection, gallery_items):
    # The bulk worker keys results by listing object, exact URL, model and method.
    def keys():
        for object_id, asset in gallery_items:
            references = (asset['mls_photo_url'], *asset.get('_photo_aliases', ()))
            for priority, reference in enumerate(references):
                material = json.dumps([str(object_id), reference, BULK_MODEL, BULK_METHOD],
                                      separators=(',', ':'))
                yield hashlib.sha256(material.encode()).hexdigest(), (asset, priority)

    remaining = keys()
    alias_winners = {}
    while batch := dict(islice(remaining, 5000)):
        for result in collection.find(
            {'_id': {'$in': list(batch)}, 'result': 'classified', 'category': {'$in': list(CATEGORIES)}},
            {'category': 1},
        ):
            asset, priority = batch[result['_id']]
            category = result['category']
            if asset.get('_photo_aliases'):
                priority = -1 if category == 'floor_plan' else priority
                winner = alias_winners.get(id(asset), float('inf'))
                if priority >= winner:
                    continue
                alias_winners[id(asset)] = priority
            asset['category'] = category
