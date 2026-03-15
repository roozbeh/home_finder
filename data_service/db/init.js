// MongoDB initialization script
// Runs once when the container is first started with a fresh data volume.
// Creates collections and indexes for the MLS application.

const db = db.getSiblingDB('mls');

// ── auth_tokens ───────────────────────────────────────────────────────────────
// Stores browser session cookies captured after each MLS login.
db.createCollection('auth_tokens');
db.auth_tokens.createIndex({ timestamp: -1 });   // latest-first lookup

// ── mls_listings ──────────────────────────────────────────────────────────────
// Stores MLS property listings. LISTING_ID is the natural unique key.
// Numeric fields (price, beds, sqft …) are coerced to numbers by the importer
// so range queries work correctly.
db.createCollection('mls_listings');
db.mls_listings.createIndex({ LISTING_ID: 1 }, { unique: true });
db.mls_listings.createIndex({ CITY: 1 });
db.mls_listings.createIndex({ MLS_STATUS: 1 });
db.mls_listings.createIndex({ LIST_PRICE: 1 });
db.mls_listings.createIndex({ BEDROOMS_TOTAL: 1 });
db.mls_listings.createIndex({ SQFT: 1 });
db.mls_listings.createIndex({ _updated_at: -1 });

// ── mls_runs ──────────────────────────────────────────────────────────────────
// Tracks each execution of get_cookies.py and search_and_store.py.
db.createCollection('mls_runs');
db.mls_runs.createIndex({ timestamp: -1 });
db.mls_runs.createIndex({ type: 1, timestamp: -1 });

// ── mls_details ───────────────────────────────────────────────────────────────
// Stores per-property detail data (fetched individually per DCID).
// Populated by a future detail-fetcher; created here so the collection exists.
db.createCollection('mls_details');
db.mls_details.createIndex({ DCID: 1 }, { unique: true });
db.mls_details.createIndex({ _updated_at: -1 });

print('MLS collections and indexes created successfully.');
