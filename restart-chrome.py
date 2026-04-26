#!/usr/bin/env python3
"""
restart-chrome.py — Restart all running browsers while preserving workspace assignments.

For each browser that currently has windows open:
  1. Record workspace + title for every window.
  2. For Edge: identify PWA windows (--app= in /proc cmdline) and record which
     sway_startup.conf entries need relaunching after Edge restarts.
  3. Kill the browser.
  4. Relaunch it (session restore reopens windows/tabs).
  5. Wait for the same number of windows with stable titles.
  6. Match restored windows to saved workspaces by title, then move.
  7. For Edge: relaunch any PWA entries from sway_startup.conf.
"""

import os
import subprocess
import sys
import time

import i3ipc

STARTUP_CONF = os.path.expanduser('~/.config/sway_startup.conf')

# Browser definitions — only browsers with open windows are restarted.
BROWSERS = [
    {
        'name':        'Edge',
        'classes':     {'Microsoft-edge'},
        'app_ids':     {'microsoft-edge'},
        'app_id_prefix': 'msedge-',   # PWA windows: msedge-_<app-id>-Default
        'kill':        'msedge',
        'launch':      [os.path.expanduser('~/bin/edge')],
        'restart_pwas': True,   # relaunch sway_startup.conf PWA entries after restart
    },
    {
        'name':    'Chrome',
        'classes': {'Google-chrome'},
        'app_ids': {'google-chrome'},
        'kill':    'chrome',
        'launch':  [os.path.expanduser('~/bin/chrome')],
        'restart_pwas': False,
    },
    {
        'name':    'Firefox',
        'classes': {'Firefox', 'firefox'},
        'app_ids': {'firefox', 'org.mozilla.firefox'},
        'kill':    'firefox',
        'launch':  ['firefox'],
        'restart_pwas': False,
    },
]

KILL_TIMEOUT   = 15   # seconds to wait for browser to exit
LAUNCH_TIMEOUT = 60   # seconds to wait for windows to reappear
PWA_TIMEOUT    = 30   # seconds to wait for each PWA window
POLL_INTERVAL  = 1.0
PWA_POLL       = 0.25

# als_tiling staging workspace — if windows are here, als_tiling is mid-arrangement.
# Don't start moving windows until they've left this workspace, otherwise als_tiling
# will move them back when it completes its own arrangement pass.
ALS_TEMP_WS = 50

# Titles that indicate a browser hasn't finished restoring yet
PENDING_TITLES = {'', 'New Tab', 'Restoring…', 'Restoring...'}


def browser_leaves(ipc, browser):
    """Return list of (ws_num, con_id, pid, title) for every non-floating window."""
    results = []
    classes = browser['classes']
    app_ids = browser['app_ids']
    for ws in ipc.get_tree().workspaces():
        for con in ws.leaves():
            match = (con.window_class in classes) or (con.app_id in app_ids)
            if match and con.floating not in ('auto_on', 'user_on'):
                results.append((ws.num, con.id, con.pid or 0, con.name or ''))
    return results




def find_pwa_entries():
    """Return (workspace, cmd) pairs marked pwa: in sway_startup.conf."""
    entries = []
    try:
        with open(STARTUP_CONF) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].lower().startswith('pwa:'):
                    ws = parts[0][4:]
                    entries.append((ws, parts[1]))
    except FileNotFoundError:
        pass
    return entries


def titles_settled(wins):
    return all(title not in PENDING_TITLES for _, _, _, title in wins)


def best_match(title, candidates):
    """Return index in candidates whose title best matches, or -1."""
    if not title:
        return -1
    for i, (_, t) in enumerate(candidates):
        if t == title:
            return i
    for i, (_, t) in enumerate(candidates):
        if t and (title.startswith(t) or t.startswith(title)):
            return i
    return -1


def all_win_ids(ipc):
    def collect(node, ids):
        if node.type == 'con' and not node.nodes and not node.floating_nodes and node.id:
            ids.add(node.id)
        for child in node.nodes + node.floating_nodes:
            collect(child, ids)
    ids = set()
    collect(ipc.get_tree(), ids)
    return ids


def browser_win_ids(ipc, browser):
    """Return set of con_ids for all non-floating windows belonging to this browser."""
    classes = browser['classes']
    app_ids = browser['app_ids']
    prefix = browser.get('app_id_prefix', '')
    ids = set()
    for ws in ipc.get_tree().workspaces():
        for con in ws.leaves():
            app_id = con.app_id or ''
            if (con.window_class in classes
                    or app_id in app_ids
                    or (prefix and app_id.startswith(prefix))):
                ids.add(con.id)
    return ids


def launch_pwa_entries(ipc, browser, pwa_entries):
    """Relaunch PWA entries from sway_startup.conf.

    Workspace placement is handled by sway assign rules (keyed on window instance),
    so we just need to launch each app and wait for its window to appear.
    """
    if not pwa_entries:
        return

    # Give Edge time to fully initialise before PWAs try to attach to its profile.
    print(f"\nWaiting for Edge to settle before launching {len(pwa_entries)} PWA app(s)…")
    time.sleep(3)

    # Ensure ~/bin is in PATH — sway's exec environment may not have it.
    env = os.environ.copy()
    env['PATH'] = os.path.expanduser('~/bin') + ':' + env.get('PATH', '')

    for _, cmd in pwa_entries:
        print(f"  launching {cmd!r}")
        before = browser_win_ids(ipc, browser)
        proc = subprocess.Popen(
            cmd, shell=True, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + PWA_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(PWA_POLL)
            if proc.poll() is not None:
                print(f"    process exited early (rc={proc.returncode})", file=sys.stderr)
                break
            if browser_win_ids(ipc, browser) - before:
                print(f"    window appeared")
                break
        else:
            print(f"    timeout: no window after {PWA_TIMEOUT}s for {cmd!r}", file=sys.stderr)


def restart_browser(ipc, browser):
    name = browser['name']

    original = browser_leaves(ipc, browser)
    if not original:
        return

    saved = [(ws, title) for ws, _, _, title in original]
    print(f"\n{name}: found {len(original)} window(s)")
    for ws, _, _, title in original:
        print(f"  workspace {ws}: {title!r}")

    # Identify PWA entries to relaunch (must be done before kill)
    pwa_entries = []
    if browser.get('restart_pwas'):
        print(f"\n{name}: scanning for PWA windows…")
        pwa_entries = find_pwa_entries()
        if pwa_entries:
            print(f"  will relaunch {len(pwa_entries)} PWA entry/entries after restart")

    # Kill
    subprocess.run(['pkill', browser['kill']], check=False)
    deadline = time.time() + KILL_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.5)
        if not browser_leaves(ipc, browser):
            break
    else:
        print(f"{name}: did not exit in time.", file=sys.stderr)
        return

    print(f"{name}: exited. Launching…")
    subprocess.Popen(browser['launch'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for main browser windows with stable titles
    # (PWA windows are relaunched separately, so only wait for non-PWA count)
    expected = len(original) - len(pwa_entries)
    if expected < 1:
        expected = 1

    deadline = time.time() + LAUNCH_TIMEOUT
    new_wins = []
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        new_wins = browser_leaves(ipc, browser)
        staging = any(ws == ALS_TEMP_WS for ws, _, _, _ in new_wins)
        if len(new_wins) >= expected and titles_settled(new_wins) and not staging:
            break

    if not new_wins:
        print(f"{name}: no windows reappeared in time.", file=sys.stderr)
    else:
        print(f"{name}: restored {len(new_wins)} window(s)")
        print(f"  saved:   {[(ws, t[:40]) for ws, t in saved]}")
        print(f"  new_wins:{[(ws, t[:40]) for ws, _, _, t in new_wins]}")

        remaining_saved = list(enumerate(saved))
        assignments = {}

        for new_idx, (_, con_id, _, new_title) in enumerate(new_wins):
            match = best_match(new_title, [(i, t) for i, (_, t) in remaining_saved])
            if match >= 0:
                _, (target_ws, _) = remaining_saved[match]
                assignments[new_idx] = target_ws
                remaining_saved.pop(match)

        unmatched_new = [i for i in range(len(new_wins)) if i not in assignments]
        for new_idx, (_, (target_ws, _)) in zip(unmatched_new, remaining_saved):
            assignments[new_idx] = target_ws

        print(f"  assignments: {assignments}")

        for new_idx, (current_ws, con_id, _, title) in enumerate(new_wins):
            target_ws = assignments.get(new_idx)
            if target_ws is None:
                print(f"  {title!r}: no assignment, leaving on workspace {current_ws}")
                continue
            if current_ws != target_ws:
                ipc.command(f'[con_id={con_id}] move to workspace number {target_ws}')
                print(f"  {title!r}: workspace {current_ws} → {target_ws}")
            else:
                print(f"  {title!r}: already on workspace {target_ws}")

    # Relaunch PWA apps
    launch_pwa_entries(ipc, browser, pwa_entries)


def main():
    ipc = i3ipc.Connection()

    running = [b for b in BROWSERS if browser_leaves(ipc, b)]
    if not running:
        print("No supported browser windows found — nothing to do.", file=sys.stderr)
        sys.exit(1)

    for browser in running:
        restart_browser(ipc, browser)

    print("\nDone.")


if __name__ == '__main__':
    main()
