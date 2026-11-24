"""Shared worker and publisher identifiers for locally generated assessments."""
import hashlib
import json

from buyer_media import approved_mls_gallery


COLLECTION = 'active_renovation_cost_drafts'
METHOD = 'local_visible_work_unit_rates'


def fingerprint(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def current_gallery(listing):
    details = listing.get('details') or {}
    gallery = listing.get('photos')
    return approved_mls_gallery(details.get('PRIMARY_PHOTO_URI'), gallery if isinstance(gallery, list) else [])
