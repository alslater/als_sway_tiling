#!/usr/bin/env python3
"""
sway_startup.py — Launch apps on specific sway workspaces from a config file.

Config file: ~/.config/sway_startup.conf

Format:
    # comment
    <workspace>  <command>

workspace : 1–9 or 'scratchpad'
command   : any shell command (run via sh -c)

Example:
    1           edge
    1           outlook
    1           teams_owa
    1           opsgenie
    scratchpad  telegram-desktop
"""

import i3ipc
import subprocess
import sys
import time
import os

CONFIG  = os.path.expanduser('~/.config/sway_startup.conf')
TIMEOUT = 30    # seconds to wait for each app's window before giving up
POLL    = 0.25  # seconds between tree polls


def notify(summary, body='', urgency='normal'):
    subprocess.Popen(
        ['notify-send', '-a', 'sway_startup', '-u', urgency, summary, body],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def parse_config(path):
    entries = []
    try:
        with open(path) as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    notify('sway_startup: bad config line',
                           f'Line {lineno}: expected "<workspace> <command>"',
                           urgency='critical')
                    continue
                workspace, cmd = parts
                ws = workspace.lower()
                # Normalise pwa:<N> → <N> (the pwa: prefix is used by restart-chrome.py)
                if ws.startswith('pwa:'):
                    ws = ws[4:]
                if ws not in ('scratchpad',) and not ws.isdigit():
                    notify('sway_startup: bad workspace',
                           f'Line {lineno}: unknown workspace {workspace!r}',
                           urgency='critical')
                    continue
                entries.append((ws, cmd))
    except FileNotFoundError:
        notify('sway_startup: config not found', path, urgency='critical')
        sys.exit(1)
    return entries


def all_win_ids(ipc):
    """Return the set of all leaf window con_ids across the entire tree."""
    def collect(node, ids):
        if node.type == 'con' and not node.nodes and not node.floating_nodes and node.id:
            ids.add(node.id)
        for child in node.nodes + node.floating_nodes:
            collect(child, ids)
    ids = set()
    collect(ipc.get_tree(), ids)
    return ids


def wait_for_new_window(ipc, before, timeout):
    """Poll until a window con_id appears that wasn't in *before*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(POLL)
        new = all_win_ids(ipc) - before
        if new:
            return next(iter(new))
    return None


def move_to(ipc, con_id, workspace):
    if workspace == 'scratchpad':
        ipc.command(f'[con_id={con_id}] move scratchpad')
    else:
        ipc.command(f'[con_id={con_id}] move to workspace number {workspace}')


def main():
    entries = parse_config(CONFIG)
    if not entries:
        notify('sway_startup', 'No entries in config — nothing to do.')
        return

    try:
        ipc = i3ipc.Connection()
    except Exception as e:
        notify('sway_startup: cannot connect to sway', str(e), urgency='critical')
        sys.exit(1)

    for workspace, cmd in entries:
        before = all_win_ids(ipc)

        subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        con_id = wait_for_new_window(ipc, before, TIMEOUT)
        if con_id:
            move_to(ipc, con_id, workspace)
        else:
            notify('sway_startup: timeout',
                   f'{cmd!r} produced no window after {TIMEOUT}s',
                   urgency='low')


if __name__ == '__main__':
    main()
