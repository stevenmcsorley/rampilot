"""Capture real app windows on an isolated X11 display; never closes processes.

Run: PYTHONPATH=src xvfb-run -a -s '-screen 0 1440x1400x24' \
     /usr/bin/python3 tools/capture_screenshots.py
Requires xvfb, xauth and imagemagick in addition to application dependencies.
"""
from pathlib import Path
import subprocess
import traceback

import gi
gi.require_version("GdkX11", "4.0")
from rampilot.app import Application, Dashboard
from rampilot.resources import install_resources
from gi.repository import Adw, GdkX11, Gio, GLib, Gtk

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "screenshots"
OUTPUT.mkdir(parents=True, exist_ok=True)
class ScreenshotApplication(Application):
    def do_activate(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        install_resources()
        self.window = Dashboard(self)
        self.window.set_default_size(1040, 1200)
        self.window.present()


app = ScreenshotApplication()
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
errors = []


def capture(window, name):
    xid = window.get_surface().get_xid()
    subprocess.run(["import", "-window", str(xid), str(OUTPUT / name)], check=True)


def later(callback, delay=1500):
    def wrapped():
        try:
            callback()
        except Exception:
            errors.append(traceback.format_exc())
            app.quit()
        return False
    GLib.timeout_add(delay, wrapped)


def dashboard():
    capture(app.window, "dashboard.png")
    app.window.open_cleanup()
    later(cleanup)


def cleanup():
    capture(app.window.cleanup, "cleanup.png")
    app.window.cleanup.close()
    app.window.set_visible(False)
    app.window.toggle_compact(None)
    app.window.set_default_size(480, 800)
    app.window.present()
    later(compact)


def compact():
    capture(app.window, "compact.png")
    app.window.show_settings()
    window = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == "RAMPilot Settings")
    window.set_visible(False)
    window.set_default_size(560, 720)
    window.present()
    later(settings)


def settings():
    window = next(w for w in Gtk.Window.list_toplevels() if w.get_title() == "RAMPilot Settings")
    capture(window, "settings.png")
    app.quit()


def prepare():
    app.window.set_default_size(1040, 1200)
    later(dashboard, 12000)


later(prepare, 1000)
app.run(None)
if errors:
    raise RuntimeError("\n".join(errors))
print(f"Screenshots saved to {OUTPUT}")
