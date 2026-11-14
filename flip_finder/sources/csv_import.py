"""Import explicitly authorized MLS/property exports without assuming an API."""
from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from ..normalize import Listing, normalize_mongo_listing


def read_csv(path: Path, source: str = "authorized_csv") -> Iterator[Listing]:
    """Yield normalized rows from an authorized CSV.

    The CSV itself is not copied or uploaded. Header names may use the MLS-like
    names consumed by ``normalize_mongo_listing``. Empty critical fields remain
    null and are later excluded by candidate rules.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            yield normalize_mongo_listing(dict(row), source=source)
