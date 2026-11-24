"""Merge current MLS gallery labels without modifying another writer's snapshot."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from buyer_media import approved_mls_photo_url, mls_photo_variant


FIELD = 'gallery_photo_labels'
PRIMARY = 'details.PRIMARY_PHOTO_URI'
PROJECTION = {'LISTING_ID': 1, 'SOURCE_MLS': 1, 'photos': 1, PRIMARY: 1, FIELD: 1}
_MISSING = object()


def photo_id_for(url):
    """Use public provider aliases and retain unrecognized query variants separately."""
    if not isinstance(url, str) or any(character.isspace() for character in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or parts.fragment
                or (host not in {'media.mlslmedia.com', 'media.crmls.org'}
                    and not host.endswith(('.cdn-connectmls.com', '.rapmls.com')))):
            return None
    except ValueError:
        return None
    if parts.query and not approved_mls_photo_url(url):
        return url
    return mls_photo_variant(url)[0]


def primary_photo(listing):
    details = listing.get('details')
    return details.get('PRIMARY_PHOTO_URI', _MISSING) if isinstance(details, dict) else _MISSING


def gallery_ids(listing):
    photos = listing.get('photos')
    photos = photos if isinstance(photos, list) else []
    return tuple(dict.fromkeys(
        photo_id for url in (primary_photo(listing), *photos) if (photo_id := photo_id_for(url))
    ))


def label_priority(label):
    source = str(label.get('source') or '').rsplit('.', 1)[-1]
    if label.get('method') == 'manual_visual_review' or source == 'photo_reviews':
        return 5
    if source == 'active_photo_classifications':
        return 3
    if source in {'mls_photo_classifications', 'moondream-mls-backfill'}:
        return 2
    if source == 'photo_classifications':
        return 1
    return 4


def label_timestamp(label):
    stamp = label.get('classified_at')
    if isinstance(stamp, datetime):
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def merge_gallery_labels(listing, candidates):
    current = gallery_ids(listing)
    allowed_ids = set(current)
    existing = listing.get(FIELD)
    existing = existing if isinstance(existing, list) else []
    winners = {}
    for label in (*existing, *candidates):
        if not isinstance(label, dict) or not isinstance(photo_id := label.get('photo_id'), str):
            continue
        if photo_id not in allowed_ids:
            continue
        previous = winners.get(photo_id)
        if previous is None:
            winners[photo_id] = label
            continue
        rank = label_priority(label), label_timestamp(label)
        prior_rank = label_priority(previous), label_timestamp(previous)
        if rank > prior_rank:
            winners[photo_id] = label
        elif rank == prior_rank and all(
            label.get(key) == previous.get(key) for key in ('category', 'source', 'model', 'method')
        ) and label.get('caption') and not previous.get('caption'):
            winners[photo_id] = {**previous, 'caption': label['caption']}
    return [winners[photo_id] for photo_id in current if photo_id in winners]


def gallery_filter(listing):
    identity = {'_id': listing['_id']}
    for field in ('SOURCE_MLS', 'LISTING_ID', 'photos'):
        identity[field] = listing[field] if field in listing else {'$exists': False}
    primary = primary_photo(listing)
    identity[PRIMARY] = {'$exists': False} if primary is _MISSING else primary
    return identity


def labels_filter(listing, identity):
    return {**identity, FIELD: listing[FIELD] if FIELD in listing else {'$exists': False}}


def store_gallery_labels(collection, listing, labels):
    """Retry label-only races and leave changed galleries for the next pass."""
    gallery = gallery_filter(listing)
    for _ in range(3):
        replacement = merge_gallery_labels(listing, labels)
        if replacement == listing.get(FIELD, []):
            return True
        result = collection.update_one(labels_filter(listing, gallery), {'$set': {FIELD: replacement}})
        if result.matched_count:
            return True
        listing = collection.find_one(gallery, PROJECTION)
        if listing is None:
            return False
    return False
