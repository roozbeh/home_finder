"""save_contact tool — persists user contact info in MongoDB."""

from datetime import datetime, timezone


def save_contact(args: dict, db, session_id: str = ""):
    """
    Returns (confirmation_text, None).
    Inserts a document into the contacts collection.
    """
    db.contacts.insert_one({
        "name":        args.get("name"),
        "email":       args.get("email"),
        "phone":       args.get("phone", ""),
        "preferences": args.get("preferences", {}),
        "session_id":  session_id,
        "created_at":  datetime.now(timezone.utc),
    })
    return f"Contact saved for {args['name']} ({args['email']}). We will be in touch!", None
