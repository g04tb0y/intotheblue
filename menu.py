"""Generic line-based menu tree with unlimited depth and uniform Back navigation.

A `Menu` node builds its options on demand (so dynamic lists refresh). An option's
action returns:
  - a `Menu`         -> descend into it
  - `BACK`           -> go up one level (same as the always-present `b`)
  - `EXIT`           -> leave the whole tree
  - `None`           -> stay on the current node (used after a leaf action)

Nodes may declare `on_enter` / `on_leave` to manage resources (e.g. connect on the
way in, disconnect on the way out). `on_enter` returning False aborts the descent.
A breadcrumb of the current path is printed so depth is always visible.
"""
from __future__ import annotations

import sys

BACK = object()
EXIT = object()


def _header(titles: list[str]) -> str:
    """Marked, breadcrumb-style menu header: bar marker + path with the current
    node highlighted, underlined by a rule. Colours only on a real terminal."""
    plain = "▌ " + " ▸ ".join(titles)
    rule = "─" * max(len(plain), 12)
    if not sys.stdout.isatty():
        return f"\n{plain}\n{rule}"
    *ancestors, current = titles
    crumb = ("\033[2m" + " ▸ ".join(ancestors) + " ▸ \033[0m") if ancestors else ""
    crumb += "\033[1;36m" + current + "\033[0m"
    return f"\n\033[36m▌\033[0m {crumb}\n\033[2m{rule}\033[0m"


class Menu:
    def __init__(self, title, build, on_enter=None, on_leave=None):
        self.title = title              # str shown in the breadcrumb
        self.build = build              # () -> list[(key, label, action)]
        self.on_enter = on_enter        # () -> bool|None ; False aborts entry
        self.on_leave = on_leave        # () -> None


def _read(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "b"


def _leave(node: Menu) -> None:
    if node.on_leave:
        node.on_leave()


def run(root: Menu) -> None:
    """Drive the menu tree rooted at `root` until it is exited or backed out of."""
    if root.on_enter and root.on_enter() is False:
        return
    stack = [root]
    while stack:
        node = stack[-1]
        items = node.build()
        print(_header([n.title for n in stack]))
        for key, label, _ in items:
            print(f"  {key}) {label}")
        print("  b) Back")

        choice = _read("Select: ")
        if choice in ("b", "back", ""):
            _leave(stack.pop())
            continue
        action = next((a for k, _, a in items if k == choice), None)
        if action is None:
            print(f"  Invalid choice: '{choice}'")
            continue

        result = action()
        if result is BACK:
            _leave(stack.pop())
        elif result is EXIT:
            while stack:
                _leave(stack.pop())
            return
        elif isinstance(result, Menu):
            if result.on_enter and result.on_enter() is False:
                continue
            stack.append(result)
        # result is None -> stay on the current node and re-render
