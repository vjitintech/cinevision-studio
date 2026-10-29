"""The optional LLM adapter only ever maps text onto the closed op list, and only sends text."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cinevision import config, db
from cinevision.adapters import llm, registry
from cinevision.concepts import refine as R


class FakeOllama(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.seen.append(body)
        reply = {"ops": [{"op": "op_warmer", "shot": None}, {"op": "rm -rf /", "shot": 1}, {"op": "op_tighter", "shot": 2}]}
        out = json.dumps({"message": {"content": json.dumps(reply)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def ollama(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = config.Settings(data_dir=tmp_path, master_key=os.urandom(32),
                        ollama_url=f"http://127.0.0.1:{srv.server_port}", ollama_model="test-model")
    for d in (s.data_dir, s.blob_dir, s.tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
    config.reset_settings(s)
    db.close_thread_connection()
    db.init_db()
    yield srv
    srv.shutdown()
    db.close_thread_connection()
    config.reset_settings(None)


def test_local_ollama_is_available_without_external_consent(ollama):
    st = registry.adapter_status("ollama", None)
    assert st["status"] == "available" and st["external"] is False
    ops, model = llm.interpret("u", "make it feel like a summer afternoon", [fn.__name__ for _, fn in R.RULES], 2)
    assert model == "ollama"
    assert ops == [{"op": "op_warmer", "shot": None, "n": 3}, {"op": "op_tighter", "shot": 1, "n": 3}]  # unknown op dropped
    sent = json.dumps(FakeOllama.seen[-1])
    assert "summer afternoon" in sent and "data:image" not in sent


def test_external_llm_requires_consent(tmp_path):
    s = config.Settings(data_dir=tmp_path, master_key=os.urandom(32), external_llm_url="https://api.example.com",
                        external_llm_model="m", external_llm_key="k")
    for d in (s.data_dir, s.blob_dir, s.tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
    config.reset_settings(s)
    db.close_thread_connection()
    db.init_db()
    try:
        assert registry.adapter_status("external-llm", None)["status"] == "needs_consent"
        assert llm.pick("nobody") is None
        with pytest.raises(llm.AdapterUnavailable):
            llm.interpret("nobody", "x", ["op_warmer"], 1)
    finally:
        db.close_thread_connection()
        config.reset_settings(None)
