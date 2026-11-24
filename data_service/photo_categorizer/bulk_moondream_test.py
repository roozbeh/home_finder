import unittest
from unittest.mock import Mock, call, patch

import requests

import bulk_moondream as backfill
from photo_inference import InferenceError

PHOTO = 'https://media.mlslmedia.com/property/MLSL/82065431/00000000000000000000000000000001/2/1'


class BackfillRetryTests(unittest.TestCase):
    def test_transient_ollama_error_recovers_and_keeps_caption(self):
        with patch.object(backfill, 'read_image', return_value={'data': 'photo'}), \
             patch.object(backfill, 'caption_for', side_effect=[InferenceError('timeout'), 'A kitchen']), \
             patch.object(backfill, 'sleep') as wait:
            label = backfill.classify_photo(None, None, 'photo-id', [PHOTO], 'moondream:latest')
        wait.assert_called_once_with(2)
        self.assertEqual(label['category'], 'kitchen')
        self.assertEqual(label['caption'], 'A kitchen')
        self.assertEqual(label['source'], backfill.LABEL_SOURCE)

    def test_service_outage_leaves_photo_unwritten_for_resume(self):
        with patch.object(backfill, 'read_image', return_value={'data': 'photo'}), \
             patch.object(backfill, 'caption_for', side_effect=InferenceError('unreachable')) as caption, \
             patch.object(backfill, 'sleep'), self.assertRaises(InferenceError):
            backfill.classify_photo(None, None, 'photo-id', [PHOTO], 'moondream:latest')
        self.assertEqual(caption.call_count, 3)

    def test_transient_photo_download_retries_but_missing_photo_does_not(self):
        for status, attempts in ((503, 3), (404, 1)):
            response = requests.Response()
            response.status_code = status
            error = requests.HTTPError(response=response)
            with self.subTest(status=status), patch.object(backfill, 'read_image', side_effect=error) as read, \
                 patch.object(backfill, 'sleep'), self.assertRaises(requests.HTTPError):
                backfill.download_with_retry(None, 'https://photo')
            self.assertEqual(read.call_count, attempts)

    def test_variant_download_falls_back_when_full_size_is_unavailable(self):
        smaller = PHOTO.rsplit('/', 2)[0] + '/1/1'
        match, urls = next(iter(backfill.classification_urls([smaller, PHOTO]).items()))
        with patch.object(backfill, 'download_with_retry', side_effect=[ValueError(), {'data': 'photo'}]) as fetch, \
             patch.object(backfill, 'caption_with_retry', return_value='A kitchen'):
            label = backfill.classify_photo(None, None, match, urls, 'moondream:latest')
        self.assertEqual(label['photo_id'], match)
        self.assertEqual(fetch.call_args_list, [call(None, PHOTO), call(None, smaller)])


class FlipPriorityTests(unittest.TestCase):
    def test_priority_resolves_both_sides_once(self):
        source, pairs = Mock(), Mock()
        pairs.find.return_value = [{'buy_mls': '002', 'sell_mls': '003'},
                                   {'buy_mls': '001', 'sell_mls': '002'}, {'buy_mls': None}]
        source.find.return_value.sort.return_value = [{'_id': 'a'}, {'_id': 'b'}, {'_id': 'c'}]
        self.assertEqual(backfill.flip_listing_ids(source, pairs), ['a', 'b', 'c'])
        source.find.assert_called_once_with({'LISTING_ID': {'$in': ['001', '002', '003']}}, {'_id': 1})

    def test_flip_stage_resumes_saved_queue_and_writes_to_the_listing_before_checkpointing(self):
        source, jobs = Mock(), Mock()
        jobs.find_one.return_value = {'state': 'running', 'priority_queue': ['first', 'second'],
                                      'priority_index': 1}
        listing = {'_id': 'second', 'photos': [PHOTO], 'RAW_STATUS': 'WDN'}
        source.find_one.return_value = listing
        photo_id = next(iter(backfill.classification_urls([PHOTO])))
        classify = Mock(return_value={'photo_id': photo_id, 'category': 'kitchen'})
        with patch.object(backfill, 'show_progress'), \
             patch.object(backfill, 'store_gallery_labels', return_value=True) as store:
            writes = Mock()
            writes.attach_mock(store, 'labels')
            writes.attach_mock(jobs.update_one, 'checkpoint')
            backfill.run_stage(source, jobs, classify, None, 'moondream:latest',
                               backfill.FLIP_STAGE, None, {'flips': 2}, 90, flip_ids=['different'])
        source.find_one.assert_called_once_with({'_id': 'second'}, backfill.PROJECTION)
        classify.assert_called_once_with(photo_id, [PHOTO], 'moondream:latest')
        store.assert_called_once_with(source, listing, [classify.return_value])
        self.assertEqual([item[0] for item in writes.mock_calls],
                         ['checkpoint', 'checkpoint', 'labels', 'checkpoint', 'checkpoint'])
        self.assertEqual(jobs.update_one.call_args_list[-2].args[1]['$set']['priority_index'], 2)

    def test_changed_gallery_does_not_advance_checkpoint(self):
        source, jobs = Mock(), Mock()
        jobs.find_one.return_value = None
        source.find_one.return_value = {'_id': 'first', 'photos': [PHOTO]}
        classify = Mock(return_value={'photo_id': 'photo-id', 'category': 'kitchen'})
        with patch.object(backfill, 'show_progress'), \
             patch.object(backfill, 'store_gallery_labels', return_value=False), \
             self.assertRaisesRegex(RuntimeError, 'Gallery changed'):
            backfill.run_stage(source, jobs, classify, None, 'moondream:latest',
                               backfill.FLIP_STAGE, None, {'flips': 1}, 90, flip_ids=['first'])
        self.assertEqual(jobs.update_one.call_count, 2)
