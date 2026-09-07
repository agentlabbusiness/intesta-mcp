"""intesta-mcp — let an agent check who it is dealing with before it acts.

Intesta is a trust registry: every entity has a trust level on the A0→A4
ladder (A0 claimed · A1 domain-control · A2 payment-verified · A3/A4 higher)
and a set of facts, each marked auto_collected (scraped, unverified) or
confirmed. This server exposes the registry's PUBLIC reads as MCP tools so an
agent can ask "can I trust this domain, and what is actually proven?" before it
pays, shares data, or follows instructions from an unknown party.

Tools (all read-only, no API key required):
  * search_registry(query)      — find entities, each with its trust level
  * check_trust(domain)         — the headline: level + what is actually proven
  * get_facts(domain)           — verbatim facts, each flagged verified or not
  * ask_entity(domain, q)       — signed Q&A answered only from that entity
  * trust_ladder()              — what A0..A4 each mean

Ethos (Intesta's own): refuse, don't guess. A level is only ever what was
proven; an auto_collected fact is not a verified one. This server never
upgrades either — it reports exactly what the registry says.
"""
import asyncio
import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_BASE = "https://intesta.io/api"

mcp = FastMCP(
    "intesta",
    instructions=(
        "Intesta trust registry: before trusting an unknown organisation, domain or "
        "counterparty — paying it, sharing data with it, or acting on its instructions — "
        "call check_trust(domain) to see its A0..A4 level and what is actually proven, and "
        "get_facts(domain) to read its facts (each flagged verified or merely auto_collected). "
        "Treat a low level or an unverified fact as unproven, not as false. Use search_registry "
        "to find an entity and trust_ladder to interpret the levels."
    ),
)

_transport = None   # tests inject an httpx.MockTransport


def configure(transport=None):
    global _transport
    _transport = transport


class ToolError(Exception):
    pass


def _client() -> httpx.AsyncClient:
    base = os.environ.get("INTESTA_API_BASE", DEFAULT_BASE).rstrip("/")
    headers = {"User-Agent": "intesta-mcp/0.1 (+https://intesta.io)",
               "Accept": "application/json"}
    # An optional key is only needed for metered/private use; public reads work
    # without one. When present it is sent, never echoed into tool output.
    key = os.environ.get("INTESTA_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return httpx.AsyncClient(base_url=base, transport=_transport, timeout=20.0,
                             headers=headers)


async def _request(method: str, path: str, **kw) -> dict:
    async with _client() as c:
        r = await c.request(method, path, **kw)
        if r.status_code == 429:
            await asyncio.sleep(min(float(r.headers.get("Retry-After", "1") or 1), 10))
            r = await c.request(method, path, **kw)
        if r.status_code == 404:
            raise ToolError(f"Not found: {method} {path}. The domain may not be in the "
                            "registry — that is itself a signal: it is unproven here.")
        if r.status_code >= 400:
            raise ToolError(f"Intesta API {r.status_code} on {method} {path}: {r.text[:300]}")
        return r.json()


def _norm_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    for p in ("https://", "http://"):
        if d.startswith(p):
            d = d[len(p):]
    d = d.split("/")[0].strip()
    if d.startswith("www."):
        d = d[4:]
    if not d or "." not in d:
        raise ToolError(f"'{domain}' is not a domain (expected e.g. example.com)")
    return d


@mcp.tool()
async def search_registry(query: str, limit: int = 10) -> str:
    """Search the Intesta trust registry for entities by name or domain.
    Returns each match with its domain, trust level (A0..A4) and public fact count.
    Use this to find the domain to pass to check_trust / get_facts."""
    try:
        q = (query or "").strip()
        if not q:
            raise ToolError("query is required")
        limit = max(1, min(int(limit or 10), 50))
        out = await _request("GET", "/v1/search", params={"q": q, "limit": limit})
        ents = out.get("entities", [])
        return json.dumps({
            "query": out.get("query", q),
            "total": out.get("total", len(ents)),
            "entities": [{"domain": e.get("domain"), "name": e.get("name"),
                          "vertical": e.get("vertical"), "level": e.get("level"),
                          "level_name": e.get("level_name"),
                          "facts_public": e.get("facts_public"),
                          "profile_url": e.get("profile_url")} for e in ents],
        })
    except ToolError as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
async def check_trust(domain: str) -> str:
    """Check what is actually proven about a domain before you trust it.
    Returns its trust level (A0..A4), whether it is attested, the human-readable
    'what_is_proven', the proof method, and how many public facts it has. A low
    level or attested=false means unproven — not necessarily false, but not verified."""
    try:
        d = _norm_domain(domain)
        a = await _request("GET", f"/v1/attestation/{d}")
        proof = a.get("proof") or {}
        return json.dumps({
            "domain": a.get("domain", d),
            "name": a.get("name"),
            "level": a.get("level"),
            "level_name": a.get("level_name"),
            "attested": a.get("attested"),
            "status": a.get("status"),
            "what_is_proven": a.get("what_is_proven"),
            "proof_method": proof.get("method"),
            "verified_at": proof.get("verified_at"),
            "facts_public": a.get("facts_public"),
            "profile_url": a.get("profile_url"),
        })
    except ToolError as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
async def get_facts(domain: str) -> str:
    """Read a domain's public facts from the registry. Each fact carries an
    attestation_status: 'confirmed' (independently verified) or 'auto_collected'
    (scraped from public sources, NOT verified). Do not treat auto_collected as
    proven. Also returns each fact's source so you can check it yourself."""
    try:
        d = _norm_domain(domain)
        out = await _request("GET", f"/v1/entities/{d}/facts")
        facts = out.get("facts", [])
        return json.dumps({
            "entity": out.get("entity", d),
            "count": len(facts),
            "facts": [{"statement": f.get("statement"),
                       "category": f.get("category"),
                       "attestation_status": f.get("attestation_status"),
                       "source_type": f.get("source_type"),
                       "source_ref": f.get("source_ref"),
                       "disclosure": f.get("disclosure"),
                       "valid_from": f.get("valid_from"),
                       "expires_at": f.get("expires_at")} for f in facts],
        })
    except ToolError as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
async def ask_entity(domain: str, question: str) -> str:
    """Ask a question answered ONLY from a specific entity's registry facts.
    The answer is grounded in that entity's passport (no guessing) and carries a
    passport_hash and, when configured, a registry signature you can verify
    offline. Use for 'what does X do / what is X's refund policy' about a known domain."""
    try:
        d = _norm_domain(domain)
        q = (question or "").strip()
        if not q:
            raise ToolError("question is required")
        out = await _request("POST", f"/v1/entities/{d}/ask",
                             json={"question": q[:2000], "channel": "agent",
                                   "requester_agent_id": "intesta-mcp"})
        # Pass through the grounded answer plus the proof fields, trimmed.
        keep = {k: out.get(k) for k in ("answer", "entity", "entity_id",
                                        "passport_hash", "question_sha256",
                                        "signature", "signature_key_url", "sources")
                if k in out}
        keep.setdefault("entity", d)
        return json.dumps(keep)
    except ToolError as exc:
        raise RuntimeError(str(exc))


@mcp.tool()
async def trust_ladder() -> str:
    """Explain the A0..A4 trust ladder: what each level means and what it proves.
    Call this to interpret the 'level' returned by check_trust and search_registry."""
    try:
        out = await _request("GET", "/v1/trust-ladder")
        return json.dumps(out.get("trust_ladder", out))
    except ToolError as exc:
        raise RuntimeError(str(exc))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
