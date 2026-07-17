"""Reusable full-screen curses "live table" for the scan activities.

A scanner object is anything exposing this informal interface:
    - snapshot() -> list[record]     current records (each with .rssi, .last_seen)
    - is_scanning -> bool            property
    - pause(), resume()              toggle scanning
    - save_csv() -> str              persist and return the file path

Columns are (header, width, function(record) -> str) tuples. The table is sorted
by RSSI (strongest first) and the bottom bar shows the commands:

    ^P  pause/resume      ^S  save to CSV      Q  quit
"""
from __future__ import annotations

import curses
import sys
import termios

Column = tuple[str, int, "callable"]


def cell(text: str, width: int) -> str:
    text = text or ""
    if len(text) > width:
        return text[: width - 1] + "…"
    return text.ljust(width)


def _disable_flow_control() -> None:
    """Disable IXON so Ctrl+S (0x13) reaches us instead of freezing the TTY."""
    fd = sys.stdin.fileno()
    attrs = termios.tcgetattr(fd)
    attrs[0] &= ~termios.IXON
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _draw(stdscr, scanner, columns: list[Column], title: str, last_saved: str | None) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    header = "".join(cell(h, w) + " " for h, w, _ in columns).rstrip()
    stdscr.addnstr(0, 0, header, width - 1, curses.A_BOLD | curses.A_UNDERLINE)

    records = sorted(scanner.snapshot(), key=lambda r: r.rssi, reverse=True)
    for i, d in enumerate(records):
        y = i + 1
        if y >= height - 2:  # leave room for the status + command bars
            break
        line = "".join(cell(func(d), w) + " " for _, w, func in columns).rstrip()
        stdscr.addnstr(y, 0, line, width - 1)

    state = "SCANNING" if scanner.is_scanning else "PAUSED"
    status = f" {title} · {state} · {len(records)} devices"
    if last_saved:
        status += f" · saved: {last_saved}"
    commands = "  ^P pause/resume   ^S save CSV   Q quit "

    stdscr.addnstr(height - 2, 0, status.ljust(width - 1), width - 1, curses.A_REVERSE)
    stdscr.addnstr(height - 1, 0, commands.ljust(width - 1), width - 1, curses.A_BOLD)
    stdscr.refresh()


def _ui(stdscr, scanner, columns: list[Column], title: str) -> None:
    curses.curs_set(0)
    _disable_flow_control()
    stdscr.nodelay(True)
    stdscr.timeout(250)  # redraw at least every 250ms

    last_saved: str | None = None
    while True:
        _draw(stdscr, scanner, columns, title, last_saved)
        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch in (ord("q"), ord("Q")):
            return
        if ch == 16:  # Ctrl+P
            scanner.resume() if not scanner.is_scanning else scanner.pause()
        elif ch == 19:  # Ctrl+S
            last_saved = scanner.save_csv()


def run(scanner, columns: list[Column], title: str) -> None:
    """Run the curses live table for the given scanner until the user quits."""
    curses.wrapper(_ui, scanner, columns, title)
