from pathlib import Path

from flip_finder.sources.csv_import import read_csv


def test_csv_import_is_nullable_and_does_not_impute_missing_price(tmp_path: Path):
    file = tmp_path / "authorized.csv"
    file.write_text("LISTING_ID,MLS_STATUS,LIST_PRICE,CITY,STREET_ADDRESS,PROPERTY_TYPE\nA1,ACTV,,Daly City,1 Test Ave,Single Family\n")
    listing = next(read_csv(file))
    assert listing.source == "authorized_csv"
    assert listing.list_price is None
    assert "missing_list_price" in listing.data_quality_flags
