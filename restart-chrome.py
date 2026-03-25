#!/usr/bin/env python3
"""
restart-chrome.py — Restart Chrome while preserving workspace assignments.

1. Scan the sway tree for all Chrome windows; record workspace + window title.
2. Kill Chrome.
3. Launch ~/bin/chrome (session restore reopens all windows/tabs).
4. Wait for the same number of windows to reappear with stable titles.
5. Match each restored window to its original workspace by title, then move it.
"""

import os
import subprocess
import sys
import time

import i3ipc

CHROME_CLASS   = 'Google-chrome'
CHROME_KILL    = 'chrome'
CHROME_LAUNCH  = [os.path.expanduser('~/bin/chrome')]

KILL_TIMEOUT   = 15   # seconds to wait for chrome to exit
LAUNCH_TIMEOUT = 60   # seconds to wait for windows to reappear
TITLE_SETTLE   = 10   # seconds to wait for titles to stabilise after windows appear

# Titles that indicate Chrome hasn't finished restoring yet
PENDING_TITLES = {'', 'New Tab', 'Restoring…', 'Restoring...'}


def chrome_leaves(ipc):
    """Return list of (ws_num, con_id, title) for every non-floating Chrome window."""
    results = []
    for ws in ipc.get_tree().workspaces():
        for con in ws.leaves():
            if con.window_class == CHROME_CLASS:
                if con.floating not in ('auto_on', 'user_on'):
                    results.append((ws.num, con.id, con.name or ''))
    return results


def titles_settled(wins):
    """True when every window has a non-pending title."""
    return all(title not in PENDING_TITLES for _, _, title in wins)


def best_match(title, candidates):
    """Return the index in candidates whose title best matches, or -1."""
    if not title:
        return -1
    # Exact match first
    for i, (_, t) in enumerate(candidates):
        if t == title:
            return i
    # Prefix / substring match (Chrome appends site names to tab titles)
    for i, (_, t) in enumerate(candidates):
        if t and (title.startswith(t) or t.startswith(title)):
            return i
    return -1


def main():
    ipc = i3ipc.Connection()

    # 1. Record existing Chrome windows.
    original = chrome_leaves(ipc)
    if not original:
        print("No Chrome windows found — nothing to do.", file=sys.stderr)
        sys.exit(1)

    saved = [(ws, title) for ws, _, title in original]
    print(f"Found {len(original)} Chrome window(s):")
    for ws, _, title in original:
        print(f"  workspace {ws}: {title!r}")

    # 2. Kill Chrome.
    subprocess.run(['pkill', CHROME_KILL], check=False)

    deadline = time.time() + KILL_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.5)
        if not chrome_leaves(ipc):
            break
    else:
        print("Chrome did not exit in time.", file=sys.stderr)
        sys.exit(1)

    print("Chrome exited. Launching…")

    # 3. Launch Chrome (session restore will reopen windows).
    subprocess.Popen(CHROME_LAUNCH)

    # 4. Wait until we have at least as many windows as before, with stable titles.
    deadline = time.time() + LAUNCH_TIMEOUT
    new_wins = []
    while time.time() < deadline:
        time.sleep(1)
        new_wins = chrome_leaves(ipc)
        if len(new_wins) >= len(original) and titles_settled(new_wins):
            break

    if not new_wins:
        print("Chrome did not reopen any windows in time.", file=sys.stderr)
        sys.exit(1)

    print(f"Restored {len(new_wins)} window(s):")
    for ws, _, title in new_wins:
        print(f"  workspace {ws}: {title!r}")

    # 5. Match each restored window to its saved workspace by title, then move.
    remaining_saved = list(enumerate(saved))   # [(orig_idx, (ws, title)), ...]
    assignments = {}                            # new_idx -> target_ws

    # First pass: title-based matching
    for new_idx, (_, con_id, new_title) in enumerate(new_wins):
        match = best_match(new_title, [(i, t) for i, (_, t) in remaining_saved])
        if match >= 0:
            _, (target_ws, _) = remaining_saved[match]
            assignments[new_idx] = target_ws
            remaining_saved.pop(match)

    # Second pass: assign unmatched windows to leftover workspaces in order
    unmatched_new = [i for i in range(len(new_wins)) if i not in assignments]
    for new_idx, (_, (target_ws, _)) in zip(unmatched_new, remaining_saved):
        assignments[new_idx] = target_ws

    # Apply moves
    for new_idx, (current_ws, con_id, title) in enumerate(new_wins):
        target_ws = assignments.get(new_idx)
        if target_ws is None:
            continue
        if current_ws != target_ws:
            ipc.command(f'[con_id={con_id}] move to workspace number {target_ws}')
            print(f"  {title!r}: workspace {current_ws} → {target_ws}")
        else:
            print(f"  {title!r}: already on workspace {target_ws}")

    print("Done.")


if __name__ == '__main__':
    main()
