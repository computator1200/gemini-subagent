# gemini-subagent

A [Claude Code](https://claude.ai/code) skill that teaches Claude how to delegate work to the **Gemini CLI** running headlessly — as a much cheaper and more context-efficient alternative to spawning a Claude subagent.

## Why

Claude subagents are powerful but they burn the parent's quota *and* dump the full intermediate trace into the parent's context window. The Gemini CLI ships with a robust headless mode, has its own generous free quota, and includes a strong reasoning model (`gemini-3-pro-preview`). Pointed at the right kind of task it produces comparable output, runs against a separate quota, and only the final summary ever touches the parent Claude's context.

This skill encodes the invocation flags, output parsing, prompting style, and pitfalls of three usage patterns:

- **Single-shot delegation**: hand Gemini a self-contained build / research task and read the JSON result.
- **Co-development**: split a full-stack system in two and run yourself + Gemini in genuine parallel against a frozen contract.
- **Real-time steering via ACP mode**: spawn `gemini --acp`, watch its thoughts and tool calls live, cancel and redirect mid-flight.

## Install

Skills live under `~/.claude/skills/` (on Windows: `%USERPROFILE%\.claude\skills\`). Drop this repo's folder there:

```sh
# macOS / Linux
git clone https://github.com/<your-username>/gemini-subagent ~/.claude/skills/gemini-subagent

# Windows (PowerShell)
git clone https://github.com/<your-username>/gemini-subagent $env:USERPROFILE\.claude\skills\gemini-subagent
```

Restart your Claude Code session (or `/reload`). Claude will pick it up automatically and consult it whenever the user mentions Gemini, asks to delegate, asks to save tokens / quota, or is about to spawn a Claude subagent for a heavy self-contained task.

## Requirements

- [Gemini CLI](https://github.com/google-gemini/gemini-cli) v0.42+ installed and authenticated (`gemini` on PATH; on Windows the launcher is `gemini.cmd`).
- Claude Code with the `Bash` tool — and, for the real-time steering pattern, the `desktop-commander` MCP for spawning a long-lived interactive process.
- Python 3 (only if you use the bundled ACP wrapper at `scripts/acp_client.py`).

If your `gemini` binary lives somewhere weird, set the `GEMINI_BIN` env var before running the ACP wrapper.

## What's inside

- `SKILL.md` — the skill itself. Read this first; everything below is just packaging.
- `scripts/acp_client.py` — a small Python wrapper around `gemini --acp` exposing a line protocol (`INIT` / `PROMPT <text>` / `CANCEL` / `STATUS` / `QUIT`) so Claude can drive an interactive Gemini session via `desktop-commander`.

## Highlights

- **Mandatory flags for headless invocation** (`--skip-trust` and `--yolo`), with explanations for why omitting either causes silent hangs.
- **JSON output parsing**: `.response` (the summary) and `.stats` (token + tool telemetry).
- **Wrapper pattern**: dispatch via Bash with `run_in_background: true`, redirect stdout to a sidecar JSON, wait for the harness completion notification, then `Read` the file — never poll.
- **Contract-first co-development**: write a frozen `SPEC.md` before either side starts coding, use a shared `STATUS.md` for the handshake.
- **ACP steering**: the JSON-RPC 2.0 lifecycle, what's reachable in each direction, and the cancel-is-best-effort failure mode (the agent's "I reverted" claim cannot be trusted; verify by running the code).
- **A ranked pitfall list** — forgotten flags, lost stderr, model-name aliasing, MCP-routed writes not showing in `.stats.files`, partial reverts after cancel.

## License

MIT. Use it, fork it, send back improvements.
