#!/usr/bin/env python3
"""
focus-guard: hold keyboard focus on modal dialogs that need user input.

When a modal app (e.g. gcr-prompter) appears:
  - disables focus_follows_mouse so the mouse can't drag focus away
  - focuses the dialog after a short delay (window may not be ready immediately)
  - watches for focus theft and refocuses the dialog

When the last such dialog closes:
  - restores focus_follows_mouse
"""

import i3ipc
import threading

MODAL_APP_IDS = {'gcr-prompter'}
FOCUS_DELAY = 0.25   # seconds to wait before focusing (allow window to map fully)
REFOCUS_DELAY = 0.1  # seconds to wait before refocusing after theft

_modal_windows = {}  # con_id -> app_id
_lock = threading.Lock()
_refocus_timer = None


def focus_modal(ipc):
    """Focus the most recently opened modal window."""
    with _lock:
        if not _modal_windows:
            return
        con_id = next(reversed(_modal_windows))
    ipc.command(f'[con_id={con_id}] focus')


def on_window(ipc, event):
    global _refocus_timer
    con_id = event.container.id

    if event.change == 'new':
        app_id = getattr(event.container, 'app_id', None) or ''
        if app_id in MODAL_APP_IDS:
            with _lock:
                was_empty = not _modal_windows
                _modal_windows[con_id] = app_id
            if was_empty:
                ipc.command('focus_follows_mouse no')
            # Delay focus so the window has time to fully map
            threading.Timer(FOCUS_DELAY, focus_modal, args=[ipc]).start()

    elif event.change == 'close':
        with _lock:
            if con_id not in _modal_windows:
                return
            del _modal_windows[con_id]
            empty = not _modal_windows
        if empty:
            ipc.command('focus_follows_mouse yes')

    elif event.change == 'focus':
        # If something stole focus while a modal is open, refocus the modal.
        # Guard: don't refocus if the focused window IS the modal.
        with _lock:
            if not _modal_windows:
                return
            if con_id in _modal_windows:
                return  # modal got focus — good
        # Something else got focus; schedule a refocus
        if _refocus_timer:
            _refocus_timer.cancel()
        _refocus_timer = threading.Timer(REFOCUS_DELAY, focus_modal, args=[ipc])
        _refocus_timer.start()


if __name__ == '__main__':
    conn = i3ipc.Connection()
    conn.on(i3ipc.Event.WINDOW, on_window)
    print('focus-guard: watching for modal dialogs...', flush=True)
    conn.main()
