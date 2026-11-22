import unittest
from unittest.mock import Mock, patch

import requests

import bulk_moondream as backfill
from photo_inference import InferenceError


class BackfillRetryTests(unittest.TestCase):
    def test_transient_ollama_error_recovers_and_saves_classification(self):
        results = Mock()
        with patch.object(backfill, 'read_image', return_value={'data': 'photo'}), \
             patch.object(backfill, 'caption_for', side_effect=[InferenceError('timeout'), 'A kitchen']), \
             patch.object(backfill, 'sleep') as wait:
            outcome = backfill.save_photo(results, None, None, 'photo-id', {'photo_url': 'https://photo'})
        wait.assert_called_once_with(2)
        self.assertEqual(outcome, 'classified')
        self.assertEqual(results.update_one.call_args.args[1]['$set']['category'], 'kitchen')

    def test_service_outage_leaves_photo_unwritten_for_resume(self):
        results = Mock()
        with patch.object(backfill, 'read_image', return_value={'data': 'photo'}), \
             patch.object(backfill, 'caption_for', side_effect=InferenceError('unreachable')) as caption, \
             patch.object(backfill, 'sleep'), self.assertRaises(InferenceError):
            backfill.save_photo(results, None, None, 'photo-id', {'photo_url': 'https://photo'})
        self.assertEqual(caption.call_count, 3)
        results.update_one.assert_not_called()

    def test_transient_photo_download_retries_but_missing_photo_does_not(self):
        for status, attempts in ((503, 3), (404, 1)):
            response = requests.Response()
            response.status_code = status
            error = requests.HTTPError(response=response)
            with self.subTest(status=status), patch.object(backfill, 'read_image', side_effect=error) as read, \
                 patch.object(backfill, 'sleep'), self.assertRaises(requests.HTTPError):
                backfill.download_with_retry(None, 'https://photo')
            self.assertEqual(read.call_count, attempts)
