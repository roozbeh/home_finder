from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase

from bson import ObjectId

from gallery_photo_labels import FIELD, gallery_ids, merge_gallery_labels, store_gallery_labels
from import_gallery_photo_labels import BULK, HISTORICAL, TARGETED, select_labels


FULL = 'https://media.mlslmedia.com/property/MLSL/12345678/0123456789abcdef0123456789abcdef/2/1'
THUMBNAIL = FULL.rsplit('/', 2)[0] + '/1/1'
NOW = datetime(2026, 11, 23, 20, tzinfo=timezone.utc)


class GalleryLabelTest(TestCase):
    def setUp(self):
        self.listing_id = ObjectId()
        self.listing = {'_id': self.listing_id, 'LISTING_ID': 'ML12345678',
                        'SOURCE_MLS': 'MLSLISTINGS', 'photos': [FULL]}
        self.photo_id, = gallery_ids(self.listing)

    def test_latest_bulk_does_not_override_targeted_or_add_missing_photo(self):
        targeted = {'photo_id': self.photo_id, 'category': 'kitchen', 'classified_at': NOW,
                    'source': 'flip_finder.active_photo_classifications'}
        bulk = {**targeted, 'category': 'floor_plan', 'classified_at': NOW + timedelta(seconds=1),
                'source': 'moondream-mls-backfill'}
        listing = {**self.listing, FIELD: [targeted]}
        self.assertEqual(merge_gallery_labels(listing, [bulk, {**bulk, 'photo_id': 'missing'}]), [targeted])

    def test_import_matches_aliases_and_skips_conflicting_historical_pair_labels(self):
        bulk = {'listing_object_id': self.listing_id, 'listing_id': self.listing['LISTING_ID'],
                'source_mls': self.listing['SOURCE_MLS'], 'photo_url': FULL,
                'category': 'bedroom', 'classified_at': NOW, 'result': 'classified'}
        targeted = {'listing_id': self.listing['LISTING_ID'], 'photo_url': THUMBNAIL,
                    'category': 'kitchen', 'classified_at': NOW}
        history = {'listing_id': self.listing['LISTING_ID'], 'photo_url': THUMBNAIL,
                   'category': 'bedroom', 'classified_at': NOW, 'pair_key': 'p', 'side': 'buy'}
        fake_pairs = SimpleNamespace(find=lambda *_: [{'pair_key': 'p', 'buy_mls': self.listing['LISTING_ID']}])
        source = SimpleNamespace(name='flip_finder', flipdb=fake_pairs)
        candidates, stats = select_labels(
            source, {BULK: [bulk], TARGETED: [targeted], HISTORICAL: [history]},
            {self.listing_id: self.listing}, {self.listing['LISTING_ID']: [self.listing_id]},
        )
        self.assertEqual(candidates[self.listing_id][self.photo_id]['category'], 'kitchen')
        self.assertEqual(stats['photos_with_conflicting_nonhistorical_labels'], 1)
        self.assertEqual(stats['sources'][HISTORICAL]['preserved_conflicting_pair_rows'], 1)

    def test_write_rechecks_gallery_after_failed_guard(self):
        label = {'photo_id': self.photo_id, 'category': 'kitchen', 'classified_at': NOW}
        writes = []

        def update_one(query, update):
            writes.append((query, update))
            return SimpleNamespace(matched_count=0)

        collection = SimpleNamespace(update_one=update_one, find_one=lambda *_: None)
        self.assertFalse(store_gallery_labels(collection, self.listing, [label]))
        self.assertEqual(writes[0][0]['photos'], [FULL])
        self.assertEqual(writes[0][0][FIELD], {'$exists': False})
        self.assertEqual(len(writes), 1)
