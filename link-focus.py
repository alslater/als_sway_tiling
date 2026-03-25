#!/usr/bin/env python3
"""
link-focus.py — When a browser window becomes urgent (e.g. a PWA link click
opens a URL in the main Edge window), switch to its workspace and focus it.

This replicates the awesomewm behaviour where clicking a link in a PWA would
raise the main browser window.
"""

import i3ipc

# XWayland window classes for Edge / Chrome / Chromium
BROWSER_CLASSES = {
    'Microsoft-edge',
    'Google-chrome',
    'Chromium',
    'chromium',
}

# Wayland app_ids (native Wayland / ozone builds)
BROWSER_APP_IDS = {
    'microsoft-edge',
    'google-chrome',
    'chromium',
}


def on_window_urgent(ipc, event):
    if not event.container.urgent:
        return

    con = event.container
    app_class = con.window_class or ''
    app_id = con.app_id or ''

    if app_class not in BROWSER_CLASSES and app_id not in BROWSER_APP_IDS:
        return

    ipc.command(f'[con_id={con.id}] focus')


ipc = i3ipc.Connection()
ipc.on('window::urgent', on_window_urgent)
ipc.main()
