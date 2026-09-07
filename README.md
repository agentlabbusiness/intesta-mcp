# intesta-mcp

<!-- mcp-name: io.intesta/intesta-mcp -->

An MCP server that lets an AI agent **check who it is dealing with before it acts.**

[Intesta](https://intesta.io) is a trust registry. Every entity has a trust level on the
**A0→A4 ladder** (A0 claimed · A1 domain-control · A2 payment-verified · A3/A4 higher) and a
set of facts, each marked `confirmed` (independently verified) or `auto_collected` (scraped
from public sources, **not** verified). This server exposes the registry's public reads as
MCP tools so an agent can ask *"can I trust this domain, and what is actually proven?"* before
it pays a counterparty, shares data, or follows instructions from an unknown party.

Its ethos is Intesta's own: **refuse, don't guess.** A level is only ever what was proven; an
`auto_collected` fact is not a verified one. The server reports exactly what the registry says
and never upgrades either.

## Tools

| Tool | What it answers |
|---|---|
| `search_registry(query, limit=10)` | Find an entity by name or domain; each result carries its trust level. |
| `check_trust(domain)` | The headline: A0..A4 level, whether it's attested, `what_is_proven`, and the proof method (e.g. `dns_txt`). |
| `get_facts(domain)` | The entity's public facts, each flagged `confirmed` vs `auto_collected`, with its source. |
| `ask_entity(domain, question)` | A question answered **only** from that entity's registry facts, with a `passport_hash` and, when configured, a registry signature you can verify offline. |
| `trust_ladder()` | What each of A0..A4 means. |

All tools are **read-only** and need **no API key** — public registry reads work out of the box.
An optional `INTESTA_API_KEY` (`ik_…`) is only needed for metered or private use.

## Install

```jsonc
// Claude Desktop / any MCP client — mcp config
{
  "mcpServers": {
    "intesta": { "command": "uvx", "args": ["intesta-mcp"] }
  }
}
```

Or run directly:

```bash
uvx intesta-mcp
```

## Example

> Agent is about to send funds to `acme-invoicing.com`.

```
check_trust("acme-invoicing.com")
→ { "level": "A0", "attested": false,
    "what_is_proven": "listed only; nothing independently verified" }
```

A0 and `attested:false` means the domain is unproven — the agent should pause or escalate
(pair this with [Raposa Aval](https://raposa.group) for a human approval step) rather than
assume the counterparty is who it claims to be.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q          # contract tests, no network
```

MIT · a DC ESCRYPT product · https://intesta.io
