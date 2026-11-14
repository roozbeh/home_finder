# Flip Detector Refresh

The daily `flip_detector_refresh` job materializes `flip_finder.flipdb` from
the source MLS collection `mls.mls_listings`. It reviews all observed closed
sales, links consecutive sales of the same APN (or, when no APN exists, a
clearly marked normalized-address fallback), and retains pairs held for 14–365
days.

Each `flipdb` record contains `buy_mls`, `sell_mls`, purchase/resale dates and
prices, hold duration, gross spread/ROI, match confidence, and source
provenance. It is an **observed short-hold resale**, not a verified renovation,
arm's-length transaction, or net-profit calculation. `is_verified_flip` is
always `false`.

The job upserts by stable `pair_key`. It does not modify `mls.mls_listings`.
If a later full recomputation no longer finds a pair, the prior record remains
for audit history and is marked `active: false`.

Run manually on the server only when an initial materialization is needed:

```sh
/home/ubuntu/home_finder/ops/aws/flip_detector_refresh.sh
```

The installed cron entry writes to:

```text
/mnt/extra500/flip_finder/logs/flip_detector_refresh.log
```

It has no MLS browser-session dependency because it reads the already-ingested
Atlas source data only.
