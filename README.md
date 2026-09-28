# MCP servers

This repository contains independently installable local Model Context Protocol servers.

- [`browserctl-dev`](browserctl-dev/README.md) keeps one stable development MCP connection
  while replacing the Browserctl child gateway after a new development release is activated.

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

[Testing](docs/testing.md) describes the test classes and gates.

## Client registration

Install each server as a uv tool pinned to a reviewed commit, then register its command in
the harness. Register `mcp-browserctl-dev` only in a development harness. It exposes a stable
`list_tools`, `call_tool`, and `restart_server` surface while the production harness continues
to register Browserctl directly.

```sh
uv tool install \
  'git+ssh://git@github.com/AsheTheWings/mcp-servers.git@<full-commit>#subdirectory=browserctl-dev'
```

## Adding a server

Create an independently installable package at the repository root, register it as a uv
workspace member, and expose a console script that runs stdio by default. Keep domain logic
separate from the MCP adapter. Add pure unit tests for domain invariants and at least one
component test using `mcp.Client(server)` so discovery, schema generation, structured output,
and error behavior are exercised through the protocol.
