"""Fairfield arithmetic, safe projections and time-separated evaluation."""
import unittest
from datetime import date

from buyer_analysis import comparable_sales, detail_analysis, project_matches, purchase_scenario, scopes
from buyer_analysis_fixtures import cost_items, evidence_fixture
from buyer_evaluate import demo_dataset, evaluate


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.items = cost_items()
        self.budget = scopes(self.items, 'dated-1', True)
        self.subject = {'id': 'preview-fairfield', 'property_type': 'Single family', 'sqft': 1426, 'beds': 3}
        self.sales, self.projects = evidence_fixture()
        self.as_of = date(2026, 11, 21)

    def test_fairfield_scope_and_arithmetic(self):
        cosmetic, full = self.budget['budgets']
        self.assertEqual((8_000, 16_000), (cosmetic['rooms'][0]['low'], cosmetic['rooms'][0]['high']))
        self.assertEqual((50_000, 80_000), (full['rooms'][0]['low'], full['rooms'][0]['high']))
        self.assertEqual((2_000, 4_000), (self.budget['notes'][0]['low'], self.budget['notes'][0]['high']))
        self.assertIn('excluded from budget', self.budget['notes'][0]['kind'])
        self.assertEqual(cosmetic['rooms'][1]['items'][0]['task'], 'Cabinetry allowance')
        self.assertTrue(any(note['kind'] == 'Seller-reported' and 'roofer' in note['task'] for note in self.budget['notes']))
        base = purchase_scenario(460_000, 545_000, cosmetic['low'], cosmetic['high'])
        alternate = purchase_scenario(460_000, 545_000, full['low'], full['high'])
        favorable = purchase_scenario(460_000, 568_000, 35_000, 35_000)
        self.assertEqual((base['renovation'], base['result'], base['break_even']), (50_000, -47_575, 416_848))
        self.assertEqual((alternate['renovation'], alternate['result'], alternate['break_even']), (103_000, -100_575, 368_776))
        self.assertEqual((favorable['result'], favorable['break_even']), (-11_070, 449_959))
        self.assertEqual(base['total_cost'], sum(row['amount'] or 0 for row in base['ledger']))
        self.assertTrue(base['incomplete'])

    def test_ledger_unknowns_bundles_and_profit_target(self):
        ledger = purchase_scenario(
            460_000, 545_000, 50_000, 50_000, target=25_000,
            assumptions={'extras': {'Title and escrow': 'purchase_bundle', 'Insurance': 2100}},
        )
        by_name = {row['label']: row for row in ledger['ledger']}
        self.assertEqual(by_name['Insurance']['amount'], 2100)
        self.assertIsNone(by_name['Title and escrow']['amount'])
        self.assertIn('purchase closing bundle', by_name['Title and escrow']['basis'])
        self.assertEqual(by_name['Photography']['basis'], 'Unknown')
        self.assertEqual(ledger['result'], -49_675)
        self.assertLess(ledger['target_offer'], ledger['break_even'])

    def test_detail_selection_is_server_side_and_validation_is_finite(self):
        home = {'scopes': self.budget, 'resale': 545_000, 'price': 460_000}
        full = detail_analysis(home, {'scope_0': 'full', 'profit_target': '50000'})
        self.assertEqual(full['scenario']['result'], -100_575)
        self.assertEqual(full['scenario']['target'], 50_000)
        invalid = detail_analysis(home, {'scope_0': 'other', 'profit_target': '100e90'})
        self.assertEqual(invalid['selection'], {'bathrooms': 'cosmetic'})
        self.assertEqual(len(invalid['errors']), 2)
        valuation_suppressed = detail_analysis({'scopes': self.budget, 'price': 460_000}, {})
        self.assertNotIn('scenario', valuation_suppressed)
        self.assertIsNone(scopes(self.items, 'dated-1', True, address_ok=False))
        self.assertEqual(full['budget']['rooms'][0]['items'][0]['evidence_count'], 0)

    def test_validate_alternatives_and_disallow_duplicate_defaults(self):
        self.items[1]['included'] = True
        with self.assertRaisesRegex(ValueError, 'one default'):
            scopes(self.items, 'dated-1', False)
        self.items[1]['included'] = False
        self.items[1]['unit_high'] = 'NaN'
        with self.assertRaisesRegex(ValueError, 'finite'):
            scopes(self.items, 'dated-1', False)

    def test_selected_comp_projection_and_crofters_label(self):
        self.sales[0].update(private_remarks='forbidden', address='hidden street', raw_provider={'key': 'private'})
        selected = comparable_sales(self.subject, self.sales, self.as_of)
        self.assertEqual([sale['price'] for sale in selected], [560_000, 530_000])
        self.assertEqual(set(selected[0]), {'sale_date', 'sqft', 'distance', 'price', 'reason', 'expires'})
        self.assertNotIn('forbidden', repr(selected))
        self.assertEqual(comparable_sales(dict(self.subject, _location_visible=False), self.sales, self.as_of), [])
        past, = project_matches(self.subject, self.projects, self.as_of)
        self.assertEqual(past['gross_spread'], 150_000)
        self.assertEqual(past['hold_days'], 77)
        self.assertEqual(past['flip_status'], 'Flip status unverified')
        self.assertIsNone(past['net_result'])

    def test_evaluation_blocks_date_and_repeat_parcel_leakage(self):
        data = demo_dataset()
        report = evaluate(**data)
        self.assertEqual(report['candidate_parcels'], 3)
        self.assertEqual(report['same_parcel_repeats_grouped'], 1)
        self.assertEqual(report['unresolved_or_unsold'], 1)
        self.assertEqual(len(report['metrics']), 3)
        for method in report['metrics'].values():
            self.assertEqual(method['matched_resales'], 2)
            self.assertEqual(method['false_profitable_calls'], 1)
            self.assertEqual(method['invoice_backed_cases'], 2)
        data['sales'].append(dict(data['sales'][0], parcel_id=data['cases'][0]['subject']['parcel_id'], price=2_000_000))
        self.assertEqual(report, evaluate(**data))
        data['cases'][0]['saved_estimator']['evidence_dates'] = ['2026-10-01']
        self.assertEqual(evaluate(**data)['closed_without_common_forecast'], 1)


if __name__ == '__main__':
    unittest.main()
