import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import psutil
from rampilot.core.meminfo import read_meminfo
from rampilot.core.history import HistoryBuffer
from rampilot.core.processes import ProcessCollector
from rampilot.core.terminator import SafeTerminator, TerminationTarget


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.terminator = SafeTerminator()

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdout.close()

    def child(self, ignore=False):
        script = "import signal,time; " + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore else "") + "x=bytearray(32*1024*1024); print('ready',flush=True); time.sleep(90)"
        child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        self.children.append(child)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        return child, TerminationTarget(child.pid, "Disposable test", psutil.Process(child.pid).create_time())

    def test_real_memory(self):
        raw = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()}
        m = read_meminfo()
        self.assertEqual(m.total, raw['MemTotal'])
        self.assertLess(abs(m.available - raw['MemAvailable']), 128 * 1024 * 1024)
        self.assertEqual(m.used, m.total - m.available)
        self.assertEqual(m.swap_used, m.swap_total - m.swap_free)

    def test_process_rss(self):
        child, _ = self.child()
        result = ProcessCollector().scan()
        info = next(p for p in result.processes if p.pid == child.pid)
        self.assertTrue(info.cleanup_eligible)
        statm_rss = int(Path(f'/proc/{child.pid}/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
        self.assertLess(abs(info.rss - statm_rss), 4 * 1024 * 1024)

    def test_graceful(self):
        child, target = self.child()
        self.assertEqual(self.terminator.terminate(target).outcome, 'sent')
        child.wait(timeout=3)
        self.assertEqual(child.returncode, -15)

    def test_force_separate(self):
        child, target = self.child(ignore=True)
        self.assertEqual(self.terminator.terminate(target).outcome, 'sent')
        time.sleep(.1)
        self.assertIsNone(child.poll())
        self.assertEqual(self.terminator.terminate(target, force=True).outcome, 'sent')
        child.wait(timeout=3)
        self.assertEqual(child.returncode, -9)

    def test_identity_mismatch(self):
        child, target = self.child()
        wrong = TerminationTarget(target.pid, target.name, target.create_time + .01)
        self.assertEqual(self.terminator.terminate(wrong).outcome, 'refused')
        self.assertIsNone(child.poll())

    def test_disappeared(self):
        child, target = self.child()
        child.terminate()
        child.wait()
        self.assertEqual(self.terminator.terminate(target).outcome, 'exited')

    def test_protection(self):
        for pid in (1, os.getpid(), os.getppid()):
            target = TerminationTarget(pid, 'Protected', psutil.Process(pid).create_time())
            self.assertEqual(self.terminator.terminate(target).outcome, 'refused')

    def test_permission_error(self):
        child, target = self.child()
        with patch.object(self.terminator, '_send_via_pidfd', side_effect=PermissionError):
            self.assertEqual(self.terminator.terminate(target).outcome, 'refused')
        self.assertIsNone(child.poll())

    def test_no_unsafe_fallback(self):
        child, target = self.child()
        with patch.object(self.terminator, '_open_pidfd', return_value=None):
            self.assertEqual(self.terminator.terminate(target).outcome, 'error')
        self.assertIsNone(child.poll())

    def test_history_bounded(self):
        history = HistoryBuffer(max_points=100)
        for i in range(10000):
            history.add(.5, i / 10)
        self.assertLessEqual(len(history), 100)
        self.assertLessEqual(history.latest.time - history.points[0].time, 60)


if __name__ == '__main__':
    unittest.main()
