from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase

from buyer_fixtures import PRIVATE, fixture_manifest
from buyer_media import validated_jpeg
from buyer_publish import project


ASSETS = Path(__file__).with_name('fixtures')


class BuyerProjectionTest(TestCase):
    def test_approved_mode_requires_source_activation(self):
        manifest = fixture_manifest(ASSETS)
        manifest['mode'] = 'approved'
        subject = manifest['homes'][0]
        self.assertEqual(project(manifest, subject, ASSETS), (None, []))
        manifest['sources']['fixture']['production_approved'] = True
        self.assertIsNotNone(project(manifest, subject, ASSETS)[0])

    def test_property_type_permission_controls_group_and_subtype(self):
        manifest = fixture_manifest(ASSETS)
        subject = manifest['homes'][0]
        subject['property_type'] = 'Condo'
        subject['facts'] = {'Property and parking': {'Property subtype': 'Condo'}}
        subject['policy']['allow']['property_type'] = False
        (home, _), _ = project(manifest, subject, ASSETS)
        self.assertNotIn('property_type', home)
        self.assertNotIn('property_type_group', home)
        self.assertNotIn('Property subtype', home['facts'].get('Property and parking', {}))

    def test_media_metadata_is_refused(self):
        original = (ASSETS / 'ranch.jpg').read_bytes()
        self.assertEqual(validated_jpeg(original), original)
        payload = PRIVATE.encode()
        length = (len(payload) + 2).to_bytes(2, 'big')
        for marker in (b'\xff\xfe', b'\xff\xe1', b'\xff\xe2'):
            with self.assertRaises(ValueError):
                validated_jpeg(original[:2] + marker + length + payload + original[2:])

    def test_live_listing_update_uses_provider_calendar_date(self):
        from buyer_live_feed import listing_update_date

        self.assertEqual(listing_update_date('11/07/2026 04:31 PM'), '2026-11-07')
        self.assertIsNone(listing_update_date('unknown'))

    def test_live_listing_baths_follow_mls_display_formats(self):
        from buyer_live_feed import listing_baths

        self.assertEqual(listing_baths({}, {'Baths': '2'}), 2)
        self.assertEqual(listing_baths({'BATHROOMS_FULL': 2}, {'Baths': '2/1'}), 2.5)
        self.assertEqual(listing_baths({}, {'BATHROOMS_FULL': '1', 'BATHROOMS_HALF': '1'}), 1.5)
        self.assertIsNone(listing_baths({}, {'Baths': '--'}))

    def test_live_assessment_import_requires_review_of_exact_revision(self):
        from buyer_live_feed import manifest_for

        now = datetime(2026, 11, 22, tzinfo=timezone.utc)
        observed = now - timedelta(hours=1)
        fields = ['internet', 'address', 'derived', 'valuation', 'sold_comps', 'ai']
        config = {'feed': {'production_approved': True, 'listing_refresh_hours': 24,
                           'internet_display_values': {'Full': fields}}}
        row = {'SOURCE_MLS': 'feed', 'LISTING_ID': 'M1', 'RAW_STATUS': 'ACTV',
               '_updated_at': observed, 'details_fetched': observed,
               'details': {'INTERNET_DISPLAY': 'Full', 'INTERNET_SITES': 'Yes - All Sites'}}
        assessment = {'created_at': observed, 'valuation_status': 'AI_estimate_requires_review',
                      'resale_midpoint': 950_000, 'photo_urls': [PRIVATE]}
        evidence = {'purchase_assessment_created_at': observed.isoformat(),
                    'purchase_assessment_refresh_by': (now + timedelta(hours=2)).isoformat(),
                    'allow': dict.fromkeys(fields, True)}
        def resulting_row():
            return manifest_for([row], config, b'test-key', reviewed={'feed:M1': evidence},
                                saved_assessments={'M1': assessment}, now=now)['homes'][0]

        carried = resulting_row()['reviewed_purchase_assessment']
        self.assertEqual(carried['resale_midpoint'], 950_000)
        self.assertNotIn('photo_urls', carried)
        evidence['allow']['sold_comps'] = False
        self.assertNotIn('reviewed_purchase_assessment', resulting_row())
        evidence['allow']['sold_comps'] = True
        assessment['created_at'] += timedelta(seconds=1)
        self.assertNotIn('reviewed_purchase_assessment', resulting_row())
        assessment.update(method='active_listing_proposed_visible_work', renovation_low=35_000, renovation_high=65_000)
        saved = manifest_for([row], config, b'test-key', saved_assessments={'M1': assessment},
                             publish_operator_estimates=True, now=now)['homes'][0]
        self.assertEqual(saved['operator_flip_assessment']['renovation_low'], 35_000)
        self.assertEqual(saved['operator_flip_assessment']['renovation_high'], 65_000)
        self.assertNotIn('reviewed_purchase_assessment', saved)
