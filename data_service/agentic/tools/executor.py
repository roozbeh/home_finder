"""Dispatches tool calls to their implementations."""

from agentic.tools.search_listings import search_listings
from agentic.tools.get_school_info import get_school_info
from agentic.tools.save_contact import save_contact

_REGISTRY = {
    "search_listings": search_listings,
    "get_school_info": get_school_info,
    "save_contact":    save_contact,
}


def exec_tool(name: str, args: dict, db, session_id: str = ""):
    """
    Dispatch a tool call by name.
    Returns (result_text, listings_or_None).
    """
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}", None

    if name == "save_contact":
        return fn(args, db, session_id)
    return fn(args, db)
