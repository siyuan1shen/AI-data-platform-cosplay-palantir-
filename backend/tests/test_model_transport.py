from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from enterprise_insight_backend.agent_runtime import AgentRuntimeService
from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.model_profiles import LocalSecretVault


def test_external_transport_sends_complete_context_or_stops_before_network(monkeypatch) -> None:
    service = object.__new__(AgentRuntimeService)
    service.settings = Settings(agent_context_max_chars=5000)
    profile = SimpleNamespace(
        provider="OPENAI_COMPATIBLE", encrypted_api_key="synthetic", base_url="https://invalid.test",
        model="synthetic", temperature=0, timeout_seconds=1,
    )
    run = SimpleNamespace(agent_kind="PROJECTION", context_manifest={
        "allow_external_model": True, "share_project_context_with_model": True,
    })
    monkeypatch.setattr(LocalSecretVault, "decrypt", lambda *args: "synthetic-test-only")
    sent = []

    def post(url, **kwargs):
        sent.append(kwargs["json"])
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": '{"content":"ok"}'}}]},
        )

    monkeypatch.setattr("enterprise_insight_backend.agent_runtime.httpx.post", post)
    context = {"available_actions": [{"key": "create_relation"}], "agent_execution": {
        "tool_results": [{"id": "actual-entity-id", "result": "完整中文引用"}],
    }}
    service._invoke_model(profile, run, "继续", context)
    serialized = sent[0]["messages"][1]["content"].split("：", 1)[1]
    assert json.loads(serialized) == context
    context["material"] = "访谈内容" * 2000
    with pytest.raises(DomainError) as error:
        service._invoke_model(profile, run, "继续", context)
    assert error.value.code == "AGENT_CONTEXT_BUDGET_EXCEEDED"
    assert len(sent) == 1


def test_external_transport_replays_a_real_local_http_round_trip(monkeypatch) -> None:
    received: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            length = int(self.headers["Content-Length"])
            received["authorization"] = self.headers["Authorization"]
            received["body"] = json.loads(self.rfile.read(length))
            content = json.dumps(
                {"content": "本地HTTP回放成功", "citations": [], "action_proposals": []},
                ensure_ascii=False,
            ).encode("utf-8")
            payload = json.dumps({
                "choices": [{"message": {"content": content.decode("utf-8")}}]
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = object.__new__(AgentRuntimeService)
        service.settings = Settings(agent_context_max_chars=5000)
        profile = SimpleNamespace(
            provider="OPENAI_COMPATIBLE",
            encrypted_api_key="synthetic",
            base_url=f"http://127.0.0.1:{server.server_port}",
            model="synthetic-replay",
            temperature=0,
            timeout_seconds=2,
        )
        run = SimpleNamespace(
            agent_kind="PROJECTION",
            context_manifest={
                "allow_external_model": True,
                "share_project_context_with_model": False,
            },
        )
        monkeypatch.setattr(LocalSecretVault, "decrypt", lambda *args: "synthetic-test-only")
        output = service._invoke_model(
            profile,
            run,
            "请确认本地HTTP链路",
            {"available_actions": [{"key": "read_graph_neighborhood"}]},
        )
        assert output.content == "本地HTTP回放成功"
        assert received["authorization"] == "Bearer synthetic-test-only"
        body = received["body"]
        assert body["model"] == "synthetic-replay"
        assert body["messages"][-1] == {"role": "user", "content": "请确认本地HTTP链路"}
        assert "read_graph_neighborhood" in body["messages"][1]["content"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_external_transport_retries_transient_gateway_failure(monkeypatch) -> None:
    attempts = 0

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            content = json.dumps(
                {"content": "重试后成功", "citations": [], "action_proposals": []},
                ensure_ascii=False,
            ).encode("utf-8")
            payload = json.dumps(
                {"choices": [{"message": {"content": content.decode("utf-8")}}]},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = object.__new__(AgentRuntimeService)
        service.settings = Settings(
            agent_context_max_chars=5000,
            agent_model_max_retries=2,
            agent_model_retry_base_seconds=0,
        )
        profile = SimpleNamespace(
            provider="OPENAI_COMPATIBLE",
            encrypted_api_key="synthetic",
            base_url=f"http://127.0.0.1:{server.server_port}",
            model="synthetic-retry",
            temperature=0,
            timeout_seconds=2,
        )
        run = SimpleNamespace(
            agent_kind="PROJECTION",
            context_manifest={"allow_external_model": True},
        )
        monkeypatch.setattr(LocalSecretVault, "decrypt", lambda *args: "synthetic-test-only")
        output = service._invoke_model(profile, run, "临时网关故障后继续", {})
        assert output.content == "重试后成功"
        assert attempts == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_external_transport_retries_with_corrective_prompt_after_invalid_json(
    monkeypatch,
) -> None:
    service = object.__new__(AgentRuntimeService)
    service.settings = Settings(
        agent_context_max_chars=5000,
        agent_model_max_retries=0,
        agent_model_retry_base_seconds=0,
    )
    profile = SimpleNamespace(
        provider="OPENAI_COMPATIBLE",
        encrypted_api_key="synthetic",
        base_url="https://invalid.test",
        model="synthetic-invalid-json",
        temperature=0,
        timeout_seconds=1,
    )
    run = SimpleNamespace(
        agent_kind="PROJECTION",
        context_manifest={"allow_external_model": True},
    )
    monkeypatch.setattr(LocalSecretVault, "decrypt", lambda *args: "synthetic-test-only")
    requests: list[dict[str, object]] = []

    def post(_url: str, **kwargs: object) -> SimpleNamespace:
        requests.append(kwargs["json"])
        content = "not-json" if len(requests) == 1 else '{"content":"修正后成功"}'
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": content}}]},
        )

    monkeypatch.setattr("enterprise_insight_backend.agent_runtime.httpx.post", post)
    output = service._invoke_model(profile, run, "请生成投影", {})
    assert output.content == "修正后成功"
    assert len(requests) == 2
    assert "不符合JSON契约" in requests[1]["messages"][-1]["content"]
