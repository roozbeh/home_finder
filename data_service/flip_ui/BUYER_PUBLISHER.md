# Buyer catalog publisher

The publisher runs in the Home Finder checkout and writes a private SQLite catalog from MongoDB. It runs once at startup and repeats after each configured interval. Docker restarts it after a process or Docker service restart unless an operator stopped it. It has no incoming web port.
The publisher uses the existing MongoDB worker’s seccomp compatibility setting for Docker 18.09. It runs unprivileged with a read-only root filesystem.

## Server setup

Use a private operator-owned base directory. Create an input directory with the reviewed `live-policy.json` and stable `public-id-key` as used by the intended buyer catalog. Put approved listing media in `input/media` if the policy references local files. Use an output directory with enough room for two catalog files during atomic replacement. Restrict both directories to the operator or intended reader.

Create a mode-0600 Compose settings file outside Git. The values below illustrate the server layout.

```dotenv
BUYER_PUBLISH_INPUT_DIR=/mnt/extra500/flip_finder/buyer-publisher/input
BUYER_PUBLISHED_DIR=/mnt/extra500/flip_finder/buyer-publisher/catalog
BUYER_MLS_PUBLISHER_ENV_FILE=/mnt/extra500/flip_finder/buyer-publisher/mongo.env
BUYER_PUBLISH_INTERVAL=900
```

The Mongo environment file supplies `MONGO_URI` and optional `MONGO_DB` and `FLIPDB_DATABASE`. The publisher only reads MongoDB. Prefer dedicated read-only database credentials when available. Set `BUYER_PUBLISHER_UID` and `BUYER_PUBLISHER_GID` if the operator UID and GID differ from 1000.

From the repository root, after pulling the intended commit, use this Compose project name to keep the publisher lifecycle independent of web and ingestion deploys.

```sh
docker compose -p home-finder-buyer-publisher --env-file /mnt/extra500/flip_finder/buyer-publisher/compose.env \
  -f data_service/docker-compose.buyer-publisher.yml up -d --build buyer-publisher

docker compose -p home-finder-buyer-publisher --env-file /mnt/extra500/flip_finder/buyer-publisher/compose.env \
  -f data_service/docker-compose.buyer-publisher.yml logs --tail 20 buyer-publisher
```

Verify the log reports a successful publication and check the file:

```sh
python3 - <<'PYTHON'
import sqlite3

uri = 'file:/mnt/extra500/flip_finder/buyer-publisher/catalog/catalog.sqlite?mode=ro'
with sqlite3.connect(uri, uri=True) as catalog:
    print(catalog.execute('PRAGMA quick_check').fetchone()[0])
    print(catalog.execute('SELECT count(*) FROM homes').fetchone()[0])
PYTHON
```

To stop publication, use the same Compose settings and `stop buyer-publisher`. To roll back code, rebuild from the last working checkout with the private inputs and output directory intact. A buyer web service needs a separately configured read-only mount of the catalog directory and approved HTTPS routing.

## MLS listing record

Source and listing credit permissions control the attribution. The MLS number also requires a publishable address because external lookups can reveal the street address. With source and listing freshness permissions, the listing record shows the ConnectMLS modification date and the UTC time Flip Finder checked the listing details. Older catalogs can show the inventory check date until republished.

## Listing photos

When a source and listing allow media, the publisher uses the gallery URLs supplied by ConnectMLS. MLSListings serves JPEGs through an extensionless endpoint, and CRMLS can supply a JPEG with the `X-Large` preset. The buyer service checks the URL again, validates the image, strips metadata and rechecks publication before serving it. Photo categories add browsing information independently of basic photo availability. Sold listing photos also require the source's `publish_sold_photos` setting.

## Property type categories

The publisher stores a buyer-facing category only when the source and listing both allow property type, and the feed has a recognized classification. It prefers the more specific ConnectMLS detail field. It can use a listing-level text label when the detail supplies an unknown code. For example, a record with detail `107` and listing-level `Farm / Ranch` uses the explicit ranch label. A standalone `109` remains uncategorized. Listings with uncertain or withheld classifications remain available under **Any**.

The same property type selections apply to result counts, cards and map locations. Multi-select URLs repeat the `property_type` parameter. Earlier links such as `?property_type=Condo` still resolve to the matching group. A category can show zero results when the current feed lacks eligible listings.
