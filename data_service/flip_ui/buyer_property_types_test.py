"""Regression coverage for normalized property-type filtering and classification."""
import unittest

from buyer_property_types import listing_property_type, property_type_group, type_filter


class PropertyTypesTest(unittest.TestCase):
    def test_uses_precise_listing_fields_without_guessing_numeric_codes(self):
        self.assertEqual(listing_property_type({'BUILDING_TYPE': 'Farm / Ranch'}, {'BUILDING_TYPE': '107'}), 'Farm / Ranch')
        self.assertEqual(listing_property_type({'BUILDING_TYPE': 'Other Residential'}, {'BUILDING_TYPE': '109'}),
                         'Other residential')
        self.assertEqual(listing_property_type({'BUILDING_TYPE': '109', 'PROPERTY_TYPE': 'RE'},
                                                {'BUILDING_TYPE': '109'}), 'Residential')
        self.assertIsNone(listing_property_type({'BUILDING_TYPE': '109'}, {'BUILDING_TYPE': '109'}))
        self.assertEqual(listing_property_type({'BUILDING_TYPE': 'Townhouse'}, {'BUILDING_TYPE': 'Condo'}), 'Condo')
        self.assertEqual(listing_property_type({}, {'BUILDING_TYPE': 'Unknown', 'PROPERTY_SUB_TYPE': 'Co-op'}), 'Co-op')
        self.assertEqual(listing_property_type({'BUILDING_TYPE': 'DE'}, {}), 'Detached')
        self.assertEqual(property_type_group('Detached, Condo'), 'condos-co-ops')

    def test_groups_known_types_leaving_ambiguous_types_under_any(self):
        expected = {
            'Detached Leased Land': 'houses', 'Duet': 'houses', 'Townhouse': 'townhomes',
            'Duplex': 'multi-family', 'Condominium': 'condos-co-ops', 'Vacant Land': 'lots-land',
            'Apartment': 'apartments', 'Manufactured On Land': 'manufactured',
            'Modular': None, 'Patio Home/Villa': None, 'Residential': None, '107': None, '109': None,
        }
        for label, group in expected.items():
            with self.subTest(label=label):
                self.assertEqual(property_type_group(label), group)
        self.assertEqual(type_filter('Condo'), 'condos-co-ops')
        self.assertEqual(type_filter('Townhouse'), 'townhomes')
        self.assertEqual(type_filter('Other Residential'), 'Other Residential')


if __name__ == '__main__':
    unittest.main()
