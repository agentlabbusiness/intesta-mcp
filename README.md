# intesta-mcp

<!-- mcp-name: io.intesta/intesta-mcp -->

An MCP server that lets an AI agent **check who it is dealing with before it acts.**

[Intesta](https://intesta.io) is a trust registry. Every entity has a trust level on the
**A0→A4 ladder** (A0 claimed · A1 domain-control · A2 payment-verified · A3/A4 higher) and a
set of facts, each marked `confirmed` (independently verified) or `auto_collected` (scraped
from public sources, **not** verified). This package is a thin proxy over the public HTTP API
and exposes the **same tool names and shapes as the remote server `https://intesta.io/mcp`** —
one contract on every surface (since 0.2.0).

Its ethos is Intesta's own: **refuse, don't guess.** A level is only ever what was proven; an
`auto_collected` fact is not a verified one. The server reports exactly what the registry says
and never upgrades either.

## Tools

| Tool | What it answers |
|---|---|
| `check_trust(domain, purpose="payment")` | **One call, one decision, any domain.** `known`, `level` A0..A4, `attested` (true only at A1+), `verdict` (`sufficient` / `insufficient` / `unknown`) under a named policy profile (`payment/v1` needs A2, `data_share/v1` and `follow_instructions/v1` need A1), `what_is_proven`, `proof`, `expires_at`, `next_step`, signature. Unlisted domain → `unknown`, never "failed". |
| `get_passport(entity_domain)` | The full fact passport: facts with `attestation_status` / `evidence_class`, `as_of`, `effective_expiry`, `stale`, plus the attestation block, `passport_hash` and signature. |
| `ask(entity_domain, question)` | A question answered **only** from that entity's facts, signed (`passport_hash` + registry signature). A refusal means the passport has no such fact. |
| `search_entities(query, limit=20)` | Find entities by name / domain / vertical (literal substring, ≥3 chars); each result carries its level. |
| `list_entities()` | The registry (first 500 + total). |
| `get_trust_ladder()` | What each of A0..A4 means and how to use the registry as an agent. |

Deprecated 0.1.x aliases (`search_registry`, `get_facts`, `ask_entity`, `trust_ladder`) still
work for one release and are removed in 0.3.0.

All tools are **read-only** and need **no API key** — public registry reads work out of the box.
An optional `INTESTA_API_KEY` (`ik_…`) is only needed for metered or private use.

Errors (unknown entity, invalid question or query, API failure) are returned as MCP **error
results** (`isError: true`), never as data-shaped successes.

## Install

```jsonc
// Claude Desktop / any MCP client — mcp config
{
  "mcpServers": {
    "intesta": { "command": "uvx", "args": ["intesta-mcp"] }
  }
}
```

Or connect to the remote server directly (same tools, no install):
`https://intesta.io/mcp` (streamable-http).

## Example

```
check_trust("acme-invoicing.com")
→ {"known": false, "level": null, "attested": false, "status": "not_checked",
   "verdict": "unknown", "policy_profile": "payment/v1",
   "next_step": "Not in the Intesta registry: nothing is proven either way. Treat as unverified. ...",
   "signature": {...}}

check_trust("darkpanther.eu", purpose="data_share")
→ {"known": true, "level": "A1", "attested": true, "verdict": "sufficient",
   "what_is_proven": "...", "proof": {"method": "well_known", "verified_at": "..."},
   "expires_at": "2026-10-03T...", "signature": {...}}
```

A verdict is the registry's proof measured against a published profile — **never a guarantee,
never financial advice.** Verify the signature against
`https://intesta.io/.well-known/intesta-key` (see https://intesta.io/llms.txt).

## Development

```
pip install -e ".[test]"
pytest
```

License: MIT. Publisher: DC ESCRYPT SL — https://intesta.io
