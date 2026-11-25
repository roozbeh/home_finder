# Retired buyer catalog publisher

The buyer site reads eligible properties directly from `mls.mls_listings` in Atlas. The former publisher built a SQLite snapshot of public fields and photo references. The Atlas service uses the existing MLS display policy and opaque listing ID key to preserve public URLs.

See the [buyer rollout guide](BUYER_ROLLOUT.md) for service commands and the [cleanup record](../../cluster_cleanup.md) for the migration and disposition of the old stores.
