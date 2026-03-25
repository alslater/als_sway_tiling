#!/usr/bin/env python3
"""
teams-idle-inhibit.py — Inhibit Wayland idle while a Teams (msedge) call is active.

Detects call state by watching for an msedge PulseAudio source-output
(microphone capture stream, media.name="RecordStream"). Holds a Wayland
zwp_idle_inhibit_manager_v1 inhibitor for the duration of the call.
"""

import os
import subprocess
import threading
import time

STATE_FILE = '/tmp/teams-in-call'

import pulsectl
from pywayland.client import Display
from pywayland.protocol.wayland import WlCompositor
from pywayland.protocol.idle_inhibit_unstable_v1 import ZwpIdleInhibitManagerV1

POLL_SECS = 5  # how often to re-check when no event fires


def is_in_call(pulse):
    """Return True if msedge has an active microphone capture stream."""
    for so in pulse.source_output_list():
        binary = so.proplist.get('application.process.binary', '')
        media  = so.proplist.get('media.name', '')
        if binary == 'msedge' and media == 'RecordStream':
            return True
    return False


class IdleInhibitor:
    """Holds a Wayland idle inhibitor for as long as it is alive."""

    def __init__(self):
        self._display    = None
        self._surface    = None
        self._inhibitor  = None
        self._compositor = None
        self._manager    = None
        self._thread     = None
        self._stop       = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return  # already inhibiting
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("Idle inhibitor: started")

    def stop(self):
        if not (self._thread and self._thread.is_alive()):
            return
        self._stop.set()
        self._thread.join(timeout=3)
        print("Idle inhibitor: stopped")

    def _run(self):
        display = Display()
        display.connect()

        compositor = None
        manager    = None

        def registry_global(registry, name, interface, version):
            nonlocal compositor, manager
            if interface == 'wl_compositor':
                compositor = registry.bind(name, WlCompositor, version)
            elif interface == 'zwp_idle_inhibit_manager_v1':
                manager = registry.bind(name, ZwpIdleInhibitManagerV1, version)

        registry = display.get_registry()
        registry.dispatcher['global'] = registry_global
        display.roundtrip()
        display.roundtrip()

        if compositor is None or manager is None:
            print("Idle inhibitor: compositor or idle_inhibit_manager not available", flush=True)
            display.disconnect()
            return

        surface   = compositor.create_surface()
        inhibitor = manager.create_inhibitor(surface)
        display.roundtrip()

        # Hold until stop() is called
        while not self._stop.is_set():
            display.flush()
            time.sleep(0.2)

        inhibitor.destroy()
        surface.destroy()
        display.roundtrip()
        display.disconnect()


def main():
    inhibitor  = IdleInhibitor()
    in_call    = False

    with pulsectl.Pulse('teams-idle-inhibit') as pulse:
        def event_handler(ev):
            # Wake the poll loop on any source-output change
            raise pulsectl.PulseLoopStop

        pulse.event_mask_set('source_output')
        pulse.event_callback_set(event_handler)

        while True:
            now_in_call = is_in_call(pulse)
            if now_in_call and not in_call:
                print("Teams call started — inhibiting idle", flush=True)
                inhibitor.start()
                open(STATE_FILE, 'w').close()
                subprocess.run(['pkill', '-SIGRTMIN+8', 'waybar'], check=False)
                in_call = True
            elif not now_in_call and in_call:
                print("Teams call ended — releasing idle inhibitor", flush=True)
                inhibitor.stop()
                try:
                    os.remove(STATE_FILE)
                except FileNotFoundError:
                    pass
                subprocess.run(['pkill', '-SIGRTMIN+8', 'waybar'], check=False)
                in_call = False

            try:
                pulse.event_listen(timeout=POLL_SECS)
            except pulsectl.PulseLoopStop:
                pass


if __name__ == '__main__':
    main()
