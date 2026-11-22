# Buyer catalog publisher

The publisher runs in the Home Finder checkout and writes a private SQLite catalog from MongoDB. It runs once at startup and repeats after each configured interval. Docker restarts it after a process or Docker service restart unless an operator stopped it. It has no incoming web port.

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
sqlite3 -readonly /mnt/extra500/flip_finder/buyer-publisher/catalog/catalog.sqlite \
  'PRAGMA quick_check; SELECT count(*) FROM homes;'
```

To stop publication, use the same Compose settings and `stop buyer-publisher`. To roll back code, rebuild from the last working checkout with the private inputs and output directory intact. A buyer web service needs a separately configured read-only mount of the catalog directory and approved HTTPS routing.
