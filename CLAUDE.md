# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Bluetooth device and protocol testing bench, used **exclusively in a quality environment against authorized devices, which involves also stress test**. It grew out of a need that shapes the whole design: testers often *don't* have complete information to recognize or locate a target device, are almost never in an RF-isolated environment, and **determining what a device exposes is itself part of the test**. That is why so much of the code is about passive identification/enrichment (vendor, advertised services, Class of Device, Fast Pair, address type) — you have to correctly identify a target before acting on it. The interactive scan → select → act interface exists to replace error-prone copy-paste of addresses between separate tools.

Roadmap direction (keep new work consistent with this): cover every aspect of the Bluetooth protocol — passive **and** active enumeration, interaction as **client and server**, and device **cloning/emulation**. New capabilities should slot into the existing scan/select/act model rather than becoming standalone one-off scripts.

## NOTES.md — working memory

`NOTES.md` is the project's running memory and scratchpad. **Read it at the start of a session and keep it current as you work** — update it in the same change as the work it describes. It holds:

- **Actions log** — what was done and why (rationale beyond what git shows).
- **TODO** — planned / next work.
- **Design decisions** — choices made and the reasoning, so they aren't re-litigated.
- **Open questions** — things to study or evaluate (protocol details, hardware quirks, ideas not yet decided).

Prefer NOTES.md over ad-hoc memory for anything that should survive across sessions.

## Environment & hard constraints

- **Python 3.10 only.** `blatann` pulls in `pc-ble-driver-py`, which has no wheels for Python ≥ 3.11. The system Python (3.13/3.14) will fail to install deps. The pinned interpreter lives in `.venv310/`.
- **Code and all on-screen/UI text are written in English** (chat with the maintainer is in Italian).
- **Two independent hardware backends** — most confusion comes from mixing them up:
  - *BLE side* (`scan.py`, `scan_live.py`, `client.py`, `emulate.py`): a **Nordic nRF52 dongle flashed with Connectivity firmware**, driven through `blatann` → `pc-ble-driver-py` over serial (`/dev/ttyACM*`). This is a serial controller, **not** an HCI adapter — it never appears in `hciconfig`.
  - *Classic/BR-EDR side* (`scan_classic.py`): the **host Bluetooth adapter** via BlueZ (`bluetoothctl` subprocess). Needs `hci0` up (`sudo systemctl start bluetooth && sudo hciconfig hci0 up`). Does not use the dongle at all.

## Commands

```bash
# One-time environment (uv fetches a standalone CPython 3.10)
uv venv --python 3.10 .venv310
.venv310/bin/python -m pip install -r requirements.txt

# Main entry point — one bench, two front-ends (mutually exclusive)
.venv310/bin/python intotheblue.py --cli                       # interactive curses menu (default)
.venv310/bin/python intotheblue.py --webui --port 8000         # web UI + MCP server

# Standalone tools (each takes --port; BLE tools autodetect the dongle)
.venv310/bin/python scan_live.py          # live BLE scan table
.venv310/bin/python scan.py --timeout 8   # batch BLE scan -> csv/
.venv310/bin/python scan_classic.py       # live Classic scan (needs hci0 up)
.venv310/bin/python client.py --address AA:BB:CC:DD:EE:FF   # GATT explore
.venv310/bin/python emulate.py --name TestDevice           # act as peripheral

```

`intotheblue.py` is the single entry point (`--cli` / `--webui`); `cli.py` still works on
its own and takes a direct activity, e.g. `python cli.py webui`. `webui/` is an **in-repo
package, not an installed one** (like `bledev/`) — nothing is `pip install`ed but the
requirements; everything loads from the working directory, never as a standalone
`python -m` package.

**CLI and web are mutually exclusive** — both open the same dongle, and a second open
fails (`rpc_h5_transport_state`). Run the curses CLI **or** the web server, never both.

There is **no automated test suite**. Verification is done by running against real hardware. Two things to know:
- Curses UIs are validated by driving them through a **pseudo-terminal** (`pty`), sending keys and asserting on output / generated CSV — the harnesses aren't committed; recreate them under the scratchpad when needed. Application-cursor-mode arrows are `\x1bOB`/`\x1bOA` (not `\x1b[B`).
- The dongle can enter a **transient bad state** (`NrfError.rpc_h5_transport_state` / `timeout`) between back-to-back sessions. `bledev.open_device` retries, but after heavy runs it may need a few seconds idle before it reopens cleanly.

## Architecture (the cross-cutting picture)

**Shared live-table UI — `livetable.py`.** A single curses full-screen table renderer driven by any *scanner* object implementing an informal interface: `snapshot() -> list[record]`, `is_scanning` (property), `pause()`, `resume()`, `save_csv() -> path`. `run(scanner, columns, title)` returns the record the user selected with Enter (or `None` on `Q`). Both `scan_live.LiveScanner` (BLE) and `scan_classic.ClassicScanner` (Classic) implement this interface, so adding a new scan type mostly means writing a scanner + a `COLUMNS` list. Deliberate behaviors: rows stay in **discovery order** (no live re-sorting, so selection doesn't jump); `columns` is an extensible `(header, width, fn)` list; `IXON` is disabled so `Ctrl+S` reaches the app; scanners that emit logs must route them to a file (see `scan_live.run`) or they corrupt the screen.

**Orchestration — `cli.py`.** An `ACTIVITIES` registry maps menu keys to handlers. The core loop is `_scan_interact_loop`: show the live table → on selection **pause the scanner**, run a per-device action, **resume**, and return to the *same* scan (`Q` exits to the main menu). Per-device action menus are built with the data-driven `_device_action(title, options, default)` helper (one line per action). **Critical invariant for BLE:** the dongle stays *open* across an interaction — you pause scanning and connect on the **same** `BleDevice`. Do not close-and-reopen the dongle between scanning and connecting; a fresh open immediately after close fails (`rpc_h5_transport_state`). `scanner.ble_device` exposes the open device for this.

**BLE plumbing — `bledev/`.** `device.py::open_device` autodetects the dongle from `/dev/serial/by-id/*nRF52*` (fallback `/dev/ttyACM0`), retries transient open failures, and takes `configure=True` for the peripheral role (the GATT server must be sized *before* `open()`). `manufacturers.py` resolves BLE Company IDs from `manufact.yaml`.

**Identification & decoding.** `scan_live.DeviceRecord.update()` is where advertising is turned into identity: vendor/protocol from manufacturer data and service-data UUIDs (`_decode_protocol`), Fast Pair via service UUID `0xFE2C` (`_fast_pair`), address type, connectable flag. `client.py` reuses blatann's `UUID_DESCRIPTION_MAP` for GATT **service/characteristic common names** and `_interpret()` to decode well-known values (Battery %, Appearance, Tx Power, PnP ID, Heart Rate, DIS strings). `scan_classic.py` decodes the **Class of Device** and enriches records via `bluetoothctl info` (CoD/Icon are not emitted in the scan event stream, only in `info`).

**Passive vs active boundary.** `exposure.py` is a **passive, read-only** classifier: it only interprets advertising already collected and sends no packets. Active/intrusive test primitives (e.g. L2CAP flooding, which lives in dedicated external tooling written in C for the required socket throughput) are **not** implemented in this repo. Keep passive analysis (`exposure.py`, record enrichment) strictly non-transmitting, and gate anything that connects/writes behind an explicit user action in the interaction menu.

**Persistence — `store.py`.** A stdlib-`sqlite3` store (the deliberately *lightweight* DB; no new native dep, unlike blatann) at `db/intotheblue.db` (git-ignored; override with `$INTOTHEBLUE_DB`, tests use `":memory:"`). It imports **no blatann**, so the read side and headless tests load without hardware. Any backend's record (`DeviceRecord`/`LeRecord`/`ClassicRecord`) upserts into one `device` row by normalised address (drop blatann's `",r"` suffix, upper-case); the merge is **latest-non-empty-wins** and carries the same Fast Pair precedence as `scan_fused._merge_fast_pair` (a decoded Model ID beats a bare `"Yes"`). `sighting` keeps the per-scan RSSI history; `gatt_service`/`gatt_characteristic` store a browsed GATT tree (latest discovery replaces the old). `row_to_record()` rebuilds a record-like object so the passive analysers (`exposure`, `bond`, `scan_fused.target_note`) are reused unchanged on the DB read side.

**Web + MCP — `webui/`.** A **third front-end over the same scanners**, not a parallel stack (roadmap: new capabilities slot into scan/select/act). One FastAPI process = web UI (Jinja2 + HTMX, no build step; HTMX vendored under `static/vendor/` for offline use) + JSON API + an MCP server (FastMCP, streamable HTTP, mounted at `/mcp`). `engine.py` is the **single hardware owner**: it holds at most one active scanner, serialises every dongle op behind one lock, persists `snapshot()` to `store` on a background flush thread, and runs `browse_gatt()` by **pausing the scan and connecting on the same open `BleDevice`** (the `_scan_interact_loop` invariant), reusing `client.*`/`capabilities`/`exposure`. The web table reuses each scanner's `COLUMNS` cell functions, so it matches the CLI exactly. **Lifespan gotcha:** a mounted MCP sub-app's own lifespan doesn't run, so `app.py`'s FastAPI lifespan enters `mcp.session_manager.run()` itself (and stops any scan on shutdown). The MCP tools are thin wrappers over `engine` + `store`; read tools are passive, `scan_start`/`scan_stop`/`browse_gatt` drive the radio (said so in each docstring). OpenTelemetry is disabled in `webui/__init__.py` before FastAPI imports, keeping the bench non-phoning-home.



## Documentation & Dependency Management
- **Outdated Knowledge Assumption**: ALWAYS assume that your pre-existing/internal understanding of dependencies, libraries, frameworks, tools, and their underlying implementations is outdated. This applies universally across all forms of dependency, not just external API integrations.
- **Context 7 MCP Usage**: ALWAYS use the `context 7` MCP tool to fetch up-to-date documentation, version details, and current implementation patterns before writing code or implementing solutions involving any dependency. If somehow `context 7` does not cover the relevant subject matter, use web search and web fetch tool calls instead.

## Communication & Progress Updates
- **Conciseness**: Keep outputs focused, brief, and direct. Keep disclaimers and caveats short, spending most of the response on the main answer.
- **Progress Cadence**: Before your first tool call, state in one sentence what you are about to do. While working, give a brief update only when you find something important or change direction.
- **Final Output Structure**: Lead directly with the outcome: your first sentence should answer "what happened" or "what did you find," followed by supporting details.
- **Written Deliverables**: Match written documents and files to substance. Cover what is needed without padding with filler sections, redundant summaries, or boilerplate.
- **Correction Narration**: Only state corrections to earlier statements if the error alters the user's code, conclusions, or decisions. State the fix plainly and briefly, then move on.

## Action Defaults & Tool Execution
- **Default to Action**: Implement changes directly rather than only proposing them. Infer intent and proceed using tools to discover details rather than guessing.
- **Parallel Tool Calling**: When calling multiple tools without sequential dependencies (such as reading multiple files or performing speculative searches), execute independent tool calls in parallel.
- **No Guessing**: Never use placeholders or guess missing parameters in tool calls.
- **Scratchpad Cleanup**: Clean up temporary scratchpad scripts or temporary test files at the end of the session.


## Task Scope & Over-Verification Safeguards
- **Exact Scope**: Deliver what was asked, at the scope intended. Make routine judgment calls yourself, and check in only when different readings of the request would lead to materially different work. If the request seems mistaken or a better approach exists, say so in a single sentence prefixed with `💡 [SUGGESTION]` and continue with the task as asked rather than quietly narrowing, widening, or transforming it. Finish the whole task, and stop short of actions that are clearly beyond what was asked.
- **Preserve Tests**: Never edit or alter existing tests to pass without explicitly asking for authorization.

## Subagent Orchestration & Damping
- **Delegation Boundaries**: Delegate to subagents ONLY for large tasks that are genuinely independent and parallelizable (such as wide multi-file investigations).
- **Delegation Restrictions**: Do not delegate work you can finish yourself in a handful of tool calls, and do not use subagents to verify or double-check your own work.

## Safety & Reversibility Guardrails
- **Reversible Local Actions**: Editing local files, running linters, and running test suites do not require prior approval.
- **Destructive & Shared Actions**: ALWAYS request user confirmation before running destructive or hard-to-reverse commands:
  - Destructive filesystem or DB operations (e.g., `rm -rf`, dropping database tables).
  - Force/hard git commands (e.g., `git push --force`, `git reset --hard`, modifying published history, bypassing hooks via `--no-verify`).
  - Shared infrastructure or external API calls with side effects.
  
## Long-Horizon Persistence & Context Compaction
- **Token Persistence**: Do not stop tasks early due to token budget concerns. Context windows are automatically compacted near limits.
- **State Preservation**: As you approach token budget limits or before context refreshes, save current progress and state to memory/files (`progress.txt`, `tests.json`, or Git history).
- **Context Refresh Discovery**: When starting after a context refresh, inspect local state files (`progress.txt`, `tests.json`, Git logs) to restore context before taking action.

## Grounding & Hallucination Prevention
- **Admit Uncertainty**: If required information is missing or no available tool fits what is requested, state so directly rather than guessing.
- **Document Analysis**: When summarizing or analyzing long context documents (>20k tokens), extract direct quotes to ground conclusions before performing tasks.

## Conventions

- Reuse the shared abstractions rather than duplicating: new scan surfaces implement the `livetable` scanner interface; new per-device actions are an entry in the relevant `_device_action` list plus a branch in the `_interact_*` handler. Web/MCP surfaces are added as `engine`/`store` methods + a route/tool, never as a second copy of scan logic.
- CSV output goes to `csv/` (git-ignored); the SQLite DB (`db/`), logs and the venv are git-ignored too.
- Commit messages in English. The working branch is `devel`; `main` is the base.
- **Documentation — follow Anthropic's best practices, matching the code's existing style.** Every module opens with a docstring stating its *purpose* **and the design rationale / the "why"** (the non-obvious decision, the hardware quirk, the failure it avoids), not just what it does. Public functions/classes get a concise docstring and full type hints; comments explain protocol details and non-obvious choices rather than restating the code. Keep docstrings, `CLAUDE.md` and `NOTES.md` current **in the same change** as the code they describe — stale docs are treated as a defect. New web/MCP/store code must meet this bar from the start.

## Rules of conduct

- **Commit and push only when the user explicitly asks, after they have validated the change on real hardware.** A commit is a user-validated checkpoint, not "it passed my isolated tests". Do NOT commit proactively when a piece of work looks done, and do NOT repeatedly offer to push — finish, report, and leave changes in the working tree until told. (Committing unvalidated work causes "fare e rifare": e.g. the SPP console was committed as "validated" then broke in the real CLI, needing a fix commit.)
- Chat with the maintainer in Italian; code and all on-screen/UI text in English (see also the Environment section).

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, used verbatim (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
