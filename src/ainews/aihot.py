"""Anonymous, bounded read-only access to the fixed AIHOT public service."""

import json
import re
import time
from datetime import date
from urllib.parse import urlencode

import httpx

from .net import RetryAfterError, SafeHTTP

ENDPOINT = "https://aihot.news/api/mcp"
BASE = "https://aihot.news"
TOOLS = {
    "aihot_get_latest": {"window", "mode", "category", "limit"},
    "aihot_search": {"q", "window", "category", "limit"},
    "aihot_get_hot_topics": {"limit"},
    "aihot_get_daily": {"date"},
}
CATEGORIES = {"ai-models", "ai-products", "industry", "paper", "tip", "opinion"}
COLLECTION_TOOLS = {"aihot_get_latest", "aihot_search"}


class AihotError(ValueError):
    """A protocol/data failure whose message contains no remote body text."""


def validate_collection_source(source):
    """Collection is a fixed anonymous service, with explicit configured queries."""
    if source.url != ENDPOINT or source.allowed_domains != ["aihot.news"] or source.region != "global":
        raise AihotError("AIHOT source requires the fixed public endpoint and global region")
    if source.mcp_tool not in COLLECTION_TOOLS:
        raise AihotError("AIHOT collection supports only latest and search tools")
    required = {"window", "limit"} | ({"mode"} if source.mcp_tool == "aihot_get_latest" else set())
    if not required <= source.mcp_arguments.keys():
        raise AihotError("AIHOT collection requires explicit window, limit and latest mode")
    queries = source.queries if source.mcp_tool == "aihot_search" else [None]
    if not queries:
        raise AihotError("AIHOT search requires configured queries")
    for query in queries:
        AihotClient._validate_arguments(source.mcp_tool, source.mcp_arguments | ({"q": query} if query is not None else {}))


class AihotClient:
    """Reuse one instance per collection run; the caller owns SafeHTTP's lifetime."""

    def __init__(self, http: SafeHTTP, sleep=time.sleep):
        self.http = http
        self.sleep = sleep
        self.last_transport = None
        self.last_warning = None
        self._initialized = False
        self._readonly_tools = set()
        self._request_id = 0
        self._session_id = None

    def call(self, tool_name, arguments):
        args = self._validate_arguments(tool_name, arguments)
        self.last_transport = None
        self.last_warning = None
        try:
            self._initialize()
            if tool_name not in self._readonly_tools:
                raise AihotError("Tool missing verified read-only annotation")
            result = self._rpc("tools/call", {"name": tool_name, "arguments": args})
            if result.get("isError"):
                raise AihotError("MCP tool reported an error")
            data = self._validate_data(result.get("structuredContent"))
            self.last_transport = "mcp"
            return data
        except RetryAfterError:
            self.last_warning = "MCP server cooldown"
            raise
        except (AihotError, httpx.HTTPError, ValueError, OSError) as exc:
            self.last_warning = "MCP failed: " + self._diagnostic(exc)
        try:
            data = self._rest(tool_name, args)
        except RetryAfterError:
            raise
        except (ValueError, httpx.HTTPError, OSError) as exc:
            raise AihotError("AIHOT REST failed: " + self._diagnostic(exc)) from None
        self.last_transport = "rest"
        return data

    @staticmethod
    def _diagnostic(exc):
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        return type(exc).__name__

    @staticmethod
    def _validate_arguments(tool, arguments):
        if not isinstance(tool, str) or tool not in TOOLS:
            raise AihotError("Unsupported read-only tool")
        if not isinstance(arguments, dict) or set(arguments) - TOOLS[tool]:
            raise AihotError("Unsupported tool arguments")
        args = dict(arguments)
        if "window" in args and args["window"] not in ("24h", "7d"):
            raise AihotError("Invalid window")
        if "mode" in args and args["mode"] not in ("selected", "all"):
            raise AihotError("Invalid mode")
        if "category" in args and (not isinstance(args["category"], str) or args["category"] not in CATEGORIES):
            raise AihotError("Invalid category")
        if "limit" in args and (type(args["limit"]) is not int or not 1 <= args["limit"] <= (10 if tool == "aihot_get_hot_topics" else 30)):
            raise AihotError("Invalid limit")
        if tool == "aihot_search":
            if not isinstance(args.get("q"), str) or not 2 <= len(args["q"].strip()) <= 200:
                raise AihotError("Search query must contain 2 to 200 characters")
        if "date" in args:
            value = args["date"]
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise AihotError("Invalid daily date")
            try:
                date.fromisoformat(value)
            except ValueError:
                raise AihotError("Invalid daily date") from None
        return args

    @staticmethod
    def _validate_data(data):
        if not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
            raise AihotError("Unsupported or missing structured schemaVersion")
        return data

    def _initialize(self):
        if self._initialized:
            return
        result = self._rpc("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "GlobalAIDaily", "version": "0.1.0"},
        })
        if result.get("protocolVersion") != "2025-03-26":
            raise AihotError("Unsupported MCP protocolVersion")
        self._rpc("notifications/initialized", notification=True)
        result = self._rpc("tools/list")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise AihotError("Invalid MCP tools list")
        self._readonly_tools = {
            tool["name"] for tool in tools if isinstance(tool, dict)
            and isinstance(tool.get("name"), str) and tool["name"] in TOOLS
            and isinstance(tool.get("annotations"), dict)
            and tool["annotations"].get("readOnlyHint") is True
        }
        self._initialized = True

    def _rpc(self, method, params=None, *, notification=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            self._request_id += 1
            payload["id"] = self._request_id
        if params is not None:
            payload["params"] = params
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"}
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        response = self.http.post_json(ENDPOINT, payload, ["aihot.news"], headers=headers, sleep=self.sleep)
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if notification:
            if response.status_code not in {200, 202, 204}:
                raise AihotError("MCP notification not accepted")
            return None
        if response.headers.get("content-type", "").split(";")[0].strip().lower() == "text/event-stream":
            messages = self._sse_messages(response.text)
        else:
            try:
                messages = [response.json()]
            except ValueError:
                raise AihotError("Invalid MCP JSON response") from None
        for message in messages:
            if not isinstance(message, dict) or type(message.get("id")) is not int or message["id"] != payload["id"]:
                continue
            if message.get("jsonrpc") != "2.0" or "error" in message or not isinstance(message.get("result"), dict):
                raise AihotError("Invalid MCP result or JSONRPC error")
            return message["result"]
        raise AihotError("Missing matching MCP response id")

    @staticmethod
    def _sse_messages(text):
        # Parse only data fields; notifications and unrelated response IDs are discarded by _rpc.
        data = []
        messages = []
        for line in text.splitlines() + [""]:
            if not line:
                if data:
                    try:
                        messages.append(json.loads("\n".join(data)))
                    except ValueError:
                        pass
                    data = []
            elif line.startswith("data:"):
                value = line[5:]
                data.append(value[1:] if value.startswith(" ") else value)
        return messages

    def _rest(self, tool, args):
        if tool == "aihot_get_latest":
            path, query = "/api/v1/items", args
        elif tool == "aihot_search":
            path, query = "/api/v1/items", {"q": args["q"], "window": args.get("window", "7d")}
            query.update({key: value for key, value in args.items() if key not in {"q", "window"}})
            query["mode"] = "selected"
        elif tool == "aihot_get_hot_topics":
            path, query = "/api/v1/hot-topics", {}
        else:
            path, query = "/api/v1/dailies/" + args.get("date", "latest"), {}
        def fetch():
            url = BASE + path + ("?" + urlencode(query) if query else "")
            response = self.http.get(url, ["aihot.news"], sleep=self.sleep,
                                     headers={"User-Agent": "aihot-api/2.0.0 GlobalAIDaily"})
            return self._validate_data(response.json())
        data = fetch()
        if tool == "aihot_search" and data.get("items") == []:
            query["mode"] = "all"
            data = fetch()
        if tool == "aihot_get_hot_topics" and isinstance(data.get("items"), list):
            data = {**data, "items": data["items"][:args.get("limit", 10)]}
            data["count"] = len(data["items"])
        return data
