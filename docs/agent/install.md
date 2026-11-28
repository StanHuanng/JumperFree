# Install JumperFree Host for a local Agent

One program serves every MCP client: `jumperfree-mcp` (stdio). The client starts it
at the start of a conversation and stops it at the end.

## 1. Install

Python 3.11 or newer.

```sh
pip install "git+https://github.com/StanHuanng/JumperFree.git#subdirectory=python"
# or, from a checkout:
pip install ./python
```

Check: `jumperfree-mcp --help`. On Windows with the Microsoft Store Python the
`Scripts` folder may not be on PATH; then use `python -m jumperfree.agent.mcp_server`
as the command below (same arguments).

## 2. Register the server

Default: the Host searches USB for the board. Add `--port COM4` (Windows) or
`--port /dev/ttyUSB0` to fix the port. `--mock` runs without hardware.

**Claude Code**

```sh
claude mcp add jumperfree -s user -- jumperfree-mcp
```

Optional: copy the Skill so Claude follows the workflow in every project:
`python/src/jumperfree/agent/SKILL.md` → `~/.claude/skills/jumperfree/SKILL.md`.

**Cursor** (`~/.cursor/mcp.json`, or `.cursor/mcp.json` in a project)

```json
{ "mcpServers": { "jumperfree": { "command": "jumperfree-mcp", "args": [] } } }
```

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.jumperfree]
command = "jumperfree-mcp"
args = []
```

The workflow rules also travel in the MCP `initialize` result (`instructions`), so
clients without Skills get them too.

## 3. Use

1. Plug the board's USB-C cable into the computer first.
2. Start a conversation and ask the Agent to wire your parts. It calls
   `describe_board`, plans, asks you to confirm, lights the clips, waits for you,
   then executes.
3. Close the conversation: the LEDs go off and every switch opens.

Only one conversation can own the board. A second one gets `PORT_BUSY`. Close any
serial monitor on the board's port before you start.
