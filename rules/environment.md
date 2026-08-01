# MCP servers environment

`PLAN_DIR` is the only application input for the local servers. It is optional, is an
absolute or relative filesystem path, defaults defensively to `/root/Desktop/plan`, and is
read once when each server process starts. Changing it requires a server restart.

This repository has no tracked dotenv profiles: there are no shared safe values beyond the
application default and tests inject isolated paths directly.
