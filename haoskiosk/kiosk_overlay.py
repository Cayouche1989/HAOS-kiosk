#!/usr/bin/env python3
"""X11 escape overlay, displayed only while Chromium is outside DeskOS."""

import asyncio
import os
import subprocess
import threading
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from browser_ctl import ChromiumController

ONBOARD_DESTINATION = "org.onboard.Onboard"
ONBOARD_PATH = "/org/onboard/Onboard/Keyboard"
ONBOARD_INTERFACE = "org.onboard.Onboard.Keyboard"
DESKOS_URL = os.getenv("DESKOS_URL", "http://127.0.0.1:4173/")
URL_POLL_SECONDS = 0.75
OVERLAY_WIDTH = 326
OVERLAY_HEIGHT = 64
OVERLAY_MARGIN = 10


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
        self._visible_for_page = None
        self.connect("realize", self._on_realize)
        self.connect("map-event", self._on_map)
        self.set_size_request(OVERLAY_WIDTH, OVERLAY_HEIGHT)
        # Home Assistant's navigation toggle lives at the top left.  Keep the
        # escape controls on the opposite side of the 1024x600 kiosk instead
        # of competing with the native HA controls.
        self.move(self._right_aligned_x(), OVERLAY_MARGIN)
        box = Gtk.Box(spacing=6, margin=6)
        self.add(box)
        for label, action in (("← DeskOS", self.deskos), ("⌨ Clavier", self.keyboard)):
            button = Gtk.Button.new_with_label(label)
            button.set_size_request(154, 52)
            button.connect("clicked", action)
            box.pack_start(button, True, True, 0)
        print("[overlay] overlay created", flush=True)

    def _right_aligned_x(self) -> int:
        """Place the fixed-size overlay inside the active X11 screen."""
        screen = self.get_screen()
        width = screen.get_width() if screen is not None else 1024
        return max(OVERLAY_MARGIN, width - OVERLAY_WIDTH - OVERLAY_MARGIN)

    @staticmethod
    def _is_deskos_url(url: str) -> bool:
        """Return true only for the configured DeskOS origin."""
        current = urlparse(url)
        deskos = urlparse(DESKOS_URL)
        return (
            current.scheme == deskos.scheme
            and current.hostname == deskos.hostname
            and current.port == deskos.port
        )

    def update_page_visibility(self, url: str) -> bool:
        """Run in the GTK thread; keep the existing popup instance alive."""
        should_show = not self._is_deskos_url(url)
        if should_show == self._visible_for_page:
            return False

        self._visible_for_page = should_show
        if should_show:
            self.show_all()
            print("[overlay] visible for non-DeskOS page", flush=True)
        else:
            self.hide()
            print("[overlay] hidden for DeskOS", flush=True)
        return False

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
        # Hide immediately: CDP will confirm the DeskOS URL shortly afterwards.
        self.update_page_visibility(DESKOS_URL)
        subprocess.Popen(
            ["python3", "/browser_ctl.py", "launch_url", os.getenv("DESKOS_URL", "http://127.0.0.1:4173/")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def keyboard(self, *_: object) -> None:
        set_onboard_visible(onboard_visible())


def monitor_page_visibility(window: Overlay) -> None:
    """Use Chromium CDP as the source of truth without recreating the popup."""
    last_url = None
    last_error = None
    while True:
        try:
            target = asyncio.run(ChromiumController().get_page_target())
            url = str(target.get("url") or "")
            if url and url != last_url:
                last_url = url
                GLib.idle_add(window.update_page_visibility, url)
            last_error = None
        except Exception as err:  # Keep the last known visibility during CDP restarts.
            message = str(err)
            if message != last_error:
                print(f"[overlay] CDP page check failed: {message}", flush=True)
                last_error = message
        threading.Event().wait(URL_POLL_SECONDS)


window = Overlay()
window.show_all()
# Never cover DeskOS while Chromium/CDP is still starting.  The monitor will
# show this same popup as soon as it observes Home Assistant (or another page).
window.hide()
threading.Thread(target=monitor_page_visibility, args=(window,), daemon=True).start()
Gtk.main()
