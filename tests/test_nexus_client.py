import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mesabot.nexus import NexusClient, NexusError


class FakeMCP(BaseHTTPRequestHandler):
    sessions: set = set()
    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeMCP.seen.append((body.get("method"), self.headers.get("Mcp-Session-Id"),
                             self.headers.get("Authorization"), self.path))
        if body["method"] == "initialize":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "sess-1")
            self.end_headers()
            FakeMCP.sessions.add("sess-1")
            self.wfile.write(json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {}}).encode())
            return
        if body["method"] == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        if self.headers.get("Mcp-Session-Id") not in FakeMCP.sessions:
            self.send_response(404)
            self.end_headers()
            return
        name = body["params"]["name"]
        if name == "boom":
            result = {"isError": True, "content": [{"type": "text", "text": "nope"}]}
        else:
            result = {"content": [{"type": "text", "text": json.dumps({"tool": name, "args": body["params"]["arguments"]})}]}
        msg = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(f"event: message\ndata: {msg}\n\n".encode())


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), FakeMCP)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/mcp"
    httpd.shutdown()


def test_initialize_call_sse_and_session_expiry(server):
    client = NexusClient(server, "tok")
    assert client.call_tool("get_me") == {"tool": "get_me", "args": {}}
    assert client.call_tool("list_assignments", {"status": "due", "x": None}) == {
        "tool": "list_assignments", "args": {"status": "due"}}
    assert FakeMCP.seen[0][0] == "initialize" and FakeMCP.seen[0][2] == "Bearer tok"
    assert FakeMCP.seen[0][3] == "/mcp/tok"  # token in the path, as Nexus documents
    assert FakeMCP.seen[2][1] == "sess-1"
    FakeMCP.sessions.clear()  # server forgets the session → client re-initializes
    assert client.call_tool("get_grades")["tool"] == "get_grades"
    with pytest.raises(NexusError, match="nope"):
        client.call_tool("boom")


def test_token_with_pasted_whitespace(server):
    FakeMCP.seen.clear()
    client = NexusClient(server, " tok\n")
    assert client.call_tool("get_me")["tool"] == "get_me"
    assert FakeMCP.seen[0][2] == "Bearer tok" and FakeMCP.seen[0][3] == "/mcp/tok"
