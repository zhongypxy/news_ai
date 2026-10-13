import json
from datetime import timedelta

import httpx
import openai
from test_analysis import NOW, settings

from ainews.ai import AIClient
from ainews.models import Source


def test_formal_sdk_web_search_payload_and_provenance():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        text = json.dumps({"items": [{"title": "Model launch", "url": "https://example.com/news",
                         "published_at": None, "excerpt": "Official release", "region": "asia"}],
                         "checked_watchlist": [], "limitations": []})
        output = [
            {"id": "ws_test", "type": "web_search_call", "status": "completed", "action": {"type": "search",
             "query": "AI news", "sources": [{"type": "url", "url": "https://example.com/news"}]}},
            {"id": "msg_test", "type": "message", "role": "assistant", "status": "completed", "content": [
             {"type": "output_text", "text": text, "annotations": []}]}]
        return httpx.Response(200, json={"id": "resp_test", "object": "response", "created_at": 1,
                            "status": "completed", "model": "gpt-5.4-mini", "output": output,
                            "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200,
                            "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}})
    sdk = openai.OpenAI(api_key="unit-test-placeholder", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    client = AIClient(settings(max_search_tool_calls=4), sdk=sdk)
    source = Source(id="search_models", kind="web_search", queries=["AI news"], categories=["models"])
    context = {"WINDOW_START": NOW-timedelta(days=5), "WINDOW_END": NOW, "WATCHLIST": [], "HISTORY": {}}
    items = client.search(source, NOW, context)
    assert len(items) == 1 and items[0].publication_basis == "search_unverified"
    body = requests[0]
    assert body["tools"][0]["type"] == "web_search" and body["tool_choice"] == "required"
    assert body["text"]["format"]["strict"] and body["store"] is False and body["max_tool_calls"] == 4
    input_data = json.loads(body["input"][0]["content"])
    assert input_data["SEARCH_START"] == str(NOW-timedelta(days=5))
    assert client.ledger.entries[0]["web_search_calls"] == 1
    source.id = "search_backfill"
    client.search(source, NOW, context)
    assert json.loads(requests[1]["input"][0]["content"])["SEARCH_START"] == str(NOW-timedelta(days=3))
    sdk.close()
