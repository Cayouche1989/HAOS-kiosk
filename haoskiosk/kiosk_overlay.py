#!/usr/bin/env python3
import os, subprocess, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
class Overlay(Gtk.Window):
    def __init__(self):
        super().__init__(); self.set_decorated(False); self.set_keep_above(True); self.set_type_hint(Gdk.WindowTypeHint.DOCK); self.set_size_request(300,58); self.move(12,12)
        box=Gtk.Box(spacing=6, margin=6); self.add(box)
        for label, action in (("← DeskOS", self.deskos), ("⌨ Clavier", self.keyboard)):
            b=Gtk.Button.new_with_label(label); b.set_size_request(140,46); b.connect("clicked", action); box.pack_start(b,True,True,0)
    def deskos(self,*_): subprocess.Popen(["python3","/browser_ctl.py","launch_url",os.getenv("DESKOS_URL","http://127.0.0.1:4173/")])
    def keyboard(self,*_): subprocess.Popen(["dbus-send","--type=method_call","--dest=org.onboard.Onboard","/org/onboard/Onboard/Keyboard","org.onboard.Onboard.Keyboard.ToggleVisible"])
w=Overlay(); w.show_all(); Gtk.main()
