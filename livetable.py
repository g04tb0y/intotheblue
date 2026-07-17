"""Reusable full-screen curses "live table" for the scan activities.

A scanner object is anything exposing this informal interface:
    - snapshot() -> list[record]     current records (each with .rssi, .last_seen)
    - is_scanning -> bool            property
    - pause(), resume()              toggle scanning
    - save_csv() -> str              persist and return the file path

Columns are (header, width, function(record) -> str) tuples. Rows keep their
discovery order (new devices append at the bottom) so they don't jump around
under the cursor. One row is highlighted; the bottom bar shows:

    ↑↓ select   ⏎ interact   ^P pause/resume   ^S save CSV   Q quit

run() returns the record the user selected with Enter (so the caller can act on
it), or None if the user quit with Q.
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


def _draw(stdscr, records, scanner, columns, title, selected, last_saved) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    header = "".join(cell(h, w) + " " for h, w, _ in columns).rstrip()
    stdscr.addnstr(0, 0, header, width - 1, curses.A_BOLD | curses.A_UNDERLINE)

    for i, d in enumerate(records):
        y = i + 1
        if y >= height - 2:  # leave room for the status + command bars
            break
        line = "".join(cell(func(d), w) + " " for _, w, func in columns).rstrip()
        attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
        stdscr.addnstr(y, 0, line.ljust(width - 1), width - 1, attr)

    state = "SCANNING" if scanner.is_scanning else "PAUSED"
    status = f" {title} · {state} · {len(records)} devices"
    if last_saved:
        status += f" · saved: {last_saved}"
    commands = "  ↑↓ select   ⏎ interact   ^P pause/resume   ^S save CSV   Q quit "

    stdscr.addnstr(height - 2, 0, status.ljust(width - 1), width - 1, curses.A_REVERSE)
    stdscr.addnstr(height - 1, 0, commands.ljust(width - 1), width - 1, curses.A_BOLD)
    stdscr.refresh()


def _ui(stdscr, scanner, columns: list[Column], title: str):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.set_escdelay(25)  # assemble arrow-key escape sequences promptly
    stdscr.keypad(True)
    _disable_flow_control()
    stdscr.timeout(250)  # redraw at least every 250ms

    selected = 0
    last_saved: str | None = None
    while True:
        records = scanner.snapshot()  # discovery order; no live re-sorting
        selected = max(0, min(selected, len(records) - 1)) if records else 0
        _draw(stdscr, records, scanner, columns, title, selected, last_saved)

        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch in (ord("q"), ord("Q")):
            return None
        if ch in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            selected = min(len(records) - 1, selected + 1) if records else 0
        elif ch in (curses.KEY_ENTER, 10, 13):
            if records:
                return records[selected]
        elif ch == 16:  # Ctrl+P
            scanner.resume() if not scanner.is_scanning else scanner.pause()
        elif ch == 19:  # Ctrl+S
            last_saved = scanner.save_csv()


def run(scanner, columns: list[Column], title: str):
    """Run the live table; return the record selected with Enter, or None on quit."""
    return curses.wrapper(_ui, scanner, columns, title)
