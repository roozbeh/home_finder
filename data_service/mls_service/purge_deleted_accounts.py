#!/usr/bin/env python3
"""
purge_deleted_accounts.py — hard-delete accounts soft-deleted 7+ days ago.

Runs weekly via cron. Removes the user record and all associated data
(chat_sessions, listing_feedback). Keeps listing_feedback rows for
analytics by default but clears the user_id reference so they are
effectively anonymised — change ANONYMISE_FEEDBACK to False to hard-delete.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MONGO_URI  = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB   = os.environ.get("MONGO_DB", "mls")
CUTOFF_DAYS = 7
ANONYMISE_FEEDBACK = True   # True = blank user_id; False = hard-delete feedback rows

mongo = MongoClient(MONGO_URI)
db    = mongo[MONGO_DB]

cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)

users = list(db.users.find({
    "is_deleted": True,
    "deleted_at": {"$lte": cutoff},
}, {"user_id": 1}))

if not users:
    logging.info("[purge] no accounts eligible for deletion")
else:
    for user in users:
        uid = user["user_id"]
        # Remove all chat sessions
        sessions_result = db.chat_sessions.delete_many({"user_id": uid})
        # Anonymise or remove feedback
        if ANONYMISE_FEEDBACK:
            db.listing_feedback.update_many({"user_id": uid}, {"$set": {"user_id": ""}})
        else:
            db.listing_feedback.delete_many({"user_id": uid})
        # Hard-delete the user record
        db.users.delete_one({"user_id": uid})
        logging.info("[purge] deleted user_id=%s  sessions_removed=%d",
                     uid, sessions_result.deleted_count)

    logging.info("[purge] done — purged %d account(s)", len(users))

mongo.close()
