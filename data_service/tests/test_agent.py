"""Tests for the agentic loop (agent.py)."""

import pytest
from unittest.mock import MagicMock
from agentic.agent import run_agent, _sanitize_messages


# ── _sanitize_messages ────────────────────────────────────────────────────────

class TestSanitizeMessages:
    def test_strips_leading_assistant_message(self):
        msgs = [
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user",      "content": "I want a 3br home"},
        ]
        result = _sanitize_messages(msgs)
        assert result[0]["role"] == "user"
        assert len(result) == 1

    def test_passes_through_user_first(self):
        msgs = [
            {"role": "user",      "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = _sanitize_messages(msgs)
        assert result == msgs

    def test_empty_list_returns_empty(self):
        assert _sanitize_messages([]) == []

    def test_only_assistant_messages_returns_empty(self):
        msgs = [{"role": "assistant", "content": "greeting"}]
        result = _sanitize_messages(msgs)
        assert result == []

    def test_multiple_leading_assistant_messages_stripped(self):
        msgs = [
            {"role": "assistant", "content": "Msg 1"},
            {"role": "assistant", "content": "Msg 2"},
            {"role": "user",      "content": "Actual user turn"},
        ]
        result = _sanitize_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"


# ── run_agent ─────────────────────────────────────────────────────────────────

class TestRunAgent:
    def test_end_turn_returns_message(self, mock_db, ai_end_turn):
        msgs = [{"role": "user", "content": "Hello"}]
        result = run_agent(msgs, "sess-1", mock_db, ai_end_turn)
        assert "message" in result
        assert "error" not in result
        assert result["message"] == "Hello! How can I help you?"

    def test_empty_listings_when_no_tool_called(self, mock_db, ai_end_turn):
        msgs = [{"role": "user", "content": "Hello"}]
        result = run_agent(msgs, "sess-1", mock_db, ai_end_turn)
        assert result["listings"] == []

    def test_strips_leading_assistant_before_api_call(self, mock_db, ai_end_turn):
        msgs = [
            {"role": "assistant", "content": "Hi, I'm Maya"},
            {"role": "user",      "content": "Show me homes in Belmont"},
        ]
        result = run_agent(msgs, "sess-1", mock_db, ai_end_turn)
        # Should succeed — no error from Anthropic about leading assistant msg
        assert "error" not in result
        # API was called with only the user message
        call_args = ai_end_turn.messages.create.call_args
        sent_msgs = call_args[1]["messages"]
        assert sent_msgs[0]["role"] == "user"

    def test_no_user_messages_returns_error(self, mock_db, ai_end_turn):
        result = run_agent([], "sess-1", mock_db, ai_end_turn)
        assert "error" in result

    def test_tool_use_then_end_turn(self, mock_db, ai_tool_then_end):
        client, db = ai_tool_then_end
        msgs = [{"role": "user", "content": "Find me a 3br in Belmont"}]
        result = run_agent(msgs, "sess-1", db, client)
        assert "message" in result
        assert result["message"] == "I found some great options in Belmont!"
        # API was called twice: once for tool, once for final reply
        assert client.messages.create.call_count == 2

    def test_tool_use_populates_listings(self, mock_db, ai_tool_then_end):
        client, db = ai_tool_then_end
        msgs = [{"role": "user", "content": "Find me homes in Belmont"}]
        result = run_agent(msgs, "sess-1", db, client)
        assert len(result["listings"]) == 2

    def test_unexpected_stop_reason_returns_error(self, mock_db):
        bad_resp = MagicMock()
        bad_resp.stop_reason = "max_tokens"
        bad_resp.content = []

        client = MagicMock()
        client.messages.create.return_value = bad_resp

        msgs = [{"role": "user", "content": "Hello"}]
        result = run_agent(msgs, "sess-1", mock_db, client)
        assert "error" in result

    def test_session_id_passed_to_tool(self, mock_db):
        """save_contact should receive the session_id from run_agent."""
        # Build a response that calls save_contact
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id   = "tool-1"
        tool_block.name = "save_contact"
        tool_block.input = {"name": "Alice", "email": "alice@example.com"}

        tool_resp = MagicMock()
        tool_resp.stop_reason = "tool_use"
        tool_resp.content = [tool_block]

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Contact saved!"
        end_resp = MagicMock()
        end_resp.stop_reason = "end_turn"
        end_resp.content = [text_block]

        client = MagicMock()
        client.messages.create.side_effect = [tool_resp, end_resp]

        msgs = [{"role": "user", "content": "Save my contact"}]
        run_agent(msgs, "my-session-id", mock_db, client)

        doc = mock_db.contacts.insert_one.call_args[0][0]
        assert doc["session_id"] == "my-session-id"
