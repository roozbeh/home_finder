"""get_school_info tool — returns school district data for Bay Area cities."""

SCHOOL_INFO = {
    "ATHERTON":        {"district": "Menlo Park City School District / Sequoia Union HSD",        "rating": 9,  "notes": "Encinal K-8, Menlo-Atherton High School"},
    "BELMONT":         {"district": "Belmont-Redwood Shores School District / Sequoia Union HSD", "rating": 8,  "notes": "Strong elementary schools, Carlmont High School"},
    "BRISBANE":        {"district": "Brisbane School District / Jefferson Union HSD",              "rating": 7,  "notes": "Small district with good community feel"},
    "BURLINGAME":      {"district": "Burlingame School District / San Mateo Union HSD",           "rating": 9,  "notes": "Highly rated elementary schools, Burlingame High School"},
    "COLMA":           {"district": "Jefferson Elementary / Jefferson Union HSD",                  "rating": 6,  "notes": "Small district"},
    "DALY CITY":       {"district": "Jefferson Elementary School District / Jefferson Union HSD",  "rating": 6,  "notes": "Jefferson High School"},
    "EASTPAALTO":      {"district": "Ravenswood City School District / Sequoia Union HSD",        "rating": 5,  "notes": "Menlo-Atherton High School serves the area"},
    "EAST PALO ALTO":  {"district": "Ravenswood City School District / Sequoia Union HSD",        "rating": 5,  "notes": "Menlo-Atherton High School serves the area"},
    "FOSTERCITY":      {"district": "San Mateo-Foster City School District / San Mateo Union HSD","rating": 8,  "notes": "Highly rated, Hillsdale High School"},
    "FOSTER CITY":     {"district": "San Mateo-Foster City School District / San Mateo Union HSD","rating": 8,  "notes": "Highly rated, Hillsdale High School"},
    "HALFMO BAY":      {"district": "Cabrillo Unified School District",                            "rating": 7,  "notes": "Half Moon Bay High School, scenic coastal community"},
    "HALF MOON BAY":   {"district": "Cabrillo Unified School District",                            "rating": 7,  "notes": "Half Moon Bay High School"},
    "LOS ALTOS":       {"district": "Los Altos School District / Mountain View–Los Altos HSD",    "rating": 9,  "notes": "Top-rated district, Los Altos and Mountain View High Schools"},
    "LOS GATOS":       {"district": "Los Gatos Union School District / Los Gatos–Saratoga HSD",   "rating": 9,  "notes": "Excellent schools, Los Gatos High rated 9/10"},
    "MENLO PARK":      {"district": "Menlo Park City School District / Sequoia Union HSD",        "rating": 9,  "notes": "Encinal K-8, Menlo-Atherton High School"},
    "MILLBRAE":        {"district": "Millbrae School District / San Mateo Union HSD",             "rating": 8,  "notes": "Mills High School"},
    "PACIFICA":        {"district": "Jefferson Elementary / Jefferson Union HSD",                  "rating": 7,  "notes": "Terra Nova High School"},
    "PALO ALTO":       {"district": "Palo Alto Unified School District",                           "rating": 10, "notes": "One of the best districts in CA, Gunn and Paly both rated 10/10"},
    "PORTOLA":         {"district": "Portola Valley School District / Sequoia Union HSD",          "rating": 9,  "notes": "Woodside High School, small intimate district"},
    "PORTOLA VALLEY":  {"district": "Portola Valley School District / Sequoia Union HSD",          "rating": 9,  "notes": "Woodside High School, small intimate district"},
    "REDWOOD CITY":    {"district": "Redwood City School District / Sequoia Union HSD",            "rating": 7,  "notes": "Multiple high schools via Sequoia Union HSD"},
    "SAN BRUNO":       {"district": "San Bruno Park School District / San Mateo Union HSD",        "rating": 7,  "notes": "Capuchino High School"},
    "SAN CARLOS":      {"district": "San Carlos School District / Sequoia Union HSD",              "rating": 9,  "notes": "Top-rated elementary district in San Mateo County, Carlmont High School"},
    "SAN MATEO":       {"district": "San Mateo-Foster City School District / San Mateo Union HSD","rating": 8,  "notes": "Multiple high schools, strong programs"},
    "SARATOGA":        {"district": "Saratoga Union School District / Los Gatos–Saratoga HSD",    "rating": 10, "notes": "Among highest-rated in CA, Saratoga High School 10/10"},
    "WOODSIDE":        {"district": "Woodside Elementary School District / Sequoia Union HSD",     "rating": 9,  "notes": "Woodside High School, small class sizes"},
}


def get_school_info(args: dict, db=None):
    """
    Returns (info_text, None).
    db is accepted but unused — school data is hardcoded.
    """
    city = args.get("city", "").upper().strip()

    info = SCHOOL_INFO.get(city)
    if not info:
        # Fuzzy fallback: partial match
        for k, v in SCHOOL_INFO.items():
            if city in k or k in city:
                info = v
                break

    if info:
        txt  = f"Schools in {city.title()}:\n"
        txt += f"• District: {info['district']}\n"
        txt += f"• Rating: {info['rating']}/10\n"
        txt += f"• Notes: {info.get('notes', '')}\n"
        return txt, None

    return (
        f"No detailed school data for {city.title()} in our database. "
        "San Mateo County generally has good schools. "
        "Check GreatSchools.org for current ratings."
    ), None
