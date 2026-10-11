"""pbar and its hub, against a real unix socket."""
import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

PBAR = os.path.join(os.path.dirname(__file__), "..", "bin", "pbar")
loader = importlib.machinery.SourceFileLoader("pbar", PBAR)
spec = importlib.util.spec_from_loader("pbar", loader)
pbar = importlib.util.module_from_spec(spec)
loader.exec_module(pbar)


class Hub:
    """A hub on a socket in a short path, its output read as the mod reads it."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="pb", dir="/tmp")
        self.sock = os.path.join(self.dir, "h.sock")
        self.proc = subprocess.Popen([sys.executable, PBAR, "hub", "--sock", self.sock],
                                     stdout=subprocess.PIPE, text=True)
        self.ready = json.loads(self.proc.stdout.readline())
        return self

    def lines(self, n):
        return [json.loads(self.proc.stdout.readline()) for _ in range(n)]

    def pbar(self, *argv, **kw):
        env = {**os.environ, "PROGRESS_BAR_SOCK": self.sock}
        return subprocess.run([sys.executable, PBAR, *argv], env=env, capture_output=True, text=True, **kw)

    def __exit__(self, *_):
        self.proc.terminate()
        self.proc.wait(5)
        self.proc.stdout.close()


class Parsing(unittest.TestCase):
    def test_values(self):
        self.assertEqual(pbar.parse_value("37", None), {"percent": 37.0})
        self.assertEqual(pbar.parse_value("37%", 240), {"percent": 37.0})
        self.assertEqual(pbar.parse_value("37", 240), {"done": 37, "total": 240})
        self.assertEqual(pbar.parse_value("118/240", None), {"done": 118, "total": 240})
        with self.assertRaises(SystemExit):
            pbar.parse_value("lots", None)

    def tracker(self, total=None, match=None):
        return pbar.Tracker(pbar.Wire(), "t", "t", total, match)

    def test_counts_every_line_with_a_total(self):
        t = self.tracker(total=10)
        t.feed(b"one\ntwo\r\nthree\n")
        self.assertEqual(t.done, 3)
        self.assertEqual(t.step, "three")

    def test_counts_only_matching_lines(self):
        t = self.tracker(total=10, match="saved")
        t.feed(b"saved a\nnoise\nsaved b\n")
        self.assertEqual((t.done, t.step), (2, "saved b"))

    def test_a_line_split_across_chunks_is_one_line(self):
        t = self.tracker(total=10)
        t.feed(b"pa")
        t.feed(b"ge 1\n")
        self.assertEqual((t.done, t.step), (1, "page 1"))

    def test_follows_fractions_and_percentages_in_the_output(self):
        t = self.tracker()
        t.feed(b"\x1b[32m[118/240]\x1b[0m shot\n")
        self.assertEqual((t.done, t.total), (118, 240))
        t = self.tracker()
        t.feed(b"downloading 43.5%\r")
        t.feed(b"downloading 61%\r")
        self.assertEqual(t.percent, 61.0)

    def test_dates_and_versions_are_not_progress(self):
        t = self.tracker()
        for line in (b"2026-10-11 12:00:00 start\n", b"on 10/11/2026 at 12:30\n", b"node v22.1.0\n", b"port 8080\n"):
            t.feed(line)
        self.assertEqual((t.done, t.total, t.percent), (None, None, None))

    def test_a_fraction_cannot_pass_its_total(self):
        t = self.tracker()
        t.feed(b"7/3 odd\n")
        self.assertEqual((t.done, t.total), (None, None))

    def test_with_nothing_to_measure_any_output_is_a_sign_of_life(self):
        t = self.tracker()
        t.moved_at = 0
        t.feed(b"still thinking\n")
        self.assertGreater(t.moved_at, 0)

    def test_output_that_moves_nothing_is_not_progress_when_there_is_a_bar(self):
        t = self.tracker(total=10, match="saved")
        t.moved_at = 123.0
        t.feed(b"noise\n")
        self.assertEqual(t.moved_at, 123.0)


class Reporting(unittest.TestCase):
    def test_the_hub_says_ready_and_relays_json_lines(self):
        with Hub() as hub:
            self.assertEqual(hub.ready["hub"], "ready")
            self.assertTrue(os.stat(hub.sock).st_mode & 0o777 == 0o600)
            s = socket.socket(socket.AF_UNIX)
            s.connect(hub.sock)
            s.sendall(b'{"id":"a","op":"set","percent":5}\nnot json\n[1]\n')
            time.sleep(0.2)
            reply = s.recv(200)
            s.close()
            self.assertEqual(hub.lines(1), [{"id": "a", "op": "set", "percent": 5}])
            self.assertEqual(reply.count(b"ok"), 1)
            self.assertEqual(reply.count(b"error"), 2)

    def test_manual_commands(self):
        with Hub() as hub:
            hub.pbar("start", "build", "--label", "Build", "--total", "10")
            hub.pbar("set", "build", "4/10", "--step", "linking")
            hub.pbar("tick", "build", "2")
            hub.pbar("done", "build", "--msg", "all green")
            start, setm, tick, done = hub.lines(4)
            self.assertEqual((start["op"], start["label"], start["total"]), ("start", "Build", 10))
            self.assertEqual((setm["done"], setm["total"], setm["step"]), (4, 10, "linking"))
            self.assertEqual((tick["op"], tick["inc"]), ("set", 2))
            self.assertEqual((done["op"], done["msg"]), ("done", "all green"))
            # The shell that ran pbar is gone a moment later, so it is not what "alive" means.
            self.assertTrue(all("pid" not in m for m in (start, setm, tick, done)))

    def test_run_passes_output_and_the_exit_code_through_and_reports(self):
        with Hub() as hub:
            r = hub.pbar("run", "--label", "shots", "--total", "3", "--match", "page", "--",
                         sys.executable, "-c", "print('page 1'); print('noise'); print('page 2'); import sys; print('oops', file=sys.stderr); sys.exit(3)")
            self.assertEqual(r.returncode, 3)
            self.assertEqual(r.stdout.split(), ["page", "1", "noise", "page", "2"])
            self.assertEqual(r.stderr.strip(), "oops")
            time.sleep(0.2)
            hub.proc.terminate()
            out = [json.loads(l) for l in hub.proc.stdout.read().splitlines()]
            ops = [m["op"] for m in out]
            self.assertEqual(ops[0], "start")
            self.assertEqual(ops[-1], "fail")
            self.assertEqual(out[-1]["msg"], "exit 3")
            self.assertEqual((out[-1]["done"], out[-1]["total"]), (2, 3))
            self.assertEqual(len({m["pid"] for m in out}), 1)

    def test_a_successful_run_ends_done(self):
        with Hub() as hub:
            r = hub.pbar("run", "--id", "ok", "--", sys.executable, "-c", "print('42%')")
            self.assertEqual(r.returncode, 0)
            time.sleep(0.2)
            hub.proc.terminate()
            out = [json.loads(l) for l in hub.proc.stdout.read().splitlines()]
            self.assertEqual(out[-1]["op"], "done")
            self.assertEqual(out[-1]["percent"], 42.0)

    def test_pipe_passes_stdin_through(self):
        with Hub() as hub:
            r = hub.pbar("pipe", "--label", "p", "--total", "2", input="a\nb\n")
            self.assertEqual(r.stdout, "a\nb\n")
            time.sleep(0.2)
            hub.proc.terminate()
            out = [json.loads(l) for l in hub.proc.stdout.read().splitlines()]
            self.assertEqual((out[-1]["op"], out[-1]["done"]), ("done", 2))

    def test_a_missing_bar_never_breaks_the_command(self):
        env = {k: v for k, v in os.environ.items() if k != "PROGRESS_BAR_SOCK"}
        env["TMPDIR"] = tempfile.mkdtemp()
        r = subprocess.run([sys.executable, PBAR, "run", "--", sys.executable, "-c", "print('hi'); raise SystemExit(5)"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 5)
        self.assertEqual(r.stdout.strip(), "hi")
        self.assertEqual(r.stderr.count("no progress bar found"), 1)

    def test_a_command_that_cannot_start_is_a_127(self):
        with Hub() as hub:
            r = hub.pbar("run", "--", "definitely-not-a-command-xyz")
            self.assertEqual(r.returncode, 127)

    def test_the_hub_leaves_no_socket_behind(self):
        with Hub() as hub:
            sock = hub.sock
            hub.proc.terminate()
            hub.proc.wait(5)
            self.assertFalse(os.path.exists(sock))


if __name__ == "__main__":
    unittest.main()
