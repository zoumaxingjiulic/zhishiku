import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app import agent_runtime  # noqa: E402
from app.mcp_client import _response_payload  # noqa: E402


def tool(read_only=True):
    return {
        "id": 42,
        "connector_code": "ERP_U9",
        "connector_name": "ERP U9 Cloud",
        "tool_name": "u9_get_item",
        "description": "查询料品",
        "input_schema": {"type": "object", "properties": {"item_code": {"type": "string"}}, "required": ["item_code"]},
        "annotations": {"readOnlyHint": read_only},
    }


def test_response_payload_supports_json_and_sse():
    request = httpx.Request("POST", "http://mcp.local/mcp")
    json_response = httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}, request=request)
    assert _response_payload(json_response, 1)["result"] == {"tools": []}
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n'
    sse_response = httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"}, request=request)
    assert _response_payload(sse_response, 2)["result"]["ok"] is True


def test_only_explicit_read_only_tools_are_exposed():
    definitions, mapping = agent_runtime.openai_tools([tool(True), tool(False)])
    assert len(definitions) == 1
    assert definitions[0]["function"]["name"] == "ERP_U9__u9_get_item"
    assert list(mapping) == ["ERP_U9__u9_get_item"]


def test_context_is_deduplicated_and_bounded():
    units = [
        {"id": 1, "title": "制度", "content_text": "重复 内容 " * 40, "page_start": 1, "page_end": 1},
        {"id": 2, "title": "制度", "content_text": "重复内容" * 40, "page_start": 2, "page_end": 2},
        {"id": 3, "title": "制度", "content_text": "另一段" * 100, "page_start": 3, "page_end": 3},
    ]
    context, selected = agent_runtime.prepare_context(units, max_chars=500)
    assert len(context) <= 500
    assert [item["id"] for item in selected] == [1, 3]


def test_runtime_executes_only_mapped_tool_and_returns_trace(monkeypatch):
    replies = iter([
        {"content": None, "tool_calls": [{"id": "call-1", "type": "function", "function": {
            "name": "ERP_U9__u9_get_item", "arguments": json.dumps({"item_code": "0001"})}}]},
        {"content": "料品 0001 已找到。"},
    ])
    monkeypatch.setattr(agent_runtime, "_chat", lambda *_: next(replies))
    calls = []

    def execute(selected_tool, arguments):
        calls.append((selected_tool["tool_name"], arguments))
        return {"item_code": "0001"}, {"connector": "ERP_U9", "tool": "u9_get_item", "success": True}

    answer, method, events, selected = agent_runtime.generate_agent_answer(
        "只查询授权数据", "查询料号0001", [], [{"role": "user", "content": "上一个问题"}],
        [tool(True)], execute, gateway={"base_url": "http://llm.local/v1", "api_key": "secret", "model_name": "test"},
    )
    assert answer == "料品 0001 已找到。"
    assert method == "llm_tools"
    assert calls == [("u9_get_item", {"item_code": "0001"})]
    assert events[0]["success"] is True
    assert events[0]["connector_tool_id"] == 42
    assert selected == []


def test_unknown_tool_call_is_reported_without_binding_id_and_answer_continues(monkeypatch):
    replies = iter([
        {"content": None, "tool_calls": [{"id": "call-x", "type": "function", "function": {
            "name": "UNKNOWN__write", "arguments": "{}"}}]},
        {"content": "该工具未获授权，未执行。"},
    ])
    monkeypatch.setattr(agent_runtime, "_chat", lambda *_: next(replies))

    answer, method, events, _ = agent_runtime.generate_agent_answer(
        "只调用授权工具", "执行未知工具", [], [], [tool(True)],
        lambda *_: (_ for _ in ()).throw(AssertionError("unknown tool must not execute")),
        gateway={"base_url": "http://llm.local/v1", "api_key": "", "model_name": "test"},
    )

    assert answer == "该工具未获授权，未执行。"
    assert method == "llm_tools"
    assert events[0]["error_code"] == "NOT_AUTHORIZED_OR_BUDGET"
    assert "connector_tool_id" not in events[0]
