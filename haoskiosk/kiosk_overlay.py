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
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.connect("realize", self._on_realize)
        self.connect("map-event", self._on_map)
        self.set_size_request(326, 64)
        self.move(10, 10)
        box = Gtk.Box(spacing=6, margin=6)
        self.add(box)
        for label, action in (("← DeskOS", self.deskos), ("⌨ Clavier", self.keyboard)):
            button = Gtk.Button.new_with_label(label)
            button.set_size_request(154, 52)
            button.connect("clicked", action)
            box.pack_start(button, True, True, 0)
        print("[overlay] overlay created", flush=True)

    def _on_realize(self, *_: object) -> None:
        """Apply the X11 override flag only after Gtk has created Gdk.Window."""
        print("[overlay] overlay realized", flush=True)
        gdk_window = self.get_window()
        if gdk_window is None:
            print("[overlay] Gdk.Window unavailable", flush=True)
            return

        print("[overlay] Gdk.Window acquired", flush=True)
        try:
            gdk_window.set_override_redirect(True)
        except (AttributeError, TypeError) as err:
            print(f"[overlay] override_redirect unavailable: {err}", flush=True)
        else:
            print("[overlay] override_redirect applied", flush=True)

    def _on_map(self, *_: object) -> bool:
        print("[overlay] overlay mapped", flush=True)
        print("[overlay] overlay visible", flush=True)
        return False

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
