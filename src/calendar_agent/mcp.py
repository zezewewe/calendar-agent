"""Minimal MCP server (stdio JSON-RPC) so an MCP client drives the same Agent as Telegram.

Run with `calendar-agent mcp`. It speaks MCP over stdin/stdout and wraps the
channel-agnostic Agent, so nothing is written until `confirm` is called - the same
safety the chat flow has.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .agent import Agent
from .models import Inbound
from .render import render, render_agenda

PROTOCOL_VERSION = "2024-11-05"

_TOOL_SPECS = [
    {"name": "agenda", "description": "List upcoming events over the next N days.",
     "inputSchema": {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 365}}, "additionalProperties": False}},
    {"name": "list_events", "description": "List events between two dates (YYYY-MM-DD, inclusive).",
     "inputSchema": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
                     "required": ["start", "end"], "additionalProperties": False}},
    {"name": "propose_change",
     "description": "Turn a natural-language instruction into a proposed change set (create/update/delete/query). "
                    "Nothing is written until confirm is called with the returned proposal_id.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}},
    {"name": "confirm", "description": "Apply a proposal returned by propose_change.",
     "inputSchema": {"type": "object", "properties": {"proposal_id": {"type": "string"}}, "required": ["proposal_id"], "additionalProperties": False}},
    {"name": "undo", "description": "Reverse a confirmed proposal.",
     "inputSchema": {"type": "object", "properties": {"proposal_id": {"type": "string"}}, "required": ["proposal_id"], "additionalProperties": False}},
]


def _handlers(agent: Agent, user_id: str, tz: ZoneInfo) -> dict[str, Callable[[dict], str]]:
    def aware(value: str) -> datetime:
        return datetime.fromisoformat(value).replace(tzinfo=tz)

    def agenda(args: dict) -> str:
        days = int(args.get("days") or 7)
        now = datetime.now(tz)
        return render_agenda(agent.agenda(now, now + timedelta(days=days)))

    def list_events(args: dict) -> str:
        return render_agenda(agent.agenda(aware(args["start"]), aware(args["end"]) + timedelta(days=1)))

    def propose_change(args: dict) -> str:
        inbound = Inbound(user_id=user_id, text=args["text"], received_at=datetime.now(tz))
        p = agent.propose(inbound)
        body = render(p)
        return f"{body}\n\nproposal_id: {p.id}" if p.events else body

    def confirm(args: dict) -> str:
        ids = agent.confirm(args["proposal_id"], user_id)
        return "No such proposal (or already handled)." if ids is None else f"Applied {len(ids)} change(s)."

    def undo(args: dict) -> str:
        return f"Reverted {agent.undo(args['proposal_id'], user_id)} change(s)."

    return {"agenda": agenda, "list_events": list_events, "propose_change": propose_change,
            "confirm": confirm, "undo": undo}


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(message: dict, handlers: dict[str, Callable[[dict], str]]) -> dict | None:
    method, rid = message.get("method"), message.get("id")
    if method == "initialize":
        return _result(rid, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                             "serverInfo": {"name": "calendar-agent", "version": "0.1.0"}})
    if method == "tools/list":
        return _result(rid, {"tools": _TOOL_SPECS})
    if method == "tools/call":
        params = message.get("params") or {}
        handler = handlers.get(params.get("name"))
        if handler is None:
            return _error(rid, -32602, f"unknown tool {params.get('name')!r}")
        try:
            text = handler(params.get("arguments") or {})
        except Exception as e:  # noqa: BLE001 - surface tool failures to the client
            return _error(rid, -32000, str(e))
        return _result(rid, {"content": [{"type": "text", "text": text}], "isError": False})
    if rid is None:  # a notification we don't handle; stay silent
        return None
    return _error(rid, -32601, f"unknown method {method!r}")


def serve(agent: Agent, user_id: str, tz: ZoneInfo, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    handlers = _handlers(agent, user_id, tz)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message, handlers)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
