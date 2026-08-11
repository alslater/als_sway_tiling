#!/usr/bin/env python3
"""Regression tests for Edge/Chromium tooltip surfaces corrupting the layout.

Hovering a calendar entry in the Outlook PWA maps a short-lived xdg_shell
surface with an empty app_id and no title (observed: ~20ms lifetime,
app_id="", name=None, type=con, floating=auto_off).  Because it is a
non-floating leaf con, get_leaves() counted it as a real tileable window,
so the grid was rebuilt for N+1 windows and then again for N.
"""

import unittest

import als_tiling


class FakeCon:
    """Minimal stand-in for i3ipc.Con as used by get_leaves()."""

    def __init__(self, id=1, app_id='firefox', name='a window',
                 type='con', nodes=None):
        self.id = id
        self.app_id = app_id
        self.name = name
        self.type = type
        self.nodes = nodes or []


def tooltip(id=165):
    """A surface matching the observed Edge tooltip exactly."""
    return FakeCon(id=id, app_id='', name=None)


class TestTooltipExcluded(unittest.TestCase):

    def test_real_windows_are_counted(self):
        ws = FakeCon(id=1, type='workspace', nodes=[
            FakeCon(id=10, app_id='msedge-_eoficlgic-Default', name='Outlook'),
            FakeCon(id=11, app_id='code', name='sway'),
            FakeCon(id=12, app_id='mate-terminal', name='Terminal'),
        ])
        self.assertEqual(len(als_tiling.get_leaves(ws)), 3)

    def test_tooltip_is_not_counted(self):
        """The bug: tooltip inflates the count 3 -> 4 and triggers a rebuild."""
        ws = FakeCon(id=1, type='workspace', nodes=[
            FakeCon(id=10, app_id='msedge-_eoficlgic-Default', name='Outlook'),
            FakeCon(id=11, app_id='code', name='sway'),
            FakeCon(id=12, app_id='mate-terminal', name='Terminal'),
            tooltip(),
        ])
        self.assertEqual(len(als_tiling.get_leaves(ws)), 3)

    def test_untitled_real_window_still_counted(self):
        """A window that has an app_id but no title yet must NOT be filtered."""
        ws = FakeCon(id=1, type='workspace', nodes=[
            FakeCon(id=10, app_id='code', name='sway'),
            FakeCon(id=11, app_id='org.gnome.Nautilus', name=None),
        ])
        self.assertEqual(len(als_tiling.get_leaves(ws)), 2)

    def test_xwayland_window_still_counted(self):
        """XWayland cons report app_id=None, not ''. Must not be filtered."""
        ws = FakeCon(id=1, type='workspace', nodes=[
            FakeCon(id=10, app_id=None, name='VirtualBox Machine'),
            FakeCon(id=11, app_id='code', name='sway'),
        ])
        self.assertEqual(len(als_tiling.get_leaves(ws)), 2)

    def test_hover_does_not_change_count(self):
        """End-to-end: the count must be stable across the tooltip's lifetime."""
        real = [
            FakeCon(id=10, app_id='msedge-_eoficlgic-Default', name='Outlook'),
            FakeCon(id=11, app_id='code', name='sway'),
            FakeCon(id=12, app_id='mate-terminal', name='Terminal'),
        ]
        before = len(als_tiling.get_leaves(FakeCon(id=1, type='workspace', nodes=list(real))))
        during = len(als_tiling.get_leaves(FakeCon(id=1, type='workspace', nodes=real + [tooltip()])))
        after = len(als_tiling.get_leaves(FakeCon(id=1, type='workspace', nodes=list(real))))
        self.assertEqual((before, during, after), (3, 3, 3))


if __name__ == '__main__':
    unittest.main(verbosity=2)
