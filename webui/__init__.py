"""Web + MCP front-end for the BLE bench.

A single FastAPI process serves the web UI (Jinja2 + HTMX), a JSON/partial API and
an MCP server, all over one `engine.Engine` that owns the hardware and one `store`
for persistence. See `webui/app.py` for how they are wired and
`docs`/`CLAUDE.md` for the single-hardware-owner rationale (CLI and web are mutually
exclusive — only one process may open the dongle).

This is an offline security bench (it deliberately avoids phoning home — see the
local `fastpair_models.yaml` decision). FastAPI's OpenTelemetry instrumentation is
inert unless an OTLP endpoint is configured, but we disable it explicitly here,
*before* FastAPI is imported, so no accidental egress is ever possible.
"""
import os as _os

_os.environ.setdefault("OTEL_SDK_DISABLED", "true")

# Apply the contained blatann GATT-discovery workaround (see blatann_patch) before
# any discovery runs. Best-effort: never fail package import over it.
try:
    from . import blatann_patch as _blatann_patch

    _blatann_patch.apply()
except Exception:  # noqa: BLE001
    pass
