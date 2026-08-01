# MCP servers

This repository contains independently installable local Model Context Protocol servers.

- `design` manages linked design and requirements documents, implementation snapshots, and
  design-scoped memory over stdio.

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

[`config/codex.toml`](config/codex.toml) contains the local `design` server registration. Its
launch command assumes this repository remains at `/root/Desktop/mcp-servers`.

## Adding a server

Create an independently installable package at the repository root, register it as a uv
workspace member, and expose a console script that runs stdio by default. Keep domain logic
separate from the MCP adapter. Add pure unit tests for domain invariants and at least one
component test using `mcp.Client(server)` so discovery, schema generation, structured output,
and error behavior are exercised through the protocol.

See [`AGENTS.md`](AGENTS.md) for maintenance rules.
