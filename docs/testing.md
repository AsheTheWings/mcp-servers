# Testing

The root `pyproject.toml` is the executable test authority.

- Developer gate: `uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run pytest`
- Release gate: the developer gate plus `uv build --all-packages`
- Unit tests live in each package's `tests/unit/` directory.
- Component tests live in `tests/component/` and use the SDK's in-memory MCP client.

Tests do not load dotenv files or inherit application configuration. Each filesystem test
owns a temporary directory and cleans up through pytest fixtures. Component tests use
in-memory transports and do not spawn processes or open sockets.
