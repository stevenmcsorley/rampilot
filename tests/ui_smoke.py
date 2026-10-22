"""Exercise real GTK windows and both confirmations using a disposable child only."""
import subprocess
import sys
import time
import traceback
from gi.repository import Gio, GLib
from rampilot.app import Application
from rampilot.core.processes import ProcessCollector

child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); x=bytearray(48*1024*1024); print("ready",flush=True); time.sleep(60)'], stdout=subprocess.PIPE, text=True)
assert child.stdout.readline().strip() == 'ready'
app = Application()
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
stage = 0
deadline = time.monotonic() + 25
errors = []

def respond(dialog, response):
    dialog.emit('response', response)
    dialog.close()

def tick():
    global stage
    try:
        assert time.monotonic() < deadline, 'UI smoke test timed out'
        w = app.window
        if w is None or not w.processes:
            return True
        if stage == 0:
            w.show_settings()
            w.toggle_compact(None)
            w.toggle_compact(None)
            w.toggle_pause(None)
            w.toggle_pause(None)
            # Supply only the disposable process to the test cleanup panel.
            w.processes = tuple(p for p in ProcessCollector().scan().processes if p.pid == child.pid)
            w.open_cleanup()
            c = w.cleanup
            assert len(c._candidates) == 1
            c._candidates[0].check.set_active(True)
            c._on_close_clicked(None)
            assert child.poll() is None
            respond(c._alert, 'cancel')
            assert not c._busy and child.poll() is None
            c._on_close_clicked(None)
            respond(c._alert, 'close')
            stage = 1
        elif stage == 1 and not w.cleanup._busy:
            c = w.cleanup
            assert c._survivors and child.poll() is None
            assert c._available_before is not None
            c._on_force_clicked(None)
            assert not c._alert.get_response_enabled('force')
            box = c._alert.get_extra_child()
            box.get_first_child().set_active(True)
            assert c._alert.get_response_enabled('force')
            respond(c._alert, 'force')
            stage = 2
        elif stage == 2 and not w.cleanup._busy:
            assert child.poll() == -9
            assert not w.cleanup._survivors
            assert w.cleanup._stack.get_visible_child_name() == 'result'
            w.cleanup.close()
            w.open_cleanup()
            assert w.cleanup is not None
            print('PASS: dashboard, settings, compact, pause, cancel, SIGTERM, force warning, SIGKILL, measured result, reopen', flush=True)
            app.quit()
            return False
    except Exception:
        errors.append(traceback.format_exc())
        app.quit()
        return False
    return True

GLib.timeout_add(200, tick)
try:
    app.run(None)
finally:
    if child.poll() is None:
        child.kill()
    child.wait()
if errors:
    raise RuntimeError('\n'.join(errors))
