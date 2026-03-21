"""Core agentic loop for the Maya home-finder assistant."""

import json
import logging
import time
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
    t_request_start = time.time()

    msgs = _sanitize_messages(list(messages))
    if not msgs:
        return {"error": "No user messages to process."}

    log.info("[agent] ── NEW REQUEST ── session=%s  history_messages=%d",
             session_id, len(msgs))

    all_listings: list = []

    for iteration in range(MAX_ITERATIONS):
        t_llm_start = time.time()
        log.info("[agent] [iter %d] → calling LLM  context_messages=%d",
                 iteration + 1, len(msgs))

        try:
            resp = ai_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=msgs,
            )
        except Exception as e:
            log.error("[agent] [iter %d] ✗ LLM call failed after %.1fs: %s",
                      iteration + 1, time.time() - t_llm_start, e)
            return {"error": f"LLM error: {e}"}

        t_llm_elapsed = time.time() - t_llm_start
        log.info("[agent] [iter %d] ← LLM responded in %.2fs  stop_reason=%s  "
                 "input_tokens=%s  output_tokens=%s",
                 iteration + 1, t_llm_elapsed,
                 resp.stop_reason,
                 getattr(resp.usage, "input_tokens", "?"),
                 getattr(resp.usage, "output_tokens", "?"))

        if resp.stop_reason == "end_turn":
            text = next((b.text for b in resp.content if b.type == "text"), "")
            t_total = time.time() - t_request_start
            log.info("[agent] ✓ DONE  total=%.2fs  reply_chars=%d  listings=%d",
                     t_total, len(text), len(all_listings))
            return {"message": text, "listings": all_listings}

        if resp.stop_reason == "tool_use":
            tool_results = []
            for b in resp.content:
                if b.type == "tool_use":
                    t_tool_start = time.time()
                    log.info("[agent] [iter %d] ⚙ tool_call  name=%s  args=%s",
                             iteration + 1, b.name, b.input)
                    try:
                        result_text, listings = exec_tool(b.name, b.input, db, session_id)
                    except Exception as e:
                        log.error("[agent] [iter %d] ✗ tool %s failed after %.2fs: %s",
                                  iteration + 1, b.name, time.time() - t_tool_start, e)
                        result_text, listings = f"Tool error: {e}", None

                    t_tool_elapsed = time.time() - t_tool_start
                    log.info("[agent] [iter %d] ✓ tool_result  name=%s  elapsed=%.2fs  "
                             "result_chars=%d  listings=%s",
                             iteration + 1, b.name, t_tool_elapsed,
                             len(result_text),
                             len(listings) if listings is not None else "n/a")

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

        log.warning("[agent] [iter %d] unexpected stop_reason=%s — aborting",
                    iteration + 1, resp.stop_reason)
        break

    t_total = time.time() - t_request_start
    log.error("[agent] ✗ FAILED  total=%.2fs  max_iterations=%d reached", t_total, MAX_ITERATIONS)
    return {"error": "Could not generate a response. Please try again."}


def run_agent_streaming(messages: list[dict], session_id: str, db, ai_client):
    """
    Generator that yields SSE-formatted strings.
    Event types:
      {"type": "status",  "text": "Searching for properties..."}
      {"type": "text",    "text": "chunk of assistant text"}
      {"type": "done",    "listings": [...], "full_text": "..."}
      {"type": "error",   "text": "..."}
    """
    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    t_request_start = time.time()
    msgs = _sanitize_messages(list(messages))
    if not msgs:
        yield sse({"type": "error", "text": "No user messages to process."})
        return

    log.info("[agent-stream] ── NEW REQUEST ── session=%s  history_messages=%d",
             session_id, len(msgs))

    all_listings: list = []

    for iteration in range(MAX_ITERATIONS):
        t_llm_start = time.time()
        log.info("[agent-stream] [iter %d] → calling LLM  context_messages=%d",
                 iteration + 1, len(msgs))

        # For the final response, stream tokens. For intermediate tool-use calls,
        # use non-streaming to keep the logic simple.
        try:
            resp = ai_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=msgs,
            )
        except Exception as e:
            log.error("[agent-stream] [iter %d] ✗ LLM error: %s", iteration + 1, e)
            yield sse({"type": "error", "text": f"LLM error: {e}"})
            return

        t_llm_elapsed = time.time() - t_llm_start
        log.info("[agent-stream] [iter %d] ← LLM %.2fs  stop_reason=%s",
                 iteration + 1, t_llm_elapsed, resp.stop_reason)

        if resp.stop_reason == "end_turn":
            text = next((b.text for b in resp.content if b.type == "text"), "")
            # Stream the text word-by-word so the frontend feels live
            words = text.split(" ")
            for i, word in enumerate(words):
                chunk = word if i == 0 else " " + word
                yield sse({"type": "text", "text": chunk})

            t_total = time.time() - t_request_start
            log.info("[agent-stream] ✓ DONE  total=%.2fs  listings=%d", t_total, len(all_listings))
            yield sse({"type": "done", "listings": all_listings, "full_text": text})
            return

        if resp.stop_reason == "tool_use":
            tool_results = []
            for b in resp.content:
                if b.type == "tool_use":
                    # Emit a status message before executing the tool
                    status_map = {
                        "search_listings": "Searching for properties...",
                        "get_school_info": "Looking up school info...",
                        "save_contact":    "Saving your contact info...",
                    }
                    status_text = status_map.get(b.name, f"Running {b.name}...")
                    yield sse({"type": "status", "text": status_text})

                    t_tool_start = time.time()
                    log.info("[agent-stream] [iter %d] ⚙ tool=%s  args=%s",
                             iteration + 1, b.name, b.input)
                    try:
                        result_text, listings = exec_tool(b.name, b.input, db, session_id)
                    except Exception as e:
                        log.error("[agent-stream] [iter %d] ✗ tool %s failed: %s",
                                  iteration + 1, b.name, e)
                        result_text, listings = f"Tool error: {e}", None

                    log.info("[agent-stream] [iter %d] ✓ tool=%s  elapsed=%.2fs",
                             iteration + 1, b.name, time.time() - t_tool_start)

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

        log.warning("[agent-stream] [iter %d] unexpected stop_reason=%s",
                    iteration + 1, resp.stop_reason)
        break

    t_total = time.time() - t_request_start
    log.error("[agent-stream] ✗ FAILED  total=%.2fs", t_total)
    yield sse({"type": "error", "text": "Could not generate a response. Please try again."})
