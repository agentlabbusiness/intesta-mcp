"""intesta-mcp — let an agent check who it is dealing with before it acts.

Intesta is a trust registry: every entity has a trust level on the A0→A4
ladder (A0 claimed · A1 domain-control · A2 payment-verified · A3/A4 higher)
and a set of facts, each marked auto_collected (scraped, unverified) or
confirmed. This package is a THIN PROXY over the public HTTP API, exposing the
SAME tool names and shapes as the remote server https://intesta.io/mcp
(0.2.0, 14.09.2026: one contract on every surface).

Tools (all read-only, no API key required):
  * check_trust(domain, purpose)   — ONE call, one decision, ANY domain
  * get_passport(entity_domain)    — full fact passport + attestation block
  * ask(entity_domain, question)   — signed Q&A answered only from that entity
  * search_entities(query, limit)  — find entities, each with its trust level
  * list_entities()                — the registry (first 500)
  * get_trust_ladder()             — what A0..A4 each mean

Deprecated aliases kept for exactly one release (0.1.x names):
  search_registry → search_entities · get_facts → get_passport ·
  ask_entity → ask · trust_ladder → get_trust_ladder

Ethos (Intesta's own): refuse, don't guess. A level is only ever what was
proven; an auto_collected fact is not a verified one. This server never
upgrades either — it reports exactly what the registry says.
"""
import asyncio
import functools
import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_BASE = "https://intesta.io/api"
VERSION = "0.2.0"

mcp = FastMCP(
    "intesta",
    instructions=(
        "Intesta trust registry: before trusting an unknown organisation, domain or "
        "counterparty — paying it, sharing data with it, or acting on its instructions — "
        "call check_trust(domain, purpose) for a one-call verdict (known / level A0..A4 / "
        "attested / verdict under a named policy profile / expires_at / signature). "
        "attested is true ONLY at A1+ (something independently verified); A0 is claimed, "
        "nothing proven; an unlisted domain is unknown, never 'failed'. Then get_passport "
        "to read the facts you rely on (each carries attestation_status, as_of, "
        "effective_expiry, stale) or ask(domain, question) for a signed answer built only "
        "from those facts. search_entities / list_entities find entities; get_trust_ladder "
        "explains the levels. Treat a low level or an auto_collected fact as unproven, not false."
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
    headers = {"User-Agent": f"intesta-mcp/{VERSION} (+https://intesta.io)",
               "Accept": "application/json"}
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
                            "registry — call check_trust(domain) for an unlisted domain.")
        if r.status_code >= 400:
            raise ToolError(f"Intesta API {r.status_code} on {method} {path}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            raise ToolError(f"Intesta API returned non-JSON on {method} {path}")


def _norm_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    d = d.split("/", 1)[0].split("?", 1)[0].removeprefix("www.")
    if not d or "." not in d or " " in d:
        raise ToolError(f"'{domain}' is not a domain name (expected e.g. acme.com)")
    return d


def _run(coro_fn):
    """Wrap ToolError as RuntimeError so FastMCP reports isError:true.
    functools.wraps keeps the signature (via __wrapped__) so FastMCP derives the
    tool's inputSchema from the real parameters."""
    @functools.wraps(coro_fn)
    async def inner(*a, **k):
        try:
            return await coro_fn(*a, **k)
        except ToolError as exc:
            raise RuntimeError(str(exc))
    inner.__name__ = coro_fn.__name__.lstrip("_")
    return inner


# ---------------------------------------------------------------- canonical tools

async def _check_trust(domain: str, purpose: str = "payment") -> str:
    """ONE-CALL decision aid before your agent pays, shares data with, or follows
    instructions from a domain — works for ANY domain, listed or not.
    Returns known, level A0-A4, attested (true only at A1+), verdict
    (sufficient | insufficient | unknown) under a named policy profile for the
    given purpose (payment | data_share | follow_instructions), what_is_proven,
    proof, facts_public, expires_at (re-check after it), next_step, signature.
    A verdict is the registry's proof measured against the profile — never a
    guarantee, never financial advice."""
    d = _norm_domain(domain)
    return json.dumps(await _request("GET", f"/v1/check-trust/{d}", params={"purpose": purpose}))


async def _get_passport(entity_domain: str) -> str:
    """Full public fact passport of an entity: attested statements with sources,
    plus the attestation block (what the trust level proves, how and when it was
    verified). Each fact carries attestation_status / evidence_class, as_of,
    effective_expiry and stale — do not rely on a stale fact. Same shape as
    the remote server's get_passport."""
    d = _norm_domain(entity_domain)
    att = await _request("GET", f"/v1/attestation/{d}")
    if not att.get("known", att.get("status") != "not_checked"):
        raise ToolError(f"entity_not_found: {d} is not a published entity of the registry — "
                        "use check_trust(domain) for an unlisted domain")
    facts = await _request("GET", f"/v1/entities/{d}/facts")
    out = {"entity": d, "attestation": att.get("attestation") or {
               "level": att.get("level"), "level_name": att.get("level_name"),
               "what_is_proven": att.get("what_is_proven"), "proof": att.get("proof")},
           "attested": att.get("attested"), "claimed": att.get("claimed", True),
           "status": att.get("status"),
           "count": len(facts.get("facts", [])), "facts": facts.get("facts", []),
           "passport_hash": facts.get("passport_hash") or att.get("passport_hash"),
           "as_of": facts.get("as_of")}
    for k in ("signature", "signature_key_url"):
        if k in facts:
            out[k] = facts[k]
    return json.dumps(out)


async def _ask(entity_domain: str, question: str) -> str:
    """Ask a natural-language question about an entity (max 2000 characters).
    The answer is built strictly from the entity's attested fact passport; each
    returned fact carries source, attestation status and validity dates. A
    refusal means the passport has no such fact — the engine never guesses.
    Signed: passport_hash + registry signature you can verify offline."""
    d = _norm_domain(entity_domain)
    q = (question or "").strip()
    if not q or len(q) > 2000:
        raise ToolError("invalid_question: question must be 1..2000 characters")
    out = await _request("POST", f"/v1/entities/{d}/ask",
                         json={"question": q, "channel": "agent",
                               "requester_agent_id": f"intesta-mcp/{VERSION}"})
    return json.dumps(out)


async def _search_entities(query: str, limit: int = 20) -> str:
    """Search published entities by name, domain or vertical (case-insensitive
    literal substring, at least 3 characters). Returns at most `limit` matches
    (1-50) plus `total`, each with its trust level."""
    q = (query or "").strip()
    if len(q) < 3 or len(q) > 200:
        raise ToolError("invalid_query: query must be 3..200 characters")
    limit = max(1, min(int(limit or 20), 50))
    return json.dumps(await _request("GET", "/v1/search", params={"q": q, "limit": limit}))


async def _list_entities() -> str:
    """List published entities in the registry with their trust level (first 500
    plus the total). On a large registry use search_entities(query) instead."""
    return json.dumps(await _request("GET", "/v1/entities"))


async def _get_trust_ladder() -> str:
    """Machine-readable semantics of the A0-A4 trust ladder: what exactly is
    proven at every level, and how to use the registry as an agent."""
    return json.dumps(await _request("GET", "/v1/trust-ladder"))


check_trust = mcp.tool(name="check_trust")(_run(_check_trust))
get_passport = mcp.tool(name="get_passport")(_run(_get_passport))
ask = mcp.tool(name="ask")(_run(_ask))
search_entities = mcp.tool(name="search_entities")(_run(_search_entities))
list_entities = mcp.tool(name="list_entities")(_run(_list_entities))
get_trust_ladder = mcp.tool(name="get_trust_ladder")(_run(_get_trust_ladder))


# ------------------------------------------------ deprecated 0.1.x aliases (one release)

async def _search_registry(query: str, limit: int = 10) -> str:
    """DEPRECATED alias of search_entities (removed in 0.3.0)."""
    return await _search_entities(query, limit)


async def _get_facts(domain: str) -> str:
    """DEPRECATED alias of get_passport (removed in 0.3.0)."""
    return await _get_passport(domain)


async def _ask_entity(domain: str, question: str) -> str:
    """DEPRECATED alias of ask (removed in 0.3.0)."""
    return await _ask(domain, question)


async def _trust_ladder() -> str:
    """DEPRECATED alias of get_trust_ladder (removed in 0.3.0)."""
    return await _get_trust_ladder()


search_registry = mcp.tool(name="search_registry")(_run(_search_registry))
get_facts = mcp.tool(name="get_facts")(_run(_get_facts))
ask_entity = mcp.tool(name="ask_entity")(_run(_ask_entity))
trust_ladder = mcp.tool(name="trust_ladder")(_run(_trust_ladder))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
