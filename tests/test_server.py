"""Contract tests for intesta-mcp 0.2.0.

The Intesta API is faked with an httpx MockTransport; the MCP side is exercised
through the SDK's in-memory client/server session — exactly what a real MCP
client sees. No network, deterministic.

0.2.0: the wrapper exposes the SAME tool names as the remote server
https://intesta.io/mcp (check_trust, get_passport, ask, search_entities,
list_entities, get_trust_ladder); the 0.1.x names stay as deprecated aliases.
"""
import json

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as client_session

from intesta_mcp import server as srv

CANONICAL = {"check_trust", "get_passport", "ask", "search_entities", "list_entities", "get_trust_ladder"}
ALIASES = {"search_registry", "get_facts", "ask_entity", "trust_ladder"}


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
        if p.endswith("/v1/entities"):
            return httpx.Response(200, json={"total": 1, "count": 1, "note": None,
                "entities": [{"domain": "acme.com", "name": "Acme", "vertical": "payments",
                              "attestation_level": "A1"}]})
        if p.endswith("/v1/trust-ladder"):
            return httpx.Response(200, json={"trust_ladder": {
                "A0": {"name": "Claimed", "what_is_proven": "listed only"},
                "A1": {"name": "Domain control", "what_is_proven": "owner proved the domain"}}})
        if p.endswith("/v1/check-trust/acme.com"):
            return httpx.Response(200, json={"domain": "acme.com", "purpose": request.url.params.get("purpose", "payment"),
                "policy_profile": "payment/v1", "min_level_required": "A2", "known": True,
                "level": "A1", "level_name": "Domain control", "attested": True, "claimed": True,
                "status": "attested", "verdict": "insufficient", "what_is_proven": "owner proved the domain",
                "proof": {"method": "dns_txt", "verified_at": "2026-09-07T18:11:07Z"},
                "facts_public": 2, "expires_at": "2026-10-01T00:00:00+00:00",
                "next_step": "Level A1 is below A2 required by payment/v1", "signature": {"alg": "Ed25519"}})
        if p.endswith("/v1/check-trust/ghost.com"):
            return httpx.Response(200, json={"domain": "ghost.com", "purpose": "payment",
                "policy_profile": "payment/v1", "known": False, "level": None, "attested": False,
                "claimed": False, "status": "not_checked", "verdict": "unknown",
                "next_step": "Not in the Intesta registry", "signature": {"alg": "Ed25519"}})
        if p.endswith("/v1/attestation/acme.com"):
            return httpx.Response(200, json={"domain": "acme.com", "name": "Acme", "level": "A1",
                "level_name": "Domain control", "facts_public": 2, "attested": True, "claimed": True,
                "status": "attested", "what_is_proven": "owner proved the domain",
                "profile_url": "https://intesta.io/e/acme.com",
                "proof": {"method": "dns_txt", "verified_at": "2026-09-07T18:11:07Z"},
                "attestation": {"level": "A1", "level_name": "Domain control",
                                "what_is_proven": "owner proved the domain",
                                "proof": {"method": "dns_txt", "verified_at": "2026-09-07T18:11:07Z"}}})
        if p.endswith("/v1/attestation/ghost.com"):
            return httpx.Response(200, json={"domain": "ghost.com", "attested": False, "status": "not_checked"})
        if p.endswith("/v1/entities/acme.com/facts"):
            return httpx.Response(200, json={"entity": "acme.com", "passport_hash": "sha256:abc", "as_of": "2026-09-01T00:00:00Z",
                "signature": {"alg": "Ed25519", "sig": "x"}, "facts": [
                {"statement": "Acme sells payment APIs.", "category": "product",
                 "attestation_status": "auto_collected", "evidence_class": "auto_collected", "source_type": "site_scrape",
                 "source_ref": "https://acme.com/", "disclosure": "public", "as_of": "2026-09-01T00:00:00Z",
                 "valid_from": "2026-09-01T00:00:00Z", "expires_at": None,
                 "effective_expiry": "2026-10-01T00:00:00Z", "stale": False},
                {"statement": "Acme is incorporated in Germany.", "category": "company",
                 "attestation_status": "confirmed", "evidence_class": "confirmed", "source_type": "registry",
                 "source_ref": "https://handelsregister.de/", "disclosure": "public", "as_of": "2026-09-01T00:00:00Z",
                 "valid_from": "2026-09-01T00:00:00Z", "expires_at": None,
                 "effective_expiry": "2027-09-01T00:00:00Z", "stale": False}]})
        if p.endswith("/v1/entities/acme.com/ask"):
            body = json.loads(request.content)
            self.bodies.append(body)
            assert body["channel"] == "agent"
            return httpx.Response(200, json={"refused": False, "answer": "Acme sells payment APIs.",
                "entity": "acme.com", "entity_id": "e-1", "facts": [{"statement": "Acme sells payment APIs."}],
                "passport_hash": "abc123", "question_sha256": "deadbeef",
                "signature": "sig"})
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


async def test_tools_listed_canonical_plus_aliases(api):
    async with client_session(srv.mcp._mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert CANONICAL <= set(tools), "wrapper must expose the remote server's tool names"
    assert ALIASES <= set(tools), "0.1.x names stay one release as aliases"
    assert "domain" in tools["check_trust"].inputSchema["properties"]
    assert "purpose" in tools["check_trust"].inputSchema["properties"]
    assert "entity_domain" in tools["get_passport"].inputSchema["properties"]
    for a in ALIASES:
        assert "DEPRECATED" in (tools[a].description or "")


async def test_check_trust_is_one_call_with_verdict(api):
    out = _payload(await _call("check_trust", {"domain": "acme.com"}))
    assert out["known"] is True and out["level"] == "A1" and out["attested"] is True
    assert out["verdict"] == "insufficient" and out["policy_profile"] == "payment/v1"
    assert out["expires_at"] and "signature" in out
    assert api.calls == [("GET", "/api/v1/check-trust/acme.com")]


async def test_check_trust_purpose_is_forwarded(api):
    out = _payload(await _call("check_trust", {"domain": "acme.com", "purpose": "data_share"}))
    assert out["purpose"] == "data_share"


async def test_check_trust_unknown_domain_is_unknown_not_failed(api):
    out = _payload(await _call("check_trust", {"domain": "ghost.com"}))
    assert out["known"] is False and out["verdict"] == "unknown" and out["status"] == "not_checked"
    assert out["attested"] is False


async def test_check_trust_normalises_url_and_www(api):
    out = _payload(await _call("check_trust", {"domain": "https://www.acme.com/path?x=1"}))
    assert out["domain"] == "acme.com"
    assert api.calls[-1] == ("GET", "/api/v1/check-trust/acme.com")


async def test_get_passport_matches_remote_shape_with_freshness(api):
    out = _payload(await _call("get_passport", {"entity_domain": "acme.com"}))
    assert out["entity"] == "acme.com" and out["count"] == 2
    assert out["attestation"]["level"] == "A1" and out["attested"] is True
    f = out["facts"][0]
    assert f["evidence_class"] == "auto_collected" and f["stale"] is False and f["effective_expiry"]
    assert out["passport_hash"] and out["signature"]
    assert [c[1] for c in api.calls] == ["/api/v1/attestation/acme.com", "/api/v1/entities/acme.com/facts"]


async def test_get_passport_unknown_domain_is_error(api):
    r = await _call("get_passport", {"entity_domain": "ghost.com"})
    assert r.isError and "entity_not_found" in _payload(r).get("_text", "")


async def test_ask_sends_agent_channel_and_returns_signed_answer(api):
    out = _payload(await _call("ask", {"entity_domain": "acme.com", "question": "what does acme do?"}))
    assert out["refused"] is False and out["answer"] and out["passport_hash"] == "abc123"
    assert api.bodies[0]["channel"] == "agent"
    assert api.bodies[0]["requester_agent_id"].startswith("intesta-mcp/")


async def test_ask_rejects_empty_question_without_network(api):
    r = await _call("ask", {"entity_domain": "acme.com", "question": "   "})
    assert r.isError and api.calls == []


async def test_search_entities_and_short_query(api):
    out = _payload(await _call("search_entities", {"query": "acme"}))
    assert out["entities"][0]["level"] == "A1"
    r = await _call("search_entities", {"query": "ab"})
    assert r.isError and api.calls == [("GET", "/api/v1/search")]


async def test_list_entities_and_trust_ladder(api):
    out = _payload(await _call("list_entities", {}))
    assert out["total"] == 1 and out["entities"][0]["attestation_level"] == "A1"
    out = _payload(await _call("get_trust_ladder", {}))
    assert "A1" in out["trust_ladder"]


async def test_aliases_route_to_canonical(api):
    a = _payload(await _call("search_registry", {"query": "acme"}))
    b = _payload(await _call("search_entities", {"query": "acme"}))
    assert a == b
    f = _payload(await _call("get_facts", {"domain": "acme.com"}))
    assert f["count"] == 2 and f["attested"] is True
    l = _payload(await _call("trust_ladder", {}))
    assert "A0" in l["trust_ladder"]


async def test_bad_domain_never_hits_api(api):
    r = await _call("check_trust", {"domain": "not-a-domain"})
    assert r.isError and api.calls == []


async def test_api_error_is_reported_not_swallowed(monkeypatch):
    fake = FakeApi(status_code=503)
    srv.configure(transport=httpx.MockTransport(fake.handler))
    r = await _call("search_entities", {"query": "acme"})
    assert r.isError and "503" in _payload(r).get("_text", "")


async def test_optional_key_sent_when_present_never_returned(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"trust_ladder": {"A0": {"name": "Claimed"}}})
    monkeypatch.setenv("INTESTA_API_KEY", "ik_secret")
    srv.configure(transport=httpx.MockTransport(handler))
    r = await _call("get_trust_ladder", {})
    assert seen["auth"] == "Bearer ik_secret"
    assert "ik_secret" not in "".join(c.text for c in r.content)
