# Browserctl development wrapper

`mcp-browserctl-dev` exposes three stable MCP tools:

- `list_tools` returns the active Browserctl child's catalog, generation, release identity,
  and server identity;
- `call_tool` calls a child tool and forwards its complete MCP tool result; and
- `restart_server` replaces the child with the active development release and restores
  tracked Browserctl session control state.

The wrapper keeps the active child when replacement startup or session restoration fails.
Calls already routed to the active child finish before a replacement becomes active.

## Configuration

The wrapper reads these optional process-start inputs:

- `BROWSERCTL_DEV_MCP_COMMAND`, defaulting to `/usr/local/bin/browserctl-dev-mcp`;
- `BROWSERCTL_DEV_MCP_ARGS_JSON`, a JSON array of child arguments, defaulting to `[]`;
- `BROWSERCTL_DEV_RELEASE_SELECTOR`, defaulting to `/opt/browserctl-dev/current`;
- `BROWSERCTL_DEV_MCP_STARTUP_TIMEOUT_SECONDS`, defaulting to `30`;
- `BROWSERCTL_DEV_MCP_CALL_TIMEOUT_SECONDS`, defaulting to `120`; and
- `BROWSERCTL_DEV_MCP_RESTORE_TIMEOUT_SECONDS`, defaulting to `120`.

The wrapper reads these inputs once when it starts. It resolves the release selector for
every child start.

Register the installed `mcp-browserctl-dev` command in a development MCP harness. Production
harnesses register Browserctl directly.
