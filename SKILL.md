---
name: gemini-subagent
description: Delegate substantial self-contained work — building apps or modules, generating long reports, doing isolated research, splitting full-stack work across two parallel implementers, or running interactive sessions where you steer the agent in real time — to the Gemini CLI running headlessly, as a much cheaper and more context-efficient alternative to spawning a Claude subagent. Use this skill whenever the user mentions "gemini", "gemini cli", "delegate to gemini", "use gemini as a subagent", "headless gemini", "steer gemini", "real-time gemini", asks to save Claude usage / save tokens / conserve quota, asks for two parallel agents to co-develop separate halves of a system (e.g., backend and frontend, or producer and consumer), or wants to watch a Gemini run live and redirect it mid-flight. Also consider it proactively before spawning a Claude subagent for a heavy self-contained build or research task: the user's Claude quota burns fast, and Gemini CLI (with its own generous free quota and a strong model like gemini-3-pro-preview) can produce comparable output while keeping the parent Claude's context window clean — Claude only ingests Gemini's final summary and stats, not the entire intermediate trace.
---

# gemini-subagent

The Gemini CLI ships with a headless mode that turns it into an excellent drop-in subagent. The key insight: when *you* (Claude) spawn one of your own subagents for a heavy task, the parent quota is what pays — and the harness still consumes context relaying the result. When you delegate the same task to `gemini -p ...` instead, the work runs against Gemini's quota, and the parent only sees a short JSON summary at the end. For self-contained "build me this module / write this report / investigate this repo" jobs, the cost difference is large and the quality of `gemini-3-pro-preview` is competitive.

This skill captures the invocation pattern, output parsing, prompting style, and pitfalls for using Gemini as a delegated worker, a co-developer, or an interactive co-pilot.

## When to use vs. not use

**Reach for Gemini when:**
- The task is self-contained and describable in one shot (build a module in folder X with these features, summarize what's in this folder, draft a long-form doc).
- The work would otherwise dump a large transcript into your context (lots of file writes, lots of internal reasoning, big code generation).
- The user explicitly mentions Gemini or asks to conserve Claude usage.
- You're about to spawn a Claude subagent for something a Gemini call could plausibly handle just as well.

**Don't reach for Gemini when:**
- The work needs your judgment between steps — Gemini runs to completion on a single prompt; you can't intercept it mid-run.
- The task depends on conversation context that isn't easy to transmit in one prompt (Gemini starts cold every invocation).
- The work would take less than ~30 seconds of equivalent inline effort. Cold start (~10s) plus ~15k tokens of system overhead per invocation makes tiny tasks net-worse.
- The user has already corrected your previous Gemini delegation — don't loop on a failing approach.

## Invocation

The canonical headless invocation:

```bash
gemini --skip-trust --yolo -o json -m gemini-3-pro-preview -p "<prompt>" > <result.json> 2>/dev/null
```

On Windows PowerShell, redirect stderr with `2>$null`. On cmd, `2>nul`. The redirect matters — `stderr` is loud with harmless warnings (true-color support, extension config not found, MCP tool name truncations, the "YOLO mode is enabled" notice), and silencing it keeps your run logs readable.

### Flags that aren't optional in practice

| Flag | Why it's effectively required |
|---|---|
| `--skip-trust` | Gemini's first-time-in-a-folder workspace trust prompt blocks headless execution. Without this, the call hangs until the harness times out. |
| `--yolo` | Auto-approves all of Gemini's internal tool calls (`write_file`, `run_shell_command`, etc.). Without this, Gemini reaches its first tool call and waits for approval forever. `--approval-mode auto_edit` is a softer alternative if you want to allow edits but not shell. |
| `-o json` | Emits a parseable JSON envelope on stdout. The default text mode is human-formatted prose mixed with status lines — unparseable from a wrapper. |
| `-p "<prompt>"` | Selects non-interactive mode. Without `-p`, Gemini drops into its TUI and the harness sees nothing. |

`--skip-trust` and `--yolo` together give Gemini broad authority in the current directory. That's the right tradeoff for headless delegation, but be deliberate about the working directory you launch it from — Gemini can write/overwrite anywhere it can reach.

### Model selection

| Model id (pass to `-m`) | Use for | Notes |
|---|---|---|
| `gemini-3-pro-preview` | Heavy builds, careful code, long reports | Aliases to `gemini-3.1-pro-preview` in the stats payload — don't be confused by the version drift. |
| `gemini-3-flash-preview` | Default / cheaper / faster | Used as `main` model when `-m` is omitted. Good for routine summaries and lighter generation. |
| `gemini-3.1-flash-lite` | (Internal) utility router | Gemini uses this for its own internal routing; not normally selected directly. |

Start with `gemini-3-pro-preview` for any task you'd otherwise have asked a Claude subagent to do. Drop to flash only for clearly lightweight work.

## The wrapper pattern (do this every time)

Gemini runs take anywhere from 30 seconds to several minutes — orders of magnitude longer than a typical inline tool call. Block the main loop on it and you waste context on idle time.

The pattern that works:

1. **Launch in the background.** Use Bash with `run_in_background: true`, redirect stdout to a sidecar JSON file, redirect stderr to `/dev/null` (or a debug log).
2. **Wait for the harness completion notification.** Don't poll. The notification fires when Gemini exits.
3. **`Read` the sidecar JSON.** Extract `.response` (the subagent's summary) and optionally `.stats` (telemetry).
4. **Delete the sidecar** after you're done with it, if it's inside the user's project tree.

A concrete invocation, suitable for cargo-culting:

```bash
gemini --skip-trust --yolo -o json -m gemini-3-pro-preview \
  -p "Build a self-contained X in <abs/path>. Requirements: ... Then briefly report what you built." \
  > <abs/path>/.gemini-run.json 2>/dev/null
```

Run that with `run_in_background: true` and a generous timeout (600000 ms is reasonable for non-trivial builds). When you get the completion notification, `Read` the `.gemini-run.json` file — never re-execute the gemini call to "check progress."

## Parsing the output

The JSON envelope (when `-o json`) looks like this:

```json
{
  "session_id": "...",
  "response": "Natural-language summary of what the agent did.",
  "stats": {
    "models": {
      "gemini-3.1-pro-preview": {
        "api": { "totalRequests": 4, "totalErrors": 0, "totalLatencyMs": 77401 },
        "tokens": { "input": 190561, "prompt": 322174, "cached": 131613, "total": 329661, ... }
      }
    },
    "tools": {
      "totalCalls": 5, "totalSuccess": 5, "totalFail": 0,
      "byName": { "write_file": { "count": 3, ... }, "update_topic": { "count": 2, ... } }
    },
    "files": { "totalLinesAdded": 393, "totalLinesRemoved": 0 }
  }
}
```

What to do with each field:

- **`.response`** — treat this as the subagent's report. Surface salient bits to the user; don't paste the whole thing unless they ask.
- **`.stats.tools.byName`** — fast sanity check that Gemini did what you expected. If you asked it to write three files and `write_file.count` is 0, the task failed silently.
- **`.stats.files.totalLinesAdded`** — quick size sniff.
- **`.stats.models.<model>.api.totalErrors`** — confirms the run was clean.
- **`.stats.models.<model>.tokens.total`** — useful if the user later asks "what did that cost?"

You don't need to surface stats unless asked. The point of this whole pattern is that you can know the run succeeded without re-reading the work itself.

## Prompting Gemini well

Gemini honors explicit constraints precisely — if you say "no build step, no external CDN, vanilla JS," it won't drift. Lean into that:

- **Absolute paths only.** e.g. `/abs/path/to/project/index.html`, not `./index.html`. Gemini starts in a working directory you may not have set deliberately.
- **Enumerate features as a checklist.** "Add task / mark complete / delete / filter all-active-completed / clear-completed / localStorage / counter" beats "make it have all the usual todo features."
- **State negative constraints explicitly.** "No frameworks. No CDN. No build step." If you don't say it, Gemini may default to whatever's idiomatic.
- **Ask for a brief final report.** End the prompt with "then briefly report what you built / what you found." The report goes into `.response` and is what you surface back to the user.
- **Don't ask Gemini to start a long-running server / dev process.** It'll either skip it or hang. Build artifacts, don't run them.

## Real-time steering via ACP mode (interactive co-pilot pattern)

The one-shot dispatch is powerful but blind: you set up the task, hit Enter, and only see results at the end. For tasks where you want to **watch Gemini work and redirect it mid-flight**, use the ACP (Agent Client Protocol) mode instead. This makes the relationship genuinely bidirectional — you see every thought, tool call, and message as it happens, and you can cancel + reprompt at any moment.

A reusable Python wrapper for this is bundled with the skill at `scripts/acp_client.py`. Use it directly; don't re-derive the JSON-RPC layer.

### The protocol in one paragraph

`gemini --acp` speaks JSON-RPC 2.0 over stdio. The client (you) calls `initialize` → `session/new` → `session/prompt`. Inside a turn, the agent streams `session/update` notifications carrying `agent_thought_chunk` (reasoning), `agent_message_chunk` (its eventual reply), `tool_call` / `tool_call_update` (each tool invocation and its result), and `plan` (its own task list). When the agent wants to use a tool it didn't pre-authorize, it sends `session/request_permission` and waits for your `outcome`. To stop a turn, send `session/cancel` (a notification — no response expected); the agent ends the turn with `stopReason: "cancelled"` and the session stays alive, so you can immediately send a new `session/prompt` to steer in a different direction.

### What you can reach and how

| What you want to do | How |
|---|---|
| See agent's reasoning in real time | Read `session/update` events of type `agent_thought_chunk` |
| See every file/shell action live | `session/update` of type `tool_call` (input) and `tool_call_update` (result) |
| Approve / deny / steer at each tool call | Run with `--approval-mode default`; the agent sends `session/request_permission` per tool call. Your response decides allow/reject. This is the only structured per-tool injection point. |
| Stop the agent and redirect | Send `session/cancel`, then `session/prompt` with new instructions. Session history is preserved across turns. |
| Resume across separate process invocations | Use `--session-id <uuid>` on launch + `-r <uuid>` on follow-up runs |

### The bundled wrapper

`scripts/acp_client.py` exposes a trivial line protocol on stdin so you can drive it via `desktop-commander`'s `start_process` + `interact_with_process` + `read_process_output`:

```
INIT                   handshake + create session (do this first)
PROMPT <free text>     send a session/prompt
CANCEL                 send session/cancel for the active turn
STATUS                 print current turn state + last update summary
QUIT                   terminate gemini and exit
```

Every `session/update` is summarized to one line on stdout, so reading the process output gives you a flat real-time log of what Gemini is doing. The wrapper auto-approves all permission requests (`AUTO_PERMISSION=True`) by default — flip the constant if you want per-tool steering opportunities.

### Workflow for a steering session

1. **Launch the wrapper in the background** via `desktop-commander.start_process("python -u <path>/acp_client.py", timeout_ms=8000)`. Note the PID.
2. **`interact_with_process(pid, "INIT")`** to do the handshake. Read output until you see `[init] session_id=...`.
3. **`interact_with_process(pid, "PROMPT <your task>")`**. Then loop on `read_process_output(pid, timeout_ms=…)` to watch progress in real time. Each call returns any new lines since you last read.
4. **When you see something you want to change**, decide which intervention:
   - Just want to abort cleanly? `CANCEL` + `QUIT`.
   - Want to redirect? `CANCEL`, wait for `[prompt-done] {"stopReason": "cancelled"}`, then `PROMPT <new instructions>`.
5. **When the turn ends naturally**, you'll see `[prompt-done] {"stopReason": "end_turn", ...}` with token telemetry — same data as the one-shot JSON envelope.
6. **`QUIT`** when done.

### Important: cancel is best-effort, not transactional

This is the failure mode that bites hardest. When you send `session/cancel`, tool calls that have *already been issued* by the agent will run to completion — they're not retracted. Worse, when you follow up with a redirect prompt that asks the agent to "revert what you started," the agent will often claim to have done so but only partially succeed: it'll clean the obvious site (e.g. a schema definition) and miss the downstream call sites that referenced it, none of which it ran to verify.

The lesson: **after cancel + redirect, do not trust the agent's "I reverted" claim**. Run the code, or read the files, and clean up the residue yourself. If the in-flight writes are irreversible at any cost, don't use yolo approval — use `--approval-mode default` so you get to inspect each tool call *before* it executes.

### When the steering pattern is worth it (vs. one-shot)

Worth it:
- You're not sure what the right approach is and want to react to what the agent picks.
- The task has many degrees of freedom (creative features, design choices) and you want a chance to redirect early.
- You want to demonstrate the agent's behavior to the user — the live stream is much more compelling than a fait-accompli JSON dump.
- The work is exploratory and you might want to stop early once you've learned what you needed.

Not worth it:
- The task is fully specified and you just want it done. One-shot is faster, simpler, and uses less of your tool budget.
- You can't pay attention while it runs. ACP without supervision is just a slower one-shot.
- The work is destructive enough that the cancel-is-best-effort failure mode is unacceptable. Either use approval mode (per-tool gating) or scope the work to a worktree.

### Other useful steering levers

- **`session/setSessionMode`** lets you change the approval mode mid-session (e.g., start with yolo for cheap setup, switch to approval-required when the agent reaches the destructive part).
- **`session/unstable_setSessionModel`** swaps the model mid-session — flash for setup, pro for hard parts. Marked unstable in the spec.
- **`--session-id <uuid>` + `--resume <uuid>`** lets you serialize a long conversation across separate gemini process invocations. Useful if you want to checkpoint and come back later without keeping the wrapper alive.

## Co-development pattern: you and Gemini as a two-person team

This is the most powerful use of the skill. Instead of "Gemini does the whole thing while I wait," you can split a system in two and work in genuine parallel — you on half, Gemini on the other half — meeting at a shared interface. Total elapsed time becomes the slower half, not the sum. The pattern works well for backend/frontend splits, producer/consumer pairs, and library/test splits: anything where the two halves can be specified independently against a frozen contract.

You are still the lead. Gemini gets one shot per dispatch; if it makes a wrong call mid-build, it can't ask. So your job is to make wrong calls impossible.

### The protocol

**1. Write a frozen contract before either of you starts.**

Create a `SPEC.md` in the project root that pins down the interface. For a backend/frontend split, that means: stack and runtime, port and host, every endpoint with method/path/body/response, exact field names, status code semantics, error envelope, CORS behavior, seed data. Don't leave anything implicit at the seam — Gemini will fill ambiguity by guessing, and its guess won't match yours.

Specifically, address the things that bite:
- **Vocabulary**: enumerate enums (label values, status values, etc.) and what `null` means.
- **Ordering**: if positions/indexes are involved, state whether they're dense, sparse, who reshuffles, and whether the index is interpreted in the source or destination context after a move.
- **Errors**: state the error envelope shape and which scenarios map to which HTTP codes.
- **Seed data**: if the frontend will render nothing without it, the backend must seed on first run — say so explicitly.
- **Origin handling**: CORS for `*`, OPTIONS preflight on PATCH/DELETE. State it.

**2. Create a shared `STATUS.md` for the handshake.**

The format that worked:

```
# STATUS — <Project> Co-Build

## Open questions / blockers
_(empty)_

## Backend (Claude) — progress log
- T+0 ...

## Frontend (Gemini) — progress log
_(Gemini fills this in)_

## Integration checklist
- [ ] ...
- [ ] ...
```

In the dispatch prompt, instruct Gemini to **append** to its section when done, listing what it built, deviations from spec (with rationale), things it wants you to verify on your side, and an integration-confidence rating (high/medium/low). Don't ask it to read your section before starting — its section is for *output*, not coordination.

**3. Dispatch Gemini in the background, then build your half in the foreground.**

The genuine parallelism is the win. Gemini's run takes 1–3 minutes for a non-trivial half; you can comfortably finish your half in roughly the same time. Use the standard wrapper pattern: Bash `run_in_background: true`, stdout to a sidecar JSON, wait for the harness notification.

**4. When your half is done, smoke-test it before Gemini's notification arrives.**

This is the most underrated step. The integration's risk is at the seam, not in either half. Verify *your* side against the spec with curl (or equivalent) before Gemini lands — that way when you do receive its output, you can attribute any failure to the frontend without ambiguity. Run the exact request shapes the spec promises, including the edge cases (null label, cross-column move, OPTIONS preflight).

**5. On Gemini's completion: read STATUS, then audit the contract seam.**

Don't open a browser yet. First, read Gemini's STATUS entry — it'll often flag things it wants you to confirm, and those are usually the right things to confirm. Then:

- Grep its source for every API path it calls (`fetch(\``, `${API_BASE}`) and check each one against the spec.
- Look for payload-shape pitfalls — anywhere JavaScript or HTML types don't round-trip cleanly to JSON. A common one: an `<option value="null">None</option>` produces the *string* `"null"` from `select.value`, not JSON null; either side may forget to map it.
- Check XSS-safety on user-input rendering (`textContent` vs `innerHTML`).

**6. Run the integration test.**

End-to-end verification via Playwright headless is the right tool: load the frontend over `file://`, wait for `networkidle` and a key selector to appear (proves the backend was reached), simulate one full happy-path (open modal, edit, save, check the value re-rendered), check console for errors. If the frontend renders, your DB write goes through, no console errors — the seam holds.

**7. If something's off, dispatch a Gemini round 2.**

The "check-in" isn't real-time — Gemini is one-shot — but you can dispatch a second call with a tight, specific brief: "Read SPEC.md and STATUS.md. Your previous build is in frontend/. Two issues found: (1) you call PATCH /api/cards/:id with `label_id`, spec says `label`; (2) the drag-leave handler misses cross-column. Fix those and only those, and append a new STATUS entry."

### Dispatch prompt template (frontend half of a stack)

```
You are the frontend developer on a two-person team. Your teammate (Claude)
is building the backend in parallel right now. They are the lead.

READ FIRST: <abs/path>/SPEC.md — frozen API contract. Do not rename endpoints
or change field names. If you find a problem with the spec, append an entry
under "Open questions / blockers" in STATUS.md and STOP without changing
behavior.

Implement the frontend in <abs/path>/frontend/ per spec. <stack + constraints
spelled out>. Wire all API calls per SPEC endpoints. Implement all behavior
listed in SPEC's "Behavior the frontend should implement" section.

When done, APPEND a markdown section to <abs/path>/STATUS.md under
"## Frontend (Gemini) — progress log" with bullets covering:
- what you built
- any deviations from spec (with rationale)
- anything you want Claude to verify on the backend side
- your final integration confidence (high/medium/low)

DO NOT modify SPEC.md. DO NOT touch the backend/ folder. DO NOT start a server.
After implementing, briefly report back what you built and any concerns.
```

### What goes wrong, and how to prevent it

- **Symmetric ambiguity**: if your spec says "position is the new index" without saying *in which column after a move*, the frontend and backend will pick opposite interpretations and the bug will be invisible until cross-column drag fires. Fix: write down the answer to "interpreted in source or destination" explicitly.
- **`null` round-trips**: HTML forms can't natively express JSON null. A `<select>` option value is always a string. Either tell Gemini to map sentinel strings back to JSON null (it'll do this if you remind it), or accept both in the backend.
- **CORS for `file://` origin**: `file://` reports as origin `"null"` (the string). `Access-Control-Allow-Origin: *` covers it; an echoed-origin policy doesn't.
- **Gemini's `.stats.files.totalLinesAdded` reads 0** when it routes its writes through an MCP server (e.g., `desktop-commander__write_file`) instead of its native `write_file`. The files exist; the counter doesn't reflect them. Don't use that counter as a "did it write anything" check — use the filesystem directly.

## Prompt template for a self-contained build

```
Build <thing> in <absolute path>. Requirements:
- <stack + runtime + "no build step / no CDN / no frameworks" if applicable>
- <enumerated feature list as a checklist>
- <UI/UX constraints if applicable, e.g. accessibility, keyboard, visual style>
- <hard exclusions: external services, network calls, additional dependencies>

Create the files, then briefly report what you built. Do not start a server.
```

Be specific about absolute paths, enumerate features as a checklist, and state negative constraints explicitly — Gemini honors all of these precisely.

## Pitfalls (in order of how badly they bite)

1. **Forgetting `--skip-trust` or `--yolo`** → silent hang until harness timeout. If a gemini call appears to never finish, this is almost always why.
2. **Forgetting `-o json`** → output is human prose mixed with progress lines; parsing breaks.
3. **Forgetting stderr redirect** → noise floods your logs. Worse, if you accidentally `2>&1`-merge stderr into stdout, JSON parsing breaks.
4. **Relative paths in the prompt** → files land somewhere unexpected. Always use absolute paths.
5. **Polling for completion** → wastes context. Use `run_in_background: true` and wait for the notification.
6. **Re-issuing the gemini call to "check progress"** → spawns a second parallel run. Just `Read` the sidecar file once notified.
7. **Model-name aliasing confusion** → `-m gemini-3-pro-preview` reports as `gemini-3.1-pro-preview` in `.stats.models`. This is normal.
8. **Treating Gemini like a Claude subagent that shares your context** → it doesn't. Re-explain the task fully each invocation.

## Quick reference card

```bash
# heavy build / long generation
gemini --skip-trust --yolo -o json -m gemini-3-pro-preview \
  -p "<explicit task with absolute paths and constraints>" \
  > <path>/.gemini-run.json 2>/dev/null

# lighter summary / fast pass
gemini --skip-trust --yolo -o json -m gemini-3-flash-preview \
  -p "<task>" \
  > <path>/.gemini-run.json 2>/dev/null

# read result
# Read <path>/.gemini-run.json → .response is the report, .stats has telemetry
```
