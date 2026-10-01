"""Tests for usage_guard.py. Run: python3 -m unittest discover -s tests -v"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "plugins", "usage-guard", "scripts", "usage_guard.py")

spec = importlib.util.spec_from_file_location("usage_guard", SCRIPT)
ug = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ug)

CFG = dict(ug.DEFAULTS)


def load(path):
    with open(path) as f:
        return json.load(f)


def read(path):
    with open(path) as f:
        return f.read()


def assessment(pct, resets_in_min=120, rate=None, name="five_hour", now=1000000.0):
    state = {"windows": {name: {"pct": pct, "resets_at": now + resets_in_min * 60, "t": now, "src": "statusline"}},
             "samples": {name: []}}
    if rate:
        state["samples"][name] = [[now - 600, pct - rate * 10], [now, pct]]
    return ug.assess(state, CFG, now)


class Parsing(unittest.TestCase):
    def test_statusline_shape(self):
        w = ug.extract_windows({"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
                                "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}})
        self.assertEqual(w["five_hour"], (23.5, 1738425600.0))
        self.assertEqual(w["seven_day"][0], 41.2)

    def test_api_shape(self):
        w = ug.extract_windows({
            "five_hour": {"utilization": 6.0, "resets_at": "2025-11-04T04:59:59.943648+00:00"},
            "seven_day_opus": {"utilization": 0.0, "resets_at": None},
            "seven_day_oauth_apps": None,
            "extra_usage": {"is_enabled": True, "utilization": 12.0},
        })
        self.assertEqual(set(w), {"five_hour", "seven_day_opus"})
        self.assertAlmostEqual(w["five_hour"][1], 1762232399.0, places=0)
        self.assertIsNone(w["seven_day_opus"][1])

    def test_parse_ts(self):
        self.assertEqual(ug.parse_ts(1738425600000), 1738425600.0)
        self.assertAlmostEqual(ug.parse_ts("2025-11-04T04:59:59Z"), 1762232399.0)
        self.assertIsNone(ug.parse_ts("garbage"))


class Levels(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(assessment(40)["level"], ug.OK)
        self.assertEqual(assessment(76)["level"], ug.NOTICE)
        self.assertEqual(assessment(89)["level"], ug.WRAPUP)
        self.assertEqual(assessment(96)["level"], ug.CRITICAL)
        self.assertEqual(assessment(100)["level"], ug.CRITICAL)

    def test_burn_rate_escalates_early(self):
        # 70% but burning 2%/min: ~15 min left -> wrap-up before the % threshold.
        a = assessment(70, rate=2.0)
        self.assertEqual(a["level"], ug.WRAPUP)
        self.assertAlmostEqual(a["worst"]["eta"], 15.0, places=1)
        # Fast burn at low usage is ignored (noisy).
        self.assertEqual(assessment(30, rate=5.0)["level"], ug.OK)

    def test_reset_before_exhaustion_downgrades(self):
        self.assertEqual(assessment(90, resets_in_min=3)["level"], ug.NOTICE)
        # Slow burn, reset in 20 min, 30 min of budget left: no hand-off needed.
        self.assertEqual(assessment(91, resets_in_min=20, rate=0.3)["level"], ug.NOTICE)
        # Fast burn beats the reset: hand off (9 min left -> wrap-up, 4 min left -> critical).
        self.assertEqual(assessment(91, resets_in_min=20, rate=1.0)["level"], ug.WRAPUP)
        self.assertEqual(assessment(96, resets_in_min=20, rate=1.0)["level"], ug.CRITICAL)

    def test_expired_window_ignored(self):
        self.assertEqual(assessment(99, resets_in_min=-1)["level"], ug.OK)

    def test_model_scoped_window(self):
        now = 1000000.0
        state = {"windows": {"seven_day_opus": {"pct": 97, "resets_at": now + 86400, "t": now}}, "samples": {}}
        self.assertEqual(ug.assess(state, CFG, now, "claude-sonnet-5-5")["level"], ug.OK)
        a = ug.assess(state, CFG, now, "claude-opus-5-5")
        self.assertEqual(a["level"], ug.CRITICAL)
        self.assertIn("switch model", ug.describe(a["worst"], now))

    def test_burn_rate_needs_span(self):
        now = 1000.0
        self.assertIsNone(ug.burn_rate([[now - 60, 10], [now, 12]], now))
        self.assertAlmostEqual(ug.burn_rate([[now - 600, 10], [now, 20]], now), 1.0)


class Decide(unittest.TestCase):
    def test_escalation_once_then_reminders_then_recovery(self):
        now = 1000000.0
        ss = {}
        self.assertIsNone(ug.decide(ss, "main", assessment(50, now=now), CFG, now))
        m = ug.decide(ss, "main", assessment(89, now=now), CFG, now)
        self.assertIn("Hand-off protocol", m)
        self.assertIsNone(ug.decide(ss, "main", assessment(89, now=now), CFG, now))
        m = ug.decide(ss, "main", assessment(96, now=now), CFG, now)
        self.assertIn("Start nothing new", m)
        self.assertNotIn("1. Leave the code consistent", m)  # protocol only sent once
        for _ in range(int(CFG["remind_every"]) - 1):
            self.assertIsNone(ug.decide(ss, "main", assessment(96, now=now), CFG, now))
        self.assertIn("Reminder", ug.decide(ss, "main", assessment(96, now=now), CFG, now))
        later = now + 121 * 60
        m = ug.decide(ss, "main", assessment(2, now=later), CFG, later)
        self.assertIn("reset", m)
        self.assertIsNone(ug.decide(ss, "main", assessment(2, now=later), CFG, later))

    def test_policies(self):
        now = 1000000.0
        inc = ug.decide({}, "main", assessment(89, now=now), CFG, now)
        self.assertIn("keep going carefully", inc)
        stop_cfg = dict(CFG, wrapup_policy="stop")
        stop = ug.decide({}, "main", assessment(89, now=now), stop_cfg, now)
        self.assertIn("don't start new tasks", stop)
        self.assertIn("propose handing off", ug.short_message(ug.WRAPUP, assessment(89, now=now), stop_cfg, now, prompt=True))

    def test_subagent_message(self):
        now = 1000000.0
        m = ug.decide({}, "agent-1", assessment(90, now=now), CFG, now, subagent=True)
        self.assertIn("return your result", m)


class HookProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        self.home = os.path.join(self.tmp, "claude")
        self.repo = os.path.join(self.tmp, "repo")
        for d in (self.data, self.home, self.repo):
            os.makedirs(d)
        self.env = dict(os.environ, CLAUDE_PLUGIN_DATA=self.data, CLAUDE_CONFIG_DIR=self.home,
                        USAGE_GUARD_DEBUG="1", USAGE_GUARD_USAGE_URL="http://127.0.0.1:9/none")
        for k in list(self.env):
            if k.startswith("CLAUDE_PLUGIN_OPTION_"):
                del self.env[k]
        run = lambda *a: subprocess.run(a, cwd=self.repo, check=True, capture_output=True)
        run("git", "init", "-q")
        run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, args, payload, env=None):
        p = subprocess.run([sys.executable, SCRIPT] + args, input=json.dumps(payload), capture_output=True,
                           text=True, env=env or self.env, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def hook(self, event, **extra):
        payload = dict(session_id="s1", cwd=self.repo, hook_event_name=event, transcript_path="", **extra)
        out = self.call(["hook", event], payload)
        return json.loads(out) if out.strip() else None

    def feed(self, pct, seven=10.0, resets_in=7200):
        now = int(time.time())
        out = self.call(["statusline"], {
            "session_id": "s1", "cwd": self.repo, "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
            "context_window": {"used_percentage": 12},
            "rate_limits": {"five_hour": {"used_percentage": pct, "resets_at": now + resets_in},
                            "seven_day": {"used_percentage": seven, "resets_at": now + 86400 * 3}}})
        return out

    def test_silent_when_normal(self):
        self.feed(20)
        for ev in ("SessionStart", "UserPromptSubmit"):
            out = self.hook(ev)
            self.assertTrue(out is None or "additionalContext" not in out.get("hookSpecificOutput", {}), out)
        self.assertIsNone(self.hook("PostToolUse", tool_name="Read"))
        self.assertIsNone(self.hook("PreToolUse", tool_name="Agent"))
        self.assertIsNone(self.hook("Stop", stop_hook_active=False))

    def test_user_config_reaches_statusline(self):
        env = dict(self.env, CLAUDE_PLUGIN_ROOT=ROOT, CLAUDE_PLUGIN_OPTION_WRAPUP_PCT="50")
        self.call(["hook", "SessionStart"], {"session_id": "s1", "cwd": self.repo, "hook_event_name": "SessionStart"}, env=env)
        self.assertEqual(load(os.path.join(self.data, "options.json")), {"wrapup_pct": "50"})
        self.assertIn("\033[38;5;208m5h 60%", self.feed(60))  # wrap-up colour at 60%

    def test_statusline_output(self):
        out = self.feed(91)
        self.assertIn("Opus", out)
        self.assertIn("5h 91%", out)

    def test_wrapup_flow(self):
        self.feed(90)
        out = self.hook("PostToolUse", tool_name="Edit")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Hand-off protocol", ctx)
        self.assertIn("HANDOFF.md", ctx)
        self.assertIn("systemMessage", out)
        self.assertIsNone(self.hook("PostToolUse", tool_name="Read"))
        # Subagents get their own short message.
        sub = self.hook("PostToolUse", tool_name="Read", agent_id="a1")
        self.assertIn("return your result", sub["hookSpecificOutput"]["additionalContext"])
        # No new subagents.
        deny = self.hook("PreToolUse", tool_name="Agent", tool_input={})
        self.assertEqual(deny["hookSpecificOutput"]["permissionDecision"], "deny")
        # New prompt: Claude must tell the user.
        p = self.hook("UserPromptSubmit", user_input="build feature X")
        self.assertIn("Mention this to the user", p["hookSpecificOutput"]["additionalContext"])
        # Stop with a dirty tree and no hand-off -> blocked once.
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("x")
        block = self.hook("Stop", stop_hook_active=False)
        self.assertEqual(block["decision"], "block")
        self.assertIn("write HANDOFF.md unless", block["reason"])
        self.assertIn("commit", block["reason"])
        self.assertIsNone(self.hook("Stop", stop_hook_active=True))
        self.assertIsNone(self.hook("Stop", stop_hook_active=False))

    def test_stop_guard_catches_bash_edits(self):
        self.hook("SessionStart", source="startup")  # records the git baseline
        self.feed(90)
        self.hook("PostToolUse", tool_name="Bash")
        with open(os.path.join(self.repo, "made_by_bash.py"), "w") as f:
            f.write("x = 1\n")
        block = self.hook("Stop", stop_hook_active=False)
        self.assertEqual(block["decision"], "block")
        self.assertIn("commit a checkpoint", block["reason"])

    def test_stop_ignores_sessions_without_work(self):
        self.hook("SessionStart", source="startup")
        self.feed(90)
        self.hook("PostToolUse", tool_name="Read")
        self.assertIsNone(self.hook("Stop", stop_hook_active=False))

    def test_stop_passes_after_handoff(self):
        self.feed(90)
        self.hook("PostToolUse", tool_name="Write")
        time.sleep(0.05)
        with open(os.path.join(self.repo, "HANDOFF.md"), "w") as f:
            f.write("# Hand-off\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "wip"],
                       cwd=self.repo, check=True)
        self.assertIsNone(self.hook("Stop", stop_hook_active=False))

    def test_reannounce_after_compact(self):
        self.feed(90)
        self.assertIn("Hand-off protocol", self.hook("PostToolUse", tool_name="Read")["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.hook("PostToolUse", tool_name="Read"))
        out = self.hook("SessionStart", source="compact")
        self.assertIn("Hand-off protocol", out["hookSpecificOutput"]["additionalContext"])

    def test_session_start_resume_hint(self):
        with open(os.path.join(self.repo, "HANDOFF.md"), "w") as f:
            f.write("# Hand-off\n")
        out = self.hook("SessionStart", source="startup")
        self.assertIn("HANDOFF.md exists", out["hookSpecificOutput"]["additionalContext"])

    def test_stop_failure_snapshot(self):
        self.feed(100)
        transcript = os.path.join(self.tmp, "t.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "Implement milestone 3"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "TodoWrite",
                    "input": {"todos": [{"content": "task A", "status": "completed"},
                                        {"content": "task B", "status": "in_progress"}]}}]}}) + "\n")
        with open(os.path.join(self.repo, "wip.py"), "w") as f:
            f.write("x = 1\n")
        payload = dict(session_id="s1", cwd=self.repo, hook_event_name="StopFailure", error="rate_limit",
                       transcript_path=transcript)
        out = json.loads(self.call(["hook", "StopFailure"], payload))
        self.assertIn("terminalSequence", out)
        text = read(os.path.join(self.repo, "HANDOFF.md"))
        self.assertIn("Implement milestone 3", text)
        self.assertIn("[in_progress] task B", text)
        self.assertNotIn("task A", text)
        self.assertIn("wip.py", text)
        self.call(["hook", "StopFailure"], payload)
        text2 = read(os.path.join(self.repo, "HANDOFF.md"))
        self.assertEqual(text2.count(ug.SNAP_START), 1)

    def test_install_wraps_existing_statusline(self):
        settings = os.path.join(self.home, "settings.json")
        with open(settings, "w") as f:
            json.dump({"statusLine": {"type": "command", "command": "echo MYLINE", "padding": 1}, "x": 1}, f)
        print_out = self.call(["install-statusline"], {})
        self.assertIn("wrapped", print_out)
        s = load(settings)
        self.assertIn("usage_guard.py", s["statusLine"]["command"])
        self.assertEqual(s["statusLine"]["padding"], 1)
        self.assertEqual(s["x"], 1)
        # The installed command works and wraps the original.
        cmd = s["statusLine"]["command"]
        now = int(time.time())
        p = subprocess.run(cmd, shell=True, input=json.dumps({"rate_limits": {"five_hour": {
            "used_percentage": 80, "resets_at": now + 3600}}}), capture_output=True, text=True,
            env={k: v for k, v in self.env.items() if k != "CLAUDE_PLUGIN_DATA"})
        self.assertIn("MYLINE", p.stdout)
        self.assertIn("5h 80%", p.stdout)
        self.call(["uninstall-statusline"], {})
        self.assertEqual(load(settings)["statusLine"]["command"], "echo MYLINE")


class FakeUsage(BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        FakeUsage.seen = dict(self.headers)
        self.send_response(FakeUsage.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if FakeUsage.status == 200:
            self.wfile.write(json.dumps({
                "five_hour": {"utilization": 93.0, "resets_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 3600))},
                "seven_day": {"utilization": 40.0, "resets_at": None},
                "extra_usage": {"is_enabled": False}}).encode())

    def log_message(self, *a):
        pass


class Fetch(unittest.TestCase):
    def test_fetch_and_backoff(self):
        tmp = tempfile.mkdtemp()
        try:
            home = os.path.join(tmp, "claude")
            os.makedirs(home)
            with open(os.path.join(home, ".credentials.json"), "w") as f:
                json.dump({"claudeAiOauth": {"accessToken": "tok", "expiresAt": (time.time() + 3600) * 1000}}, f)
            srv = HTTPServer(("127.0.0.1", 0), FakeUsage)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            env = dict(os.environ, CLAUDE_PLUGIN_DATA=os.path.join(tmp, "d"), CLAUDE_CONFIG_DIR=home,
                       USAGE_GUARD_USAGE_URL="http://127.0.0.1:%d/" % srv.server_port, USAGE_GUARD_DEBUG="1")
            env.pop("ANTHROPIC_BASE_URL", None)
            subprocess.run([sys.executable, SCRIPT, "fetch"], env=env, check=True, timeout=20)
            self.assertEqual(FakeUsage.seen.get("Authorization"), "Bearer tok")
            state = load(os.path.join(tmp, "d", "usage.json"))
            self.assertEqual(state["windows"]["five_hour"]["pct"], 93.0)
            self.assertEqual(state["windows"]["five_hour"]["src"], "api")
            self.assertIsNone(state["api"]["err"])
            FakeUsage.status = 429
            subprocess.run([sys.executable, SCRIPT, "fetch"], env=env, check=True, timeout=20)
            state = load(os.path.join(tmp, "d", "usage.json"))
            self.assertEqual(state["api"]["err"], "http-429")
            self.assertGreater(state["api"]["next_t"], time.time() + 300)
            self.assertEqual(state["windows"]["five_hour"]["pct"], 93.0)  # last good data kept
            srv.shutdown()
            srv.server_close()
        finally:
            FakeUsage.status = 200
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
