"""Contract tests for intesta-mcp.

The Intesta API is faked with an httpx MockTransport; the MCP side is exercised
through the SDK's in-memory client/server session — exactly what a real MCP
client sees. No network, deterministic.
"""
import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as client_session

from intesta_mcp import server as srv


class FakeApi:
    """Minimal public Intesta registry for one entity: acme.com at A1."""

    def __init__(self, status_code=200):
        self.status_code = status_code
        self.calls, self.bodies = [], []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"detail": f"faked {self.status_code}"})
        p = request.url.path
        if p.endswith("/v1/search"):
            return httpx.Response(200, json={"query": "acme", "total": 1, "count": 1, "offset": 0,
                "entities": [{"domain": "acme.com", "name": "Acme", "vertical": "payments",
                              "level": "A1", "level_name": "Domain control", "facts_public": 2,
                              "profile_url": "https://intesta.io/e/acme.com"}]})
        if p.endswith("/v1/trust-ladder"):
            return httpx.Response(200, json={"trust_ladder": {
                "A0": {"name": "Claimed", "what_is_proven": "listed only"},
                "A1": {"name": "Domain control", "what_is_proven": "owner proved the domain"}}})
        if p.endswith("/v1/attestation/acme.com"):
            return httpx.Response(200, json={"domain": "acme.com", "name": "Acme", "level": "A1",
                "level_name": "Domain control", "facts_public": 2, "attested": True,
                "status": "attested", "what_is_proven": "owner proved the domain",
                "profile_url": "https://intesta.io/e/acme.com",
                "proof": {"method": "dns_txt", "verified_at": "2026-09-07T18:11:07Z"}})
        if p.endswith("/v1/entities/acme.com/facts"):
            return httpx.Response(200, json={"entity": "acme.com", "facts": [
                {"statement": "Acme sells payment APIs.", "category": "product",
                 "attestation_status": "auto_collected", "source_type": "site_scrape",
                 "source_ref": "https://acme.com/", "disclosure": "public",
                 "valid_from": "2026-09-01T00:00:00Z", "expires_at": None},
                {"statement": "Acme is incorporated in Germany.", "category": "company",
                 "attestation_status": "confirmed", "source_type": "registry",
                 "source_ref": "https://handelsregister.de/", "disclosure": "public",
                 "valid_from": "2026-09-01T00:00:00Z", "expires_at": None}]})
        if p.endswith("/v1/entities/acme.com/ask"):
            body = json.loads(request.content)
            self.bodies.append(body)
            assert body["channel"] == "agent"
            return httpx.Response(200, json={"answer": "Acme sells payment APIs.",
                "entity": "acme.com", "entity_id": "e-1",
                "passport_hash": "abc123", "question_sha256": "deadbeef",
                "signature": "sig", "sources": ["fact-1"]})
        return httpx.Response(404, json={"detail": "not_found"})


@pytest.fixture
def api():
    fake = FakeApi()
    srv.configure(transport=httpx.MockTransport(fake.handler))
    return fake


async def _call(name, args):
    async with client_session(srv.mcp._mcp_server) as client:
        return await client.call_tool(name, args)


def _payload(result):
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    try:
        return json.loads(text)
    except ValueError:
        return {"_text": text}


async def test_tools_listed_with_schemas(api):
    async with client_session(srv.mcp._mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) >= {"search_registry", "check_trust", "get_facts",
                          "ask_entity", "trust_ladder"}
    assert "domain" in tools["check_trust"].inputSchema["properties"]


async def test_search_returns_entities_with_level(api):
    out = _payload(await _call("search_registry", {"query": "acme"}))
    assert out["total"] == 1
    e = out["entities"][0]
    assert e["domain"] == "acme.com" and e["level"] == "A1"


async def test_check_trust_reports_what_is_proven(api):
    out = _payload(await _call("check_trust", {"domain": "acme.com"}))
    assert out["level"] == "A1" and out["attested"] is True
    assert out["proof_method"] == "dns_txt"
    assert "proved the domain" in out["what_is_proven"]


async def test_check_trust_normalises_url_and_www(api):
    out = _payload(await _call("check_trust", {"domain": "https://www.acme.com/path"}))
    assert out["domain"] == "acme.com"
    assert any(m == "GET" and p.endswith("/v1/attestation/acme.com") for m, p in api.calls)


async def test_get_facts_flags_unverified(api):
    out = _payload(await _call("get_facts", {"domain": "acme.com"}))
    assert out["count"] == 2
    statuses = {f["attestation_status"] for f in out["facts"]}
    assert statuses == {"auto_collected", "confirmed"}
    # source is preserved so the caller can verify for itself
    assert all(f["source_ref"] for f in out["facts"])


async def test_ask_sends_agent_channel_and_returns_proof(api):
    out = _payload(await _call("ask_entity", {"domain": "acme.com", "question": "what does acme do?"}))
    assert out["answer"] == "Acme sells payment APIs."
    assert out["passport_hash"] == "abc123"
    assert api.bodies[-1]["channel"] == "agent"


async def test_trust_ladder(api):
    out = _payload(await _call("trust_ladder", {}))
    assert out["A1"]["name"] == "Domain control"


async def test_bad_domain_never_hits_api(api):
    r = await _call("check_trust", {"domain": "not-a-domain"})
    assert r.isError and api.calls == []


async def test_unknown_domain_404_is_clear(api):
    r = await _call("check_trust", {"domain": "ghost.com"})
    assert r.isError
    assert "unproven" in _payload(r).get("_text", "").lower()


async def test_api_error_is_reported_not_swallowed(monkeypatch):
    fake = FakeApi(status_code=500)
    srv.configure(transport=httpx.MockTransport(fake.handler))
    r = await _call("search_registry", {"query": "acme"})
    assert r.isError and "500" in _payload(r).get("_text", "")


async def test_optional_key_sent_when_present_never_returned(monkeypatch):
    seen = {}
    def h(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"trust_ladder": {"A0": {"name": "Claimed"}}})
    monkeypatch.setenv("INTESTA_API_KEY", "ik_secret")
    srv.configure(transport=httpx.MockTransport(h))
    r = await _call("trust_ladder", {})
    assert seen["auth"] == "Bearer ik_secret"
    assert "ik_secret" not in json.dumps(_payload(r))
