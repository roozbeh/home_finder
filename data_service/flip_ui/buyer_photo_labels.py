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


def apply_bulk_categories(collection, gallery_items):
    # The bulk worker keys results by listing object, exact URL, model and method.
    def keys():
        for object_id, asset in gallery_items:
            material = json.dumps([str(object_id), asset['mls_photo_url'], BULK_MODEL, BULK_METHOD],
                                  separators=(',', ':'))
            yield hashlib.sha256(material.encode()).hexdigest(), asset

    remaining = keys()
    while batch := dict(islice(remaining, 5000)):
        for result in collection.find(
            {'_id': {'$in': list(batch)}, 'result': 'classified', 'category': {'$in': list(CATEGORIES)}},
            {'category': 1},
        ):
            batch[result['_id']]['category'] = result['category']
