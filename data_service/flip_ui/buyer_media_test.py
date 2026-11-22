import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from buyer_media import approved_mls_gallery, approved_mls_photo_url, fetch_mls_photo, validated_jpeg
from buyer_photo_labels import BULK_METHOD, BULK_MODEL, apply_bulk_categories, gallery_category


MLSL_PHOTO = 'https://media.mlslmedia.com/property/MLSL/12345678/0123456789abcdef0123456789abcdef/2/1'
CRMLS_IMAGE = 'https://media.crmls.org/medias/00112233-4455-6677-8899-aabbccddeeff.jpg'


class BuyerMediaTest(unittest.TestCase):
    def test_extensionless_mls_listings_gallery_urls(self):
        for url in (MLSL_PHOTO, MLSL_PHOTO[:-1] + '65'):
            self.assertEqual(approved_mls_photo_url(url), url)

        for url in (
            MLSL_PHOTO.replace('https:', 'http:'),
            MLSL_PHOTO.replace('media.mlslmedia.com', 'media.crmls.org'),
            MLSL_PHOTO.replace('media.mlslmedia.com', 'media.mlslmedia.com.evil.test'),
            MLSL_PHOTO.replace('media.mlslmedia.com', 'media.mlslmedia.com@evil.test'),
            MLSL_PHOTO.replace('media.mlslmedia.com', 'media.mlslmedia.com:8443'),
            MLSL_PHOTO.replace('/property/', '/anything/'),
            MLSL_PHOTO.replace('/2/1', '/../1'),
            MLSL_PHOTO + '?token=provider-private',
            MLSL_PHOTO + '#fragment',
            MLSL_PHOTO + '\n',
        ):
            with self.subTest(url=url):
                self.assertIsNone(approved_mls_photo_url(url))

    def test_crmls_public_preset_and_gallery_size_variants(self):
        preset = CRMLS_IMAGE + '?preset=X-Large'
        self.assertEqual(approved_mls_photo_url(preset), preset)
        mediaz = preset.replace('/medias/', '/mediaz/')
        self.assertEqual(approved_mls_photo_url(mediaz), mediaz)
        for url in (
            preset + '&token=provider-private', preset.replace('X-Large', 'Other'),
            preset.replace('media.crmls.org', 'sfarmedia.rapmls.com'),
            preset.replace('/medias/', '/other/'), mediaz.replace('00112233', 'inval1d'),
        ):
            with self.subTest(url=url):
                self.assertIsNone(approved_mls_photo_url(url))
        second = MLSL_PHOTO[:-1] + '2'
        thumbnail = MLSL_PHOTO.replace('/2/1', '/1/1')
        self.assertEqual(approved_mls_gallery(thumbnail, [second, MLSL_PHOTO, thumbnail]),
                         [(MLSL_PHOTO, [thumbnail]), (second, [])])
        self.assertEqual(approved_mls_gallery(preset, [CRMLS_IMAGE, preset]), [(CRMLS_IMAGE, [preset])])
        self.assertEqual(approved_mls_gallery(preset, []), [(preset, [])])

    def test_classification_of_a_supplied_variant_is_retained(self):
        thumbnail = MLSL_PHOTO.replace('/2/1', '/1/1')
        other = MLSL_PHOTO[:-1] + '2'
        self.assertEqual(gallery_category({thumbnail: 'kitchen'}, MLSL_PHOTO, [thumbnail]), 'kitchen')
        self.assertEqual(gallery_category({thumbnail: 'floor_plan', MLSL_PHOTO: 'other'},
                                          MLSL_PHOTO, [thumbnail]), 'floor_plan')
        collection = Mock()

        def key(url):
            material = json.dumps(['fixture-object', url, BULK_MODEL, BULK_METHOD], separators=(',', ':'))
            return hashlib.sha256(material.encode()).hexdigest()

        collection.find.return_value = [{'_id': key(url), 'category': category} for url, category in (
            (thumbnail, 'floor_plan'), (MLSL_PHOTO, 'other'), (other, 'kitchen'),
        )]
        asset = {'mls_photo_url': MLSL_PHOTO, '_photo_aliases': [thumbnail]}
        second = {'mls_photo_url': other}
        apply_bulk_categories(collection, [('fixture-object', asset), ('fixture-object', second)])
        self.assertEqual(asset['category'], 'floor_plan')
        self.assertEqual(second['category'], 'kitchen')

    def test_extensionless_mls_listings_fetch_still_sanitizes_returned_jpeg(self):
        original = (Path(__file__).with_name('fixtures') / 'ranch.jpg').read_bytes()
        comment = b'upstream comment with nonpublic notes'
        length = (len(comment) + 2).to_bytes(2, 'big')
        upstream = original[:2] + b'\xff\xfe' + length + comment + original[2:]
        with patch('buyer_media.build_opener') as build:
            opener = build.return_value
            response = opener.open.return_value.__enter__.return_value
            response.read.return_value = upstream
            clean = fetch_mls_photo(MLSL_PHOTO)
            request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, MLSL_PHOTO)
        self.assertEqual(request.get_header('Accept'), 'image/jpeg')
        self.assertEqual(clean, original)
        self.assertEqual(validated_jpeg(clean), clean)

        with patch('buyer_media.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = b'not an image'
            with self.assertRaisesRegex(ValueError, 'approved JPEG'):
                fetch_mls_photo(MLSL_PHOTO)

        with patch('buyer_media.build_opener') as build:
            with self.assertRaisesRegex(ValueError, 'Invalid public image URL'):
                fetch_mls_photo(MLSL_PHOTO + '?token=provider-private')
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
