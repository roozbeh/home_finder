"""Core agentic loop for the Maya home-finder assistant."""

import logging
from agentic.prompts import SYSTEM_PROMPT, TOOLS
from agentic.tools.executor import exec_tool

MAX_ITERATIONS = 8

log = logging.getLogger(__name__)


def _blocks_to_dicts(content) -> list[dict]:
    """Convert Anthropic SDK content blocks to plain serialisable dicts."""
    result = []
    for b in content:
        if b.type == "text":
            result.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            result.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return result


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    """
    Anthropic API requires the first message to have role='user'.
    Strip any leading assistant messages (e.g. the greeting injected client-side).
    """
    start = 0
    while start < len(messages) and messages[start]["role"] != "user":
        start += 1
    return messages[start:]


def run_agent(messages: list[dict], session_id: str, db, ai_client) -> dict:
    """
    Run the agentic loop.

    Args:
        messages:   Conversation history in Anthropic format [{role, content}].
                    May start with an assistant message — it will be stripped.
        session_id: Client session UUID (passed to tools that need it).
        db:         PyMongo database handle.
        ai_client:  anthropic.Anthropic instance.

    Returns:
        dict with keys:
            "message"  — assistant reply text (str)
            "listings" — list of listing dicts (may be empty)
        or:
            "error"    — error message string
    """
    msgs = _sanitize_messages(list(messages))
    if not msgs:
        return {"error": "No user messages to process."}

    log.info("[agent] session=%s  starting with %d message(s)", session_id, len(msgs))

    all_listings: list = []

    for iteration in range(MAX_ITERATIONS):
        log.info("[agent] iteration=%d  calling LLM (messages in context: %d)", iteration + 1, len(msgs))

        try:
            resp = ai_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=msgs,
            )
        except Exception as e:
            log.error("[agent] LLM call failed: %s", e)
            return {"error": f"LLM error: {e}"}

        log.info("[agent] LLM stop_reason=%s", resp.stop_reason)

        if resp.stop_reason == "end_turn":
            text = next((b.text for b in resp.content if b.type == "text"), "")
            log.info("[agent] end_turn  reply_length=%d  listings=%d", len(text), len(all_listings))
            return {"message": text, "listings": all_listings}

        if resp.stop_reason == "tool_use":
            tool_results = []
            for b in resp.content:
                if b.type == "tool_use":
                    log.info("[agent] tool_call  name=%s  args=%s", b.name, b.input)
                    try:
                        result_text, listings = exec_tool(b.name, b.input, db, session_id)
                    except Exception as e:
                        log.error("[agent] tool %s failed: %s", b.name, e)
                        result_text, listings = f"Tool error: {e}", None

                    log.info("[agent] tool_result  name=%s  result_length=%d  listings=%s",
                             b.name, len(result_text), len(listings) if listings is not None else "n/a")

                    if listings is not None:
                        all_listings = listings
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": b.id,
                        "content":     result_text,
                    })

            msgs = msgs + [
                {"role": "assistant", "content": _blocks_to_dicts(resp.content)},
                {"role": "user",      "content": tool_results},
            ]
            continue

        log.warning("[agent] unexpected stop_reason=%s — aborting", resp.stop_reason)
        break

    return {"error": "Could not generate a response. Please try again."}
