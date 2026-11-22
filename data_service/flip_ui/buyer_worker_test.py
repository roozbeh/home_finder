"""Whole-gallery, budget and permission regression tests for the fixture worker."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from buyer_worker import Worker, pilot_ids
from buyer_worker_fixtures import gallery_fixture


class WorkerTests(unittest.TestCase):
    def test_gallery_restart_idempotence_and_incremental_updates(self):
        manifest = gallery_fixture()
        row = manifest['homes'][0]
        row['media'].append(deepcopy(row['media'][0]))
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'worker.sqlite3'
            with closing(Worker(path)) as worker:
                worker.sync(manifest)
                self.assertEqual(worker.run(10), 10)
                progress = worker.report()['listings'][0]
                self.assertEqual((progress['eligible'], progress['completed'], progress['retryable'],
                                  progress['unavailable']), (70, 8, 1, 1))
            with closing(Worker(path)) as worker:
                worker.sync(manifest)
                self.assertEqual(worker.run(100), 61)
                report = worker.report()['listings'][0]
                self.assertEqual((report['eligible'], report['completed'], report['unavailable'],
                                  report['retryable'], report['retries']), (70, 69, 1, 0, 1))
                self.assertEqual(report['state'], 'partial')
                initial = worker.ready(row['id'])
                self.assertEqual(initial['gross_after_purchase_and_work'], 75_000)
                self.assertEqual(initial['resale'], 545_000)
                self.assertEqual(initial['scenario']['renovation'], 10_000)
                row['price'] += 5_000
                worker.sync(manifest)
                self.assertEqual(worker.ready(row['id'])['gross_after_purchase_and_work'], 70_000)
                self.assertEqual(worker.run(100), 0)
                cache_hits = worker.report()['listings'][0]['cache_hits']
                self.assertEqual(cache_hits, 69)
                worker.sync(manifest)
                self.assertEqual(worker.report()['listings'][0]['cache_hits'], cache_hits)
                row['analysis_inputs']['comp_prices'][1] += 10_000
                row['analysis_inputs']['comps_version'] = 'sales-2'
                row['analysis_inputs']['price_book']['Kitchen']['unit_low'] += 2_000
                row['analysis_inputs']['price_book']['Kitchen']['unit_high'] += 2_000
                row['analysis_inputs']['price_book_version'] = 'prices-2'
                worker.sync(manifest)
                updated = worker.ready(row['id'])
                self.assertEqual((updated['gross_after_purchase_and_work'], updated['resale'],
                                  updated['renovation']), (73_000, 550_000, 12_000))
                row['media'][8]['image_version'] = 'changed-pixels'
                worker.sync(manifest)
                self.assertIsNone(worker.ready(row['id']))
                self.assertEqual(worker.run(100), 1)
                self.assertIsNotNone(worker.ready(row['id']))

    def test_rights_revocation_before_dispatch_and_save(self):
        manifest = gallery_fixture(2)
        row = manifest['homes'][0]
        with closing(Worker(':memory:')) as worker:
            worker.sync(manifest)
            claim = worker._claim()
            row['policy']['allow']['ai'] = False
            worker.sync(manifest)
            self.assertEqual(worker.run(), 0)
            worker._finish(*claim, ('complete', {'category': 'Kitchen'}, 300, 24, 80))
            report = worker.report()
            self.assertEqual(report['ai_eligible'], 0)
            self.assertEqual(report['publication_eligible'], 1)
            self.assertIsNone(worker.ready(row['id']))
            row['policy']['allow']['ai'] = True
            worker.sync(manifest)
            self.assertEqual(worker.run(), 2)
            self.assertIsNotNone(worker.ready(row['id']))
            row['withdrawn'] = True
            worker.sync(manifest)
            self.assertEqual(worker.run(), 0)
            self.assertEqual(worker.report()['publication_eligible'], 0)
            self.assertIsNone(worker.ready(row['id']))

    def test_restart_reclaims_lease_and_respects_pause_and_budget(self):
        manifest = gallery_fixture(2)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'worker.sqlite3'
            with closing(Worker(path)) as worker:
                worker.sync(manifest)
                worker.controls(paused=True)
                self.assertEqual(worker.run(), 0)
                worker.controls(paused=False, ceiling=90)
                self.assertEqual(worker.run(), 0)
                worker.controls(ceiling=100)
                worker._claim()
                worker.connection.execute("UPDATE worker_images SET lease_until='2000-01-01'")
            with closing(Worker(path)) as worker:
                self.assertEqual(worker.run(), 0)
                worker.controls(ceiling=1000)
                self.assertEqual(worker.run(), 2)
                self.assertEqual(worker.report()['listings'][0]['completed'], 2)
                attempts = worker.connection.execute('SELECT state FROM worker_attempts ORDER BY rowid').fetchall()
                self.assertEqual([row['state'] for row in attempts], ['interrupted', 'complete', 'complete'])

    def test_pilot_caps_execution_and_keeps_full_inventory(self):
        manifest = gallery_fixture(1)
        template = manifest['homes'][0]
        manifest['homes'] = [{**template, 'id': f'gallery{i:04}test', 'condition': str(i % 3)}
                             for i in range(110)]
        template['policy']['allow']['ai'] = True
        manifest['homes'][0]['county'] = 'nonsupported'
        expected = pilot_ids(manifest['homes'][1:])
        with closing(Worker(':memory:')) as worker:
            worker.sync(manifest, pilot=True)
            self.assertEqual(worker.run(150), 100)
            report = worker.report()
            self.assertEqual(report['publication_eligible'], 109)
            self.assertEqual(report['ai_eligible'], 109)
            self.assertEqual(report['states']['out of pilot'], 9)
            self.assertEqual({row['id'] for row in report['listings'] if row['completed']}, expected)

    def test_approved_work_cannot_dispatch_fixture_executor(self):
        manifest = gallery_fixture(1)
        manifest['mode'] = 'approved'
        manifest['sources']['synthetic']['production_approved'] = True
        with closing(Worker(':memory:')) as worker:
            worker.sync(manifest)
            self.assertEqual(worker.report()['ai_eligible'], 1)
            self.assertEqual(worker.run(), 0)

    def test_remaining_coverage_states_and_separate_permissions(self):
        manifest = gallery_fixture(1)
        template = manifest['homes'][0]
        manifest['homes'] = [deepcopy(template) for _ in range(4)]
        missing, failed, thin, no_public = manifest['homes']
        for row, listing_id in zip(manifest['homes'], ('missing', 'failed', 'thin', 'ai-only')):
            row['id'] = listing_id
        missing['media'] = []
        failed['media'][0]['fixture_failure'] = 'always'
        thin['analysis_inputs']['comp_prices'] = [530_000]
        no_public['policy']['allow']['internet'] = False
        with closing(Worker(':memory:')) as worker:
            worker.sync(manifest)
            self.assertEqual(worker.report()['states']['queued'], 3)
            self.assertEqual(worker.report()['states']['missing media'], 1)
            self.assertEqual(worker.run(20), 5)
            report = worker.report()
            self.assertEqual((report['publication_eligible'], report['ai_eligible']), (3, 4))
            self.assertEqual((report['states']['failed'], report['states']['insufficient comps'],
                              report['states']['complete']), (1, 1, 1))
            self.assertIsNone(worker.ready('ai-only'))


if __name__ == '__main__':
    unittest.main()
