"""Public MLS fact labels and scalar value rules for the buyer catalog."""
import html
import re

# Map the observed ConnectMLS keys. Raw provider objects never reach a template.
GROUPS = {
    'Interior': {
        'Bedrooms': ('BEDROOMS_TOTAL', 'Beds'), 'Full bathrooms': ('BATHROOMS_FULL',),
        'Half bathrooms': ('BATHROOMS_HALF',), 'Bathrooms': ('BATHS_DISPLAY',),
        'Living area (sq ft)': ('SQFT', 'LivingArea'), 'Rooms': ('ROOMS_TOTAL',),
        'Additional rooms': ('ROOM_ADDL',), 'Kitchen': ('KITCHEN_FEATURES',),
        'Bathroom features': ('MASTER_BATH_INCLUDES', 'BATHROOM_FEATURES', 'BATH_INCLUDES'),
        'Dining room': ('DINING_ROOM',), 'Family room': ('FAMILY_ROOM',),
        'Appliances': ('APPLIANCES', 'APPLIANCES_INCLUDED', 'EQUIPMENT_ADDL'),
        'Laundry': ('LAUNDRY',), 'Flooring': ('FLOORING',),
        'Heating': ('HEATING',), 'Cooling': ('COOLING',), 'Fireplaces': ('FIREPLACES', 'FP', 'FIREPLACES_TOTAL'),
        'Accessibility features': ('DISABLED_FEATURES',),
    },
    'Property and parking': {
        'Property subtype': ('BUILDING_TYPE', 'PROPERTY_SUBTYPE', 'PROPERTY_TYPE_DISPLAY'),
        'Stories': ('STORIES',), 'Unit floor': ('UNIT_FLOOR',), 'Total units': ('NUMBER_OF_UNITS_TOTAL',),
        'Unit features': ('UNIT_FEATURE',), 'Lot size (sq ft)': ('LOT_SQFT',), 'Lot size (acres)': ('ACRES',),
        'Lot features': ('LOT_FEATURES', 'LOT_DESCRIPTION'),
        'Garage': ('GARAGE',), 'Garage spaces': ('GARAGE_SPACES',), 'Parking spaces': ('PARKING_TOTAL', 'PARKING_SPACES_TOTAL'),
        'Parking features': ('GARAGE_DESC', 'PARKING_FEATURES'), 'Views': ('VIEWS', 'VIEW'),
        'Outdoor features': ('EXTERIOR_FEATURES', 'PATIO_AND_PORCH_FEATURES'), 'Pool': ('POOL_FEATURES',),
        'Zoning': ('ZONING',), 'Parcel number': ('PARCEL_NUMBER',),
    },
    'Construction': {
        'Year built': ('YEAR_BUILT',), 'Architectural style': ('STYLE', 'ARCHITECTURAL_STYLE'),
        'Construction materials': ('CONSTRUCTION_MATERIALS', 'CONSTRUCTION'), 'Construction status': ('CONSTRUCTION_STATUS',),
        'Condition': ('PROPERTY_CONDITION',), 'New construction': ('NEW_CONSTRUCTION_YN',),
        'Foundation': ('FOUNDATION', 'FOUNDATION_DETAILS'), 'Roof': ('ROOF',),
        'Accessory dwelling area (sq ft)': ('ADU_SQFT',),
    },
    'Utilities': {
        'Water': ('WATER_SOURCE', 'WATER'), 'Sewer': ('SEWER', 'SEWER_SEPTIC'), 'Water and sewer services': ('WATERSEWER',),
        'Gas and electricity': ('UTILITIES', 'UTILITIES_AVAILABLE', 'GAS'),
        'Energy features': ('GREEN_ENERGY_EFFICIENT', 'ENERGY_FEATURES', 'ENERGY_SAVING_FEATURE'),
    },
    'Community and HOA': {
        'Community': ('SUBDIVISION_NAME',), 'County': ('COUNTY_OR_PARISH',),
        'Association name': ('HOANAME', 'ASSOCIATION_NAME'),
        'HOA': ('HOA', 'ASSOCIATION_YN'), 'HOA fee': ('HOA_FEE', 'ASSOCIATION_FEE'),
        'HOA billing period': ('FREQ', 'ASSOCIATION_FEE_FREQUENCY'),
        'HOA amenities': ('HOA_AMENITIES', 'ASSOCIATION_AMENITIES'),
        'HOA fee includes': ('HOA_FEE_INCLUDES', 'ASSOCIATION_FEE_INCLUDES'),
        'Pets': ('PETS', 'PETS_ALLOWED'),
        'Elementary school district': ('ELEMENTARY_SCHOOL_DISTRICT',),
        'High school district': ('HIGH_SCHOOL_DISTRICT',),
    },
    'Financial and listing details': {
        'Price per square foot': ('LIST_PRICE_PER_SQFT',),
        'Annual property tax': ('TAX_ANNUAL_AMOUNT', 'ANNUAL_PROPERTY_TAX'),
        'Tax assessed value': ('TAX_ASSESSED_VALUE',), 'Tax year': ('TAX_YEAR',),
        'Listing terms': ('LISTING_TERMS',), 'Listing agreement': ('LISTING_AGREEMENT',),
    },
}
FACT_KEYS = frozenset(key for group in GROUPS.values() for keys in group.values() for key in keys)

PRIVATE_CONTENT = re.compile(
    r'\b(?:lock[ -]?box|supra|gate code|door code|alarm code|access code|showing instructions|'
    r'showing desk|tenant[ -]occupied|owner[ -]occupied|occupied by|'
    r'(?:contact|call|text|e-?mail)\s+(?:the\s+)?(?:agent|seller|owner|tenant|listing agent)|'
    r'(?:seller|owner|buyer|agent)(?:[’\x27]s)?\s+(?:name|phone|email)|'
    r'commission|agent compensation)\b|'
    r'https?://|[\w.+-]+@[\w.-]+\.\w{2,}|(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}',
    re.IGNORECASE,
)


def public_text(value, *, maxlength=320):
    if type(value) in (int, float):
        return str(value)
    if type(value) is bool:
        return 'Yes' if value else 'No'
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r'<[^>]*>', ' ', html.unescape(value))
    cleaned = ' '.join(cleaned.split())
    if cleaned and len(cleaned) <= maxlength and not PRIVATE_CONTENT.search(cleaned):
        return cleaned
    return None


def listing_phone(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(?:\+?1[ .-]?)?(?:\(([2-9]\d{2})\)|([2-9]\d{2}))[ .-]*(\d{3})[ .-]*(\d{4})',
                         value.strip(), re.ASCII)
    if match:
        wrapped_area, area, prefix, number = match.groups()
        return f'{wrapped_area or area}-{prefix}-{number}'
    return None


def listing_credit(value):
    """Publish only the named listing parties and their matching report phones."""
    result = {}
    for party in ('agent', 'office'):
        if name := public_text(value.get(party), maxlength=120):
            result[party] = name
            if phone := listing_phone(value.get(f'{party}_phone')):
                result[f'{party}_phone'] = phone
    return result


def public_facts(document, details):
    result = {}
    for group, labels in GROUPS.items():
        rows = {}
        for label, keys in labels.items():
            for key in keys:
                raw = details.get(key)
                if raw in (None, ''):
                    raw = document.get(key)
                if isinstance(raw, list):
                    parts = [part for value in raw if (part := public_text(value))]
                    raw = ', '.join(parts)
                value = public_text(raw)
                if value and value not in {'--', 'None', 'N/A'}:
                    if label == 'HOA fee' and value.isascii() and value.isdecimal():
                        value = f'${int(value):,}'
                    rows[label] = value
                    break
        if rows:
            result[group] = rows
    return result


def checked_facts(value):
    """Limit the published fact object to defined labels even for manual manifests."""
    return {
        group: {label: clean for label in labels if (clean := public_text(value[group].get(label)))}
        for group, labels in GROUPS.items() if isinstance(value.get(group), dict)
    }
