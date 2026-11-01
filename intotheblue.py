#!/usr/bin/env python3
"""intotheblue — single entry point for the Bluetooth test bench.

Two front-ends over the same scanners, selected by a flag:

    python intotheblue.py --cli      interactive curses menu (scan/select/act) [default]
    python intotheblue.py --webui    web UI + MCP server (web at /, MCP at /mcp)

They are **mutually exclusive**: both open the same nRF52 dongle, and a second open
fails (`rpc_h5_transport_state`), so only one may run at a time. `--host`/`--port`
apply to `--webui` only.

Nothing here is installed — this file and the `webui/`/`bledev/` packages load from the
working directory. Run it with the pinned interpreter: `.venv310/bin/python intotheblue.py`.
"""
from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="intotheblue", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cli", action="store_true",
                      help="interactive curses menu (default if no flag given)")
    mode.add_argument("--webui", action="store_true",
                      help="web UI + MCP server (owns the dongle; persists to SQLite)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="--webui bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="--webui port (default: 8000)")
    args = parser.parse_args()

    if args.webui:
        import uvicorn
        print(f"intotheblue webui on http://{args.host}:{args.port}  (MCP at /mcp)")
        print("This process owns the dongle — do not run --cli at the same time.")
        uvicorn.run("webui.app:app", host=args.host, port=args.port)
        return

    # Default: the interactive CLI.
    import cli
    try:
        cli.main()
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
