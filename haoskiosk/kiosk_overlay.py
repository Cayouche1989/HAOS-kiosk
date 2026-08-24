#!/usr/bin/env python3
"""Small, always-available X11 escape overlay for the touchscreen kiosk."""

import os
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

ONBOARD_DESTINATION = "org.onboard.Onboard"
ONBOARD_PATH = "/org/onboard/Onboard/Keyboard"
ONBOARD_INTERFACE = "org.onboard.Onboard.Keyboard"


def onboard_visible() -> bool:
    """Read Onboard's actual visibility instead of blindly toggling it."""
    result = subprocess.run(
        [
            "dbus-send", "--print-reply", "--dest=" + ONBOARD_DESTINATION,
            ONBOARD_PATH, "org.freedesktop.DBus.Properties.Get",
            "string:" + ONBOARD_INTERFACE, "string:Visible",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and "boolean true" in result.stdout.lower()


def set_onboard_visible(visible: bool) -> None:
    method = "Hide" if visible else "Show"
    subprocess.Popen(
        ["dbus-send", "--type=method_call", "--dest=" + ONBOARD_DESTINATION,
         ONBOARD_PATH, ONBOARD_INTERFACE + "." + method],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class Overlay(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_decorated(False)
        self.set_override_redirect(True)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_size_request(326, 64)
        self.move(10, 10)
        box = Gtk.Box(spacing=6, margin=6)
        self.add(box)
        for label, action in (("← DeskOS", self.deskos), ("⌨ Clavier", self.keyboard)):
            button = Gtk.Button.new_with_label(label)
            button.set_size_request(154, 52)
            button.connect("clicked", action)
            box.pack_start(button, True, True, 0)

    def deskos(self, *_: object) -> None:
        subprocess.Popen(
            ["python3", "/browser_ctl.py", "launch_url", os.getenv("DESKOS_URL", "http://127.0.0.1:4173/")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def keyboard(self, *_: object) -> None:
        set_onboard_visible(onboard_visible())


window = Overlay()
window.show_all()
Gtk.main()
