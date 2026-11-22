"""Public property categories backed by explicit listing classifications."""
import re

PROPERTY_GROUPS = {
    'houses': 'Houses',
    'townhomes': 'Townhomes',
    'multi-family': 'Multi-family',
    'condos-co-ops': 'Condos/Co-ops',
    'lots-land': 'Lots/Land',
    'apartments': 'Apartments',
    'manufactured': 'Manufactured',
}


def normalized(value):
    return ' '.join(re.findall(r'[a-z0-9]+', value.casefold())) if isinstance(value, str) else ''


# DE, CO and TH were corroborated against the detailed text for the same
# ConnectMLS listings. Codes without a textual match remain unclassified.
_LABELS = {
    ('houses', 'Detached'): ('de', 'detached', 'single family', 'single family home', 'single family residence', 'sfr', 'house', 'houses'),
    ('houses', 'Detached on leased land'): ('detached leased land', 'detached on leased land'),
    ('houses', 'Duet'): ('duet',),
    ('houses', 'Farm / Ranch'): ('farm ranch',),
    ('townhomes', 'Townhouse'): ('th', 'townhouse', 'townhouses', 'townhome', 'townhomes'),
    ('multi-family', 'Multi-family'): ('multi family', 'multifamily', 'multi family home'),
    ('multi-family', 'Duplex'): ('duplex',),
    ('multi-family', 'Triplex'): ('triplex',),
    ('multi-family', 'Fourplex'): ('fourplex', 'quadruplex'),
    ('condos-co-ops', 'Condo'): ('co', 'condo', 'condos', 'condominium', 'condo co op', 'condos co ops'),
    ('condos-co-ops', 'Detached condo'): ('detached condo', 'condo detached', 'detached condominium', 'condominium detached'),
    ('condos-co-ops', 'Co-op'): ('co op', 'co ops', 'coop', 'cooperative', 'stock cooperative'),
    ('lots-land', 'Land'): ('land', 'lots', 'lot', 'lots land', 'land lot', 'land lots', 'vacant land', 'residential lot'),
    ('apartments', 'Apartment'): ('apartment', 'apartments', 'apartment unit', 'flat'),
    ('manufactured', 'Manufactured home'): ('manufactured', 'manufactured home', 'manufactured homes'),
    ('manufactured', 'Manufactured on land'): ('manufactured on land', 'manufactured home on land'),
    ('manufactured', 'Mobile home'): ('mobile', 'mobile home', 'mobile homes'),
    (None, 'Residential'): ('re', 'residential'),
    (None, 'Other residential'): ('other residential',),
    (None, 'Modular'): ('modular',),
    (None, 'Loft'): ('loft',),
    (None, 'Floating home'): ('floating homes', 'floating home'),
    (None, 'Patio home / villa'): ('patio home villa', 'patio home', 'villa'),
}
_TYPE_FOR = {alias: key for key, aliases in _LABELS.items() for alias in aliases}


def property_type_group(value):
    """Group a known type, including the public labels stored in older catalogs."""
    return _TYPE_FOR.get(normalized(value), (None, None))[0]


def property_type_label(value):
    return _TYPE_FOR.get(normalized(value), (None, None))[1]


def listing_property_type(doc, details):
    """Read only classification fields, preferring a specific detail label."""
    detail_subtype = (details.get('PROPERTY_SUBTYPE'), details.get('PROPERTY_SUB_TYPE'))
    listing_subtype = (doc.get('PROPERTY_SUBTYPE'), doc.get('PROPERTY_SUB_TYPE'))
    fields = (
        details.get('BUILDING_TYPE'), *detail_subtype, doc.get('BUILDING_TYPE'), *listing_subtype,
        details.get('PROPERTY_TYPE'), details.get('PROPERTY_TYPE_DISPLAY'), doc.get('PROPERTY_TYPE'),
    )
    labels = [label for value in fields if (label := property_type_label(value))]
    return next((label for label in labels if label != 'Residential'), labels[0] if labels else None)


def type_filter(value):
    """Normalize old exact-type URLs while allowing unfamiliar legacy labels."""
    if value in PROPERTY_GROUPS:
        return value
    return property_type_group(value) or value
