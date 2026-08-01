# MCP servers testing

The root `pyproject.toml` is the executable test authority.

- Developer gate: `uv run ruff format --check . && uv run ruff check . && uv run ty check && uv run pytest`
- Release gate: developer gate plus `uv build --all-packages`
- Unit tests are colocated in each package's `tests/unit/` directory.
- Component tests are colocated in `tests/component/` and use the SDK's in-memory MCP client.
- Conformance tests are in `tests/conformance/` and read only repository metadata.

Tests do not load dotenv files or inherit application configuration. Each filesystem test
owns a temporary directory, patches the package's settings object, and cleans up through
pytest fixtures. Component tests use in-memory transports and do not spawn processes or
open sockets.
