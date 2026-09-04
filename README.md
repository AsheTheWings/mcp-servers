# MCP servers

This repository contains independently installable local Model Context Protocol servers.

- `design` scaffolds standalone design documents or linked design and requirements documents,
  and manages paired implementation snapshots and design-scoped memory over stdio.

## Setup and verification

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required.

```sh
uv sync --all-packages --group dev
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv build --all-packages
```

## Client registration

Install the design server as a uv tool pinned to a reviewed commit, then register the
`mcp-design` command in the harness. The server requires `PLAN_DIR` naming the machine's
Git-backed plan workspace; the harness registration must supply it.

```sh
uv tool install \
  'git+ssh://git@github.com/AsheTheWings/mcp-servers.git@<full-commit>#subdirectory=design'
```

## Adding a server

Create an independently installable package at the repository root, register it as a uv
workspace member, and expose a console script that runs stdio by default. Keep domain logic
separate from the MCP adapter. Add pure unit tests for domain invariants and at least one
component test using `mcp.Client(server)` so discovery, schema generation, structured output,
and error behavior are exercised through the protocol.

See [`AGENTS.md`](AGENTS.md) for maintenance rules.
