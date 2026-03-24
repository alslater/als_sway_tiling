#!/usr/bin/env python3
"""
als_tiling: fair grid tiling daemon for sway

Arranges windows on workspaces 1-9 into a balanced grid layout.
When windows are added or removed, all windows are redistributed equally.

Grid layout for N windows:
  1: [A]
  2: [A|B]
  3: [A|B/C]       (2 cols: 1 left, 2 right)
  4: [A/B|C/D]     (2 cols: 2 left, 2 right)
  5: [A/B|C/D|E]   (3 cols: 2+2+1)
  6: [A/B|C/D|E/F] (3 cols: 2+2+2)
"""

import i3ipc
import math
import threading

MANAGED_WORKSPACES = set(range(1, 10))  # workspaces 1-9 (screen 1)
TEMP_WS = 50                            # staging workspace for rearrangement
SCRATCHPAD_WS_NUM = -1                  # sway uses -1 for __i3_scratch
DEBOUNCE_SECS = 0.3                     # wait for burst of events to settle

_pending = {}
_lock = threading.Lock()
_arranging = 0              # >0 means als_tiling is moving windows itself
_arrange_sem = threading.Semaphore(1)  # only one arrangement runs at a time
_window_ws = {}             # con_id -> ws_num for every known window
_floating_windows = set()   # con_ids of known floating windows


def find_workspace(node, name):
    if node.type == 'workspace' and node.name == name:
        return node
    for child in node.nodes:
        result = find_workspace(child, name)
        if result:
            return result
    return None


def get_leaves(node):
    """Return all non-floating tiled leaf windows."""
    if not node.nodes:
        if node.type == 'con' and node.id:
            return [node]
        return []
    leaves = []
    for child in node.nodes:
        leaves.extend(get_leaves(child))
    return leaves


def calc_grid(n):
    """Return col_sizes list for a fair grid of n windows.

    e.g. n=5 → [2, 2, 1]  (3 columns, distributed as evenly as possible)
    """
    if n <= 1:
        return [n]
    cols = math.ceil(math.sqrt(n))
    base = n // cols
    extra = n % cols
    return [base + (1 if i < extra else 0) for i in range(cols)]


def arrange_fair(ipc, ws_name, ws_num):
    global _arranging
    _arrange_sem.acquire()
    with _lock:
        _arranging += 1
    try:
        _arrange_fair(ipc, ws_name, ws_num)
    finally:
        with _lock:
            _arranging -= 1
        _arrange_sem.release()


def _arrange_fair(ipc, ws_name, ws_num):
    # Remember which workspace is currently focused so we can restore it.
    # arrange_fair calls 'workspace number X' to place windows correctly,
    # which would visibly switch the display if X isn't currently focused.
    workspaces = ipc.get_workspaces()
    focused_before = next((w for w in workspaces if w.focused), None)

    tree = ipc.get_tree()
    ws = find_workspace(tree, ws_name)
    if not ws:
        return

    wins = get_leaves(ws)
    n = len(wins)

    if n <= 1:
        return

    col_sizes = calc_grid(n)
    win_ids = [w.id for w in wins]

    # Partition win_ids into columns before moving anything.
    it = iter(win_ids)
    columns = [[next(it) for _ in range(size)] for size in col_sizes]

    # Move ALL windows to staging to completely clear the workspace.
    # This removes any leftover intermediate containers from prior arrangements.
    for wid in win_ids:
        ipc.command(f'[con_id={wid}] move to workspace number {TEMP_WS}')

    # Refocus the (now empty) target workspace so subsequent moves land as
    # direct children of its splith — not inside any stale nested container.
    ipc.command(f'workspace number {ws_num}')

    # Pass 1 — pull each column's first window back as a flat row of direct
    # workspace siblings.  No split commands: sway places each window next to
    # the previously focused one at workspace level, building a clean splith row.
    for col in columns:
        ipc.command(f'[con_id={col[0]}] move to workspace number {ws_num}')
        ipc.command(f'[con_id={col[0]}] focus')

    # Pass 2 — for columns needing more than one row, split the column leader
    # vertically and pull remaining windows in from staging.
    for col in columns:
        prev_wid = col[0]
        for next_wid in col[1:]:
            ipc.command(f'[con_id={prev_wid}] focus')
            ipc.command('split v')
            ipc.command(f'[con_id={next_wid}] move to workspace number {ws_num}')
            ipc.command(f'[con_id={next_wid}] focus')
            prev_wid = next_wid

    # Restore previously focused workspace if we switched away from it.
    if focused_before and focused_before.num != ws_num:
        ipc.command(f'workspace number {focused_before.num}')


def schedule_arrange(ipc, ws_name, ws_num):
    """Debounce rapid window events before rearranging."""
    with _lock:
        if ws_name in _pending:
            _pending[ws_name].cancel()
        t = threading.Timer(DEBOUNCE_SECS, arrange_fair, args=[ipc, ws_name, ws_num])
        _pending[ws_name] = t
        t.start()


def find_ws_for_con(tree, con_id):
    """Return (ws_name, ws_num, is_floating) for the workspace containing con_id, or None."""
    def _search(node, current_ws, in_floating):
        if node.type == 'workspace':
            try:
                current_ws = (node.name, int(node.num))
            except (TypeError, ValueError):
                current_ws = None
        if node.id == con_id:
            return (*current_ws, in_floating) if current_ws else None
        for child in node.floating_nodes:
            result = _search(child, current_ws, True)
            if result:
                return result
        for child in node.nodes:
            result = _search(child, current_ws, in_floating)
            if result:
                return result
        return None
    return _search(tree, None, False)


def init_window_ws(ipc):
    """Populate _window_ws and _floating_windows from current tree on startup."""
    def walk(node, ws_num, in_floating):
        if node.type == 'workspace':
            try:
                ws_num = int(node.num)
            except (TypeError, ValueError):
                ws_num = None
        if node.type == 'con' and node.id and ws_num is not None:
            if not node.nodes and not node.floating_nodes:
                _window_ws[node.id] = ws_num
                if in_floating:
                    _floating_windows.add(node.id)
        for child in node.floating_nodes:
            walk(child, ws_num, True)
        for child in node.nodes:
            walk(child, ws_num, in_floating)
    walk(ipc.get_tree(), None, False)


def on_window(ipc, event):
    if event.change not in ('new', 'close', 'move'):
        return

    with _lock:
        if _arranging > 0:
            return

    con_id = event.container.id

    if event.change == 'new':
        # Find which workspace the new window landed on
        tree = ipc.get_tree()
        result = find_ws_for_con(tree, con_id)
        if not result:
            return
        ws_name, ws_num, floating = result
        _window_ws[con_id] = ws_num
        if floating:
            _floating_windows.add(con_id)
        elif ws_num in MANAGED_WORKSPACES:
            schedule_arrange(ipc, ws_name, ws_num)

    elif event.change == 'close':
        was_floating = con_id in _floating_windows
        _floating_windows.discard(con_id)
        old_ws_num = _window_ws.pop(con_id, None)
        if not was_floating and old_ws_num in MANAGED_WORKSPACES:
            tree = ipc.get_tree()
            ws = find_workspace(tree, str(old_ws_num))
            if ws:
                schedule_arrange(ipc, ws.name, old_ws_num)

    elif event.change == 'move':
        old_ws_num = _window_ws.get(con_id)

        # Find where the window is now
        tree = ipc.get_tree()
        result = find_ws_for_con(tree, con_id)
        if not result:
            return  # can't determine destination; leave _window_ws unchanged
        ws_name, new_ws_num, floating = result
        _window_ws[con_id] = new_ws_num

        # Floating windows (scratchpad, dialogs, etc.) don't occupy tiled space.
        # Skip rearrangement regardless of where they came from or went to.
        if floating:
            _floating_windows.add(con_id)
            return
        else:
            _floating_windows.discard(con_id)

        if old_ws_num == new_ws_num:
            return  # intra-workspace reposition (mod+Shift+hjkl) — ignore

        # Explicit scratchpad guard (in case window is tiled but ws num is -1)
        if old_ws_num == SCRATCHPAD_WS_NUM or new_ws_num == SCRATCHPAD_WS_NUM:
            return

        if new_ws_num in MANAGED_WORKSPACES:
            schedule_arrange(ipc, ws_name, new_ws_num)

        if old_ws_num in MANAGED_WORKSPACES:
            ws = find_workspace(tree, str(old_ws_num))
            if ws:
                schedule_arrange(ipc, ws.name, old_ws_num)


if __name__ == '__main__':
    conn = i3ipc.Connection()
    init_window_ws(conn)
    conn.on(i3ipc.Event.WINDOW, on_window)
    print('als_tiling: listening for window events...', flush=True)
    conn.main()
