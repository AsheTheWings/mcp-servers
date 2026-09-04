# Repository instructions

Read `README.md` before changing this repository.

- `design/src/` owns the installable local stdio server and all design lifecycle domains.
- Client registration lives in host harness configuration, never in this repository.
  `README.md` owns the registration contract: the installable `mcp-design` command and
  the required `PLAN_DIR` environment variable.
- Pin the MCP SDK exactly in every server package and update the workspace lockfile with it.
- Prefer typed `MCPServer.tool()` handlers and structured return models over hand-written
  protocol dispatch. Add behavioral annotations and keep transport startup in the adapter.
- Do not write diagnostics to stdout because stdout carries the stdio protocol.
- Run the commands in `rules/testing.md` before committing.

The project-specific environment and testing contracts are in `rules/`.
