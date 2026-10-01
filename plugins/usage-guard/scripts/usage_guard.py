#!/usr/bin/env python3
"""usage-guard: make Claude Code aware of claude.ai usage limits.

One self-contained, stdlib-only script that serves every entry point:

  hook <Event>          Claude Code hook handler (reads hook JSON on stdin)
  statusline            status line command: records rate_limits, prints a segment
  fetch                 background poll of the usage endpoint (fallback source)
  status                human-readable report
  install-statusline    tap the status line feed (wraps any existing status line)
  uninstall-statusline  restore the previous status line
  snapshot              write the emergency hand-off block for the current repo

Design rules: never block or slow Claude Code (always exit 0, no network in the
foreground), and cost zero context tokens while usage is normal.
"""

import json
import os
import sys
import time

VERSION = "1.0.0"

OK, NOTICE, WRAPUP, CRITICAL = 0, 1, 2, 3
LEVEL_NAMES = ("ok", "notice", "wrap-up", "critical")

DEFAULTS = {
    "enabled": True,
    # Percentage thresholds (per window).
    "notice_pct": 75,
    "wrapup_pct": 88,
    "critical_pct": 95,
    # Projected minutes until the limit at the current burn rate.
    "notice_eta_min": 45,
    "wrapup_eta_min": 20,
    "critical_eta_min": 8,
    # Burn-rate projections only escalate above this usage (rates are noisy early).
    "eta_min_pct": 50,
    # A window resetting within this many minutes never forces a hand-off.
    "reset_grace_min": 5,
    "handoff_file": "HANDOFF.md",
    "auto_commit": True,
    "block_subagents": True,
    "stop_guard": True,
    # At wrap-up: "incremental" = hand off early, then continue in small committed
    # steps until critical; "stop" = hand off and stop starting work.
    "wrapup_policy": "incremental",
    "auto_snapshot": True,
    "notify": True,
    "api_fallback": True,
    "api_poll_sec": 300,
    "remind_every": 15,
    "resume_hint_days": 3,
    "statusline_segment": "always",  # always | elevated | never
}

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SUBAGENT_TOOLS = {"Agent", "Task"}

WINDOW_LABELS = {
    "five_hour": "5h",
    "seven_day": "weekly",
    "seven_day_opus": "weekly Opus",
    "seven_day_sonnet": "weekly Sonnet",
    "spend_limit": "spend",
}
# Windows that only apply to one model family: switching models escapes them.
MODEL_SCOPED = {"seven_day_opus": "opus", "seven_day_sonnet": "sonnet"}
IGNORED_API_KEYS = {"extra_usage", "seven_day_oauth_apps"}

SNAP_START = "<!-- usage-guard:auto:start -->"
SNAP_END = "<!-- usage-guard:auto:end -->"

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
STATUSLINE_FRESH_SEC = 180


# --------------------------------------------------------------------------- #
# Paths, config, small file helpers
# --------------------------------------------------------------------------- #

_DATA_OVERRIDE = None


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def data_dir():
    if _DATA_OVERRIDE:
        d = _DATA_OVERRIDE
    elif os.environ.get("CLAUDE_PLUGIN_DATA"):
        d = os.environ["CLAUDE_PLUGIN_DATA"]
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(here, ".usage-guard-data")):
            d = here
        else:
            base = os.path.join(config_dir(), "plugins", "data")
            d = os.path.join(base, "usage-guard")
            try:
                found = sorted(n for n in os.listdir(base) if n.startswith("usage-guard"))
                if found:
                    d = os.path.join(base, found[0])
            except OSError:
                pass
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _coerce(value, default):
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return str(value)


def plugin_options():
    """userConfig values: exported to hooks as env vars, persisted for the status line,
    which runs outside the plugin environment."""
    opts = {k: os.environ["CLAUDE_PLUGIN_OPTION_" + k.upper()] for k in DEFAULTS
            if os.environ.get("CLAUDE_PLUGIN_OPTION_" + k.upper()) not in (None, "")}
    path = os.path.join(data_dir(), "options.json")
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        if read_json(path, None) != opts:
            write_json(path, opts)
        return opts
    return opts or read_json(path, {}) or {}


def load_config(cwd=None):
    cfg = dict(DEFAULTS)
    for key, raw in plugin_options().items():
        if key in DEFAULTS:
            cfg[key] = _coerce(raw, DEFAULTS[key])
    files = [os.path.join(config_dir(), "usage-guard.json")]
    root = cwd and (git_root(cwd) or cwd)
    if root:
        files.append(os.path.join(root, ".claude", "usage-guard.json"))
    for path in files:
        extra = read_json(path, None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key in DEFAULTS:
                    cfg[key] = _coerce(value, DEFAULTS[key])
    if os.environ.get("USAGE_GUARD_DISABLE", "").strip() in ("1", "true", "yes"):
        cfg["enabled"] = False
    return cfg


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class Locked:
    """Exclusive advisory lock (POSIX); a no-op where flock is unavailable."""

    def __init__(self, path):
        self.path = path + ".lock"
        self.f = None

    def __enter__(self):
        try:
            import fcntl

            self.f = open(self.path, "a")
            fcntl.flock(self.f, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        return self

    def __exit__(self, *exc):
        if self.f:
            try:
                self.f.close()
            except OSError:
                pass


def run(cmd, cwd=None, timeout=3, input_text=None, shell=False):
    import subprocess

    try:
        p = subprocess.run(
            cmd, cwd=cwd, input=input_text, shell=shell, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True,
        )
        return p.returncode, p.stdout
    except Exception:
        return 1, ""


def git_root(cwd):
    if not cwd or not os.path.isdir(cwd):
        return None
    probe = cwd
    while True:
        if os.path.exists(os.path.join(probe, ".git")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent


# --------------------------------------------------------------------------- #
# Usage state: ingest samples, estimate burn rate, assess risk
# --------------------------------------------------------------------------- #

def usage_path():
    return os.path.join(data_dir(), "usage.json")


def load_usage():
    state = read_json(usage_path(), None)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("windows", {})
    state.setdefault("samples", {})
    state.setdefault("api", {})
    return state


def parse_ts(value):
    """Epoch seconds from epoch s/ms numbers or ISO-8601 strings."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e12 else float(value)
    try:
        from datetime import datetime

        s = str(value).strip().replace("Z", "+00:00")
        if "." in s:  # trim sub-second precision that older Pythons reject
            head, _, tail = s.partition(".")
            tz = ""
            for sep in ("+", "-"):
                if sep in tail:
                    tz = sep + tail.split(sep, 1)[1]
                    break
            s = head + tz
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def extract_windows(obj):
    """Normalise statusline `rate_limits` or the usage API body to {name: (pct, resets_at)}."""
    out = {}
    if not isinstance(obj, dict):
        return out
    for name, w in obj.items():
        if name in IGNORED_API_KEYS or not isinstance(w, dict):
            continue
        pct = w.get("used_percentage", w.get("utilization"))
        if not isinstance(pct, (int, float)):
            continue
        out[name] = (float(pct), parse_ts(w.get("resets_at")))
    return out


def ingest(state, windows, src, now):
    """Record a reading. Returns True when anything meaningful changed."""
    changed = False
    for name, (pct, resets) in windows.items():
        prev = state["windows"].get(name) or {}
        samples = state["samples"].setdefault(name, [])
        new_epoch = False
        if prev.get("resets_at") and resets and abs(prev["resets_at"] - resets) > 600:
            new_epoch = True
        if samples and pct < samples[-1][1] - 2:
            new_epoch = True
        if new_epoch:
            del samples[:]
        if prev.get("pct") != pct or prev.get("resets_at") != resets or now - prev.get("t", 0) > 30:
            changed = True
        state["windows"][name] = {"pct": pct, "resets_at": resets, "t": now, "src": src}
        if not samples or samples[-1][1] != pct or now - samples[-1][0] >= 60:
            samples.append([round(now, 1), pct])
            changed = True
        cutoff = now - 3600
        state["samples"][name] = [s for s in samples if s[0] >= cutoff][-200:]
    return changed


def burn_rate(samples, now):
    """%/minute, the higher of the 8- and 20-minute trends (reacts fast, errs early)."""
    best = None
    for span_min in (8, 20):
        pts = [s for s in samples if now - s[0] <= span_min * 60]
        if len(pts) < 2:
            continue
        dt = (pts[-1][0] - pts[0][0]) / 60.0
        if dt < 3:
            continue
        rate = max(0.0, (pts[-1][1] - pts[0][1]) / dt)
        best = rate if best is None else max(best, rate)
    return best


def window_level(pct, eta, ttr, cfg):
    if pct >= 100:
        return CRITICAL
    level = OK
    if pct >= cfg["critical_pct"]:
        level = CRITICAL
    elif pct >= cfg["wrapup_pct"]:
        level = WRAPUP
    elif pct >= cfg["notice_pct"]:
        level = NOTICE
    if eta is not None and pct >= cfg["eta_min_pct"]:
        if eta <= cfg["critical_eta_min"]:
            level = max(level, CRITICAL)
        elif eta <= cfg["wrapup_eta_min"]:
            level = max(level, WRAPUP)
        elif eta <= cfg["notice_eta_min"]:
            level = max(level, NOTICE)
    # The window resets before we would run out: stay informed, don't hand off.
    if level > NOTICE and ttr is not None and pct < 99:
        if ttr <= cfg["reset_grace_min"] or (eta is not None and eta > ttr):
            level = NOTICE
    return level


def model_family(model):
    m = (model or "").lower()
    for fam in ("opus", "sonnet", "haiku", "fable"):
        if fam in m:
            return fam
    return None


def assess(state, cfg, now, model=None):
    fam = model_family(model)
    windows = []
    for name, w in state.get("windows", {}).items():
        resets = w.get("resets_at")
        if resets and resets <= now:
            continue  # window rolled over; the reading is obsolete
        scope = MODEL_SCOPED.get(name)
        if scope and fam and fam != scope:
            continue
        age = now - w.get("t", now)
        if age > 6 * 3600:
            continue
        rate = burn_rate(state.get("samples", {}).get(name, []), now)
        pct = float(w.get("pct", 0))
        estimated = False
        if rate and 90 < age < 1200:
            pct = min(100.0, pct + rate * age / 60.0)
            estimated = True
        eta = (100.0 - pct) / rate if rate and rate > 0.005 and pct < 100 else None
        ttr = (resets - now) / 60.0 if resets else None
        level = window_level(pct, eta, ttr, cfg)
        windows.append({
            "name": name, "label": WINDOW_LABELS.get(name, name.replace("_", " ")),
            "pct": pct, "rate": rate, "eta": eta, "ttr": ttr, "resets_at": resets,
            "level": level, "scope": scope, "estimated": estimated, "src": w.get("src"), "age": age,
        })
    windows.sort(key=lambda x: (x["level"], x["pct"]), reverse=True)
    worst = windows[0] if windows else None
    return {"level": worst["level"] if worst else OK, "worst": worst, "windows": windows,
            "extra_usage": state.get("extra_usage")}


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

def fmt_dur(minutes):
    m = int(round(max(0, minutes)))
    if m < 60:
        return "%dm" % m
    h, m = divmod(m, 60)
    if h < 24:
        return "%dh%02dm" % (h, m) if m else "%dh" % h
    d, h = divmod(h, 24)
    return "%dd%dh" % (d, h) if h else "%dd" % d


def fmt_clock(ts, now):
    lt = time.localtime(ts)
    return time.strftime("%H:%M" if ts - now < 20 * 3600 else "%a %H:%M", lt)


def describe(w, now):
    s = "%s limit %s%.0f%% used" % (w["label"], "~" if w["estimated"] else "", w["pct"])
    if w["eta"] is not None and (w["ttr"] is None or w["eta"] < w["ttr"]):
        s += ", ~%s to the limit at the current pace" % fmt_dur(w["eta"])
    if w["resets_at"]:
        s += ", resets %s (in %s)" % (fmt_clock(w["resets_at"], now), fmt_dur(w["ttr"]))
    if w["scope"]:
        s += "; only %s models count, so the user could switch model" % w["scope"].capitalize()
    return s


def protocol_text(cfg):
    h = cfg["handoff_file"]
    if cfg["auto_commit"]:
        commit = ("Commit a checkpoint on the current branch: stage your changes and %s (no secrets or "
                  "build output), message \"wip: checkpoint before usage limit (see %s)\". Don't push "
                  "unless that is already the workflow." % (h, h))
    else:
        commit = "Don't commit; leave changes in the working tree."
    return (
        "Hand-off protocol:\n"
        "1. Leave the code consistent: finish or revert the half-done edit; no broken builds.\n"
        "2. Write %s at the repo root (replace old content) so ANY agent or human can continue: goal; "
        "done (with commits); in progress (exact files/functions, what's left); next steps in order; "
        "decisions and gotchas; how to verify (commands).\n"
        "3. Update the project's task/backlog statuses if it keeps them.\n"
        "4. %s\n"
        "5. When you stop, tell the user in at most 3 lines: limit status, reset time, where the hand-off is."
        % (h, commit)
    )


def reset_hint(w):
    if w and w["ttr"] is not None and w["ttr"] <= 30 and not w["scope"]:
        return " It resets in %s: if the user is around, pausing until then may beat handing off." % fmt_dur(w["ttr"])
    return ""


def message_for(level, a, cfg, now, subagent=False, with_protocol=False):
    w = a["worst"]
    d = describe(w, now)
    h = cfg["handoff_file"]
    extra = ""
    if (a.get("extra_usage") or {}).get("is_enabled"):
        extra = " (Usage credits are on: past the limit, work continues but bills credits.)"
    if subagent:
        if level >= WRAPUP:
            return ("[usage-guard] Account %s. Wrap up now: stop exploring and return your result so far "
                    "plus what remains undone, briefly." % d)
        return "[usage-guard] Account %s. Be economical: targeted searches, no broad exploration." % d
    if level == NOTICE:
        return ("[usage-guard] %s.%s Conserve budget: prefer targeted Grep/Read (offset/limit) over broad "
                "exploration, avoid subagents and re-reading large files, and commit after each finished "
                "task so a cutoff loses little." % (d, extra))
    proto = ("\n" + protocol_text(cfg)) if with_protocol else " (Hand-off protocol: see earlier usage-guard note.)"
    if level == WRAPUP and cfg["wrapup_policy"] != "stop":
        return ("[usage-guard] %s.%s The limit is close, so make the work interruption-safe now, then keep "
                "going carefully. At the next stable point, write %s and commit (protocol below). After "
                "that, continue only in small steps: finish, commit and update %s after each one before "
                "starting the next; no large reads, broad exploration or subagents. A critical warning will "
                "tell you when to stop.%s%s" % (d, extra, h, h, reset_hint(w), proto))
    if level == WRAPUP:
        return ("[usage-guard] %s.%s Wrap up: don't start new tasks or large reads; finish only the current "
                "small step (or revert it), then hand off and tell the user.%s%s" % (d, extra, reset_hint(w), proto))
    return ("[usage-guard] %s.%s You may be cut off within minutes. Start nothing new: make sure %s is "
            "current and committed, in as few tool calls as possible, then tell the user and end the turn.%s%s"
            % (d, extra, h, reset_hint(w), proto))


def short_message(level, a, cfg, now, prompt=False):
    d = describe(a["worst"], now)
    h = cfg["handoff_file"]
    incremental = level == WRAPUP and cfg["wrapup_policy"] != "stop"
    if prompt and incremental:
        return ("[usage-guard] %s. Mention this to the user. For a work request: first write the plan to %s "
                "and commit it, then proceed in small committed steps, keeping %s current." % (d, h, h))
    if prompt and level >= WRAPUP:
        return ("[usage-guard] %s. Tell the user about the limit before starting; for anything non-trivial, "
                "propose handing off (%s) instead of starting it." % (d, h))
    if prompt:
        return "[usage-guard] %s. Keep this request lean; commit after each finished task." % d
    if incremental:
        return "[usage-guard] Reminder: %s. Small committed steps only; keep %s current." % (d, h)
    return "[usage-guard] Reminder: %s. Hand off and stop now if not done (%s)." % (d, h)


# --------------------------------------------------------------------------- #
# Per-session notification state
# --------------------------------------------------------------------------- #

def session_path(sid):
    safe = "".join(c for c in str(sid or "default") if c.isalnum() or c in "-_")[:80] or "default"
    d = os.path.join(data_dir(), "sessions")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, safe + ".json")


def decide(ss, key, a, cfg, now, subagent=False):
    """Return a message when the level escalates, recovers, or needs a reminder."""
    ag = ss.setdefault("agents", {}).setdefault(key, {"level": OK, "n": 0})
    level = a["level"]
    w = a["worst"]
    if ag["level"] > OK and level < ag["level"] and ag.get("reset_at") and now >= ag["reset_at"]:
        was = ag["level"]
        ag.update(level=OK, n=0, proto=False, reset_at=None)
        if level == OK:
            if not subagent and was >= NOTICE:
                return "[usage-guard] Usage limits have reset. Resume normal operation."
            return None
    if level > ag["level"]:
        with_proto = level >= WRAPUP and not ag.get("proto") and not subagent
        ag.update(level=level, n=0, reset_at=w["resets_at"] if w else None, esc_t=now)
        if with_proto:
            ag["proto"] = True
        return message_for(level, a, cfg, now, subagent=subagent, with_protocol=with_proto)
    if level >= WRAPUP:
        ag["n"] = ag.get("n", 0) + 1
        if ag["n"] >= cfg["remind_every"] and not subagent:
            ag["n"] = 0
            return short_message(level, a, cfg, now)
    return None


# --------------------------------------------------------------------------- #
# Background fetch from the usage endpoint (fallback when no status line feed)
# --------------------------------------------------------------------------- #

def api_allowed():
    for var in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        if os.environ.get(var, "") not in ("", "0", "false"):
            return False
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    return not base or "anthropic.com" in base


def read_token():
    """Read Claude Code's OAuth access token. Never refreshes it (that would rotate
    the refresh token out from under Claude Code)."""
    blob = read_json(os.path.join(config_dir(), ".credentials.json"), None)
    if not blob and sys.platform == "darwin":
        import hashlib

        services = ["Claude Code-credentials"]
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            suffix = hashlib.sha256(config_dir().encode()).hexdigest()[:8]
            services.insert(0, "Claude Code-credentials-" + suffix)
        for svc in services:
            code, out = run(["security", "find-generic-password", "-s", svc, "-w"], timeout=4)
            if code == 0 and out.strip():
                try:
                    blob = json.loads(out)
                    break
                except ValueError:
                    pass
    oauth = (blob or {}).get("claudeAiOauth") if isinstance(blob, dict) else None
    if isinstance(oauth, dict) and oauth.get("accessToken"):
        exp = oauth.get("expiresAt")
        if isinstance(exp, (int, float)) and exp / 1000.0 < time.time() + 60:
            return None
        return oauth["accessToken"]
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or None


def maybe_spawn_fetch(state, cfg, now, level):
    if not cfg["api_fallback"] or not api_allowed():
        return
    if now - state.get("statusline_t", 0) < STATUSLINE_FRESH_SEC:
        return  # the status line feed is live and authoritative
    if now < state["api"].get("next_t", 0):
        return
    lock = os.path.join(data_dir(), "fetch.lock")
    try:
        if now - os.path.getmtime(lock) < 60:
            return
    except OSError:
        pass
    try:
        with open(lock, "w") as f:
            f.write(str(os.getpid()))
        import subprocess

        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL, "close_fds": True}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        env = dict(os.environ, CLAUDE_PLUGIN_DATA=data_dir(), USAGE_GUARD_LEVEL=str(level))
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "fetch"], env=env, **kwargs)
    except Exception:
        pass


def cmd_fetch():
    import urllib.error
    import urllib.request

    now = time.time()
    cfg = load_config()
    elevated = os.environ.get("USAGE_GUARD_LEVEL", "0") not in ("", "0")
    poll = max(60.0, cfg["api_poll_sec"] / 2.0 if elevated else cfg["api_poll_sec"])
    result, windows, extra = {}, {}, None
    token = read_token() if api_allowed() else None
    if not token:
        result = {"err": "no-credentials", "next_t": now + 1800}
    else:
        req = urllib.request.Request(os.environ.get("USAGE_GUARD_USAGE_URL", USAGE_URL), headers={
            "Authorization": "Bearer " + token,
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
            "User-Agent": "usage-guard/" + VERSION,
        })
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            windows = extract_windows(body)
            if isinstance(body.get("extra_usage"), dict):
                extra = {"is_enabled": bool(body["extra_usage"].get("is_enabled"))}
            result = {"err": None, "ok_t": now, "fails": 0, "next_t": now + poll}
        except urllib.error.HTTPError as e:
            fails = load_usage()["api"].get("fails", 0) + 1
            wait = poll * (2 ** min(fails, 4))
            if e.code == 429:
                try:
                    wait = max(wait, float(e.headers.get("retry-after") or 0))
                except ValueError:
                    pass
            elif e.code in (401, 403):
                wait = 1800
            result = {"err": "http-%d" % e.code, "fails": fails, "next_t": now + min(wait, 3600)}
        except Exception as e:
            fails = load_usage()["api"].get("fails", 0) + 1
            result = {"err": type(e).__name__, "fails": fails, "next_t": now + min(poll * 2 ** min(fails, 4), 3600)}
    path = usage_path()
    with Locked(path):
        state = load_usage()
        state["api"].update(result, last_t=now)
        if windows:
            ingest(state, windows, "api", now)
        if extra is not None:
            state["extra_usage"] = extra
        write_json(path, state)
    try:
        os.unlink(os.path.join(data_dir(), "fetch.lock"))
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Emergency snapshot (no model involved): written when the limit is hit
# --------------------------------------------------------------------------- #

def tail_lines(path, max_bytes=1500000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    return lines[1:] if size > max_bytes else lines


def transcript_facts(transcript_path):
    """Last real user request and the latest TodoWrite list from the transcript tail."""
    last_user, todos = None, None
    for line in reversed(tail_lines(transcript_path)):
        if last_user is not None and todos is not None:
            break
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        msg = ev.get("message") or {}
        content = msg.get("content")
        if ev.get("type") == "user" and last_user is None and not ev.get("isMeta"):
            text = None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
                if parts and not any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                    text = "\n".join(parts)
            if text and not text.lstrip().startswith("<"):
                last_user = text.strip()
        elif ev.get("type") == "assistant" and todos is None and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "TodoWrite":
                    todos = [(t.get("status", "?"), t.get("content", ""))
                             for t in (c.get("input") or {}).get("todos", []) if isinstance(t, dict)]
    return last_user, todos


def task_list(session_id):
    d = os.path.join(config_dir(), "tasks", str(session_id or ""))
    out = []
    try:
        names = sorted(os.listdir(d), key=lambda n: (len(n), n))
    except OSError:
        return None
    for n in names:
        if n.endswith(".json"):
            t = read_json(os.path.join(d, n), None)
            if isinstance(t, dict):
                out.append((t.get("status", "?"), t.get("subject") or t.get("content") or ""))
    return out or None


def write_snapshot(cwd, cfg, session_id=None, transcript_path=None, reason="usage limit reached"):
    now = time.time()
    root = git_root(cwd)
    stamp = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(now))
    lines = [SNAP_START, "## Interrupted: %s (auto-captured %s)" % (reason, stamp), "",
             "_Written by usage-guard without the model: Claude was cut off mid-task, so the "
             "notes above (if any) may be out of date. Trust `git status` / `git diff` first._", ""]
    if root:
        _, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
        _, head = run(["git", "log", "-1", "--format=%h %s"], cwd=root)
        _, status = run(["git", "status", "--porcelain"], cwd=root)
        changes = [l for l in status.splitlines() if l.strip()]
        lines.append("- Branch: `%s` at `%s`" % (branch.strip() or "?", head.strip() or "no commits"))
        if changes:
            lines.append("- Uncommitted changes (%d):" % len(changes))
            lines.append("  ```")
            lines.extend("  " + c for c in changes[:40])
            if len(changes) > 40:
                lines.append("  ... %d more" % (len(changes) - 40))
            lines.append("  ```")
        else:
            lines.append("- Working tree clean.")
    last_user, todos = transcript_facts(transcript_path) if transcript_path else (None, None)
    tasks = task_list(session_id) or todos
    if last_user:
        text = " ".join(last_user.split())
        lines.append("- Last user request: \"%s\"" % (text[:600] + (" ..." if len(text) > 600 else "")))
    if tasks:
        open_tasks = [t for t in tasks if t[0] not in ("completed", "deleted")]
        if open_tasks:
            lines.append("- Open tasks:")
            lines.extend("  - [%s] %s" % (s, " ".join(str(c).split())[:200]) for s, c in open_tasks[:30])
    a = assess(load_usage(), cfg, now)
    if a["windows"]:
        lines.append("- Limits: " + "; ".join(
            "%s %.0f%%%s" % (w["label"], w["pct"], (", resets " + fmt_clock(w["resets_at"], now)) if w["resets_at"] else "")
            for w in a["windows"]))
    lines += ["", "To resume: review the diff, run the project's checks, then continue with the open tasks.", SNAP_END]
    block = "\n".join(lines)

    if root:
        path = os.path.join(root, cfg["handoff_file"])
    else:
        snap_dir = os.path.join(data_dir(), "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        path = os.path.join(snap_dir, "%s.md" % (session_id or "session"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
    except OSError:
        existing = ""
    if SNAP_START in existing and SNAP_END in existing:
        head, _, rest = existing.partition(SNAP_START)
        _, _, tail = rest.partition(SNAP_END)
        new = head + block + tail
    elif existing.strip():
        new = existing.rstrip() + "\n\n" + block + "\n"
    else:
        new = "# Hand-off\n\n" + block + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return path


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #

def emit(obj):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def ctx(event, text):
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def is_dirty(root):
    code, out = run(["git", "status", "--porcelain"], cwd=root)
    return code == 0 and bool(out.strip())


def git_fingerprint(cwd):
    """HEAD plus a hash of `git status`: changes when work happens by any tool, Bash included."""
    root = git_root(cwd)
    if not root:
        return None
    import hashlib

    _, head = run(["git", "rev-parse", "HEAD"], cwd=root)
    _, status = run(["git", "status", "--porcelain"], cwd=root)
    return head.strip() + ":" + hashlib.sha1(status.encode("utf-8", "replace")).hexdigest()[:12]


def handoff_info(cwd, cfg):
    root = git_root(cwd) or cwd
    path = os.path.join(root, cfg["handoff_file"]) if root else None
    try:
        return path, os.path.getmtime(path)
    except (OSError, TypeError):
        return path, None


def cmd_hook(event_arg):
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    event = data.get("hook_event_name") or event_arg
    cwd = data.get("cwd") or os.getcwd()
    cfg = load_config(cwd)
    if not cfg["enabled"]:
        return
    now = time.time()
    sid = data.get("session_id")
    agent_id = data.get("agent_id")
    key = agent_id or "main"

    if event == "StopFailure":
        return hook_stop_failure(data, cfg, cwd)

    state = load_usage()
    spath = session_path(sid)
    with Locked(spath):
        ss = read_json(spath, None) or {}
        if event == "SessionStart" and data.get("model"):
            ss["model"] = data["model"] if isinstance(data["model"], str) else (data["model"] or {}).get("id")
        model = ss.get("model") or (state.get("models") or {}).get(sid or "")
        a = assess(state, cfg, now, model)
        maybe_spawn_fetch(state, cfg, now, a["level"])
        out = None

        if event == "PostToolUse":
            if not agent_id and data.get("tool_name") in EDIT_TOOLS:
                ss["edits"] = ss.get("edits", 0) + 1
            msg = decide(ss, key, a, cfg, now, subagent=bool(agent_id))
            if msg:
                out = ctx(event, msg)
                if not agent_id and a["level"] >= WRAPUP:
                    out["systemMessage"] = "usage-guard: %s; asked Claude to wrap up and hand off." % describe(a["worst"], now)

        elif event == "UserPromptSubmit":
            if "base" not in ss:
                ss["base"] = git_fingerprint(cwd)
            msg = decide(ss, key, a, cfg, now)
            if not msg and a["level"] >= NOTICE:
                msg = short_message(a["level"], a, cfg, now, prompt=True)
            if msg:
                out = ctx(event, msg)

        elif event == "PreToolUse":
            if (cfg["block_subagents"] and a["level"] >= WRAPUP
                    and data.get("tool_name") in SUBAGENT_TOOLS):
                out = {"hookSpecificOutput": {
                    "hookEventName": "PreToolUse", "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "[usage-guard] %s. A subagent now would likely be cut off and waste the remaining "
                        "budget; do the step inline or hand off." % describe(a["worst"], now))}}

        elif event == "Stop":
            out = stop_guard(data, ss, a, cfg, cwd, now)

        elif event == "SessionStart":
            out = session_start(data, ss, a, cfg, cwd, now, state)

        if out is not None or event in ("PostToolUse", "UserPromptSubmit", "SessionStart", "Stop"):
            ss["t"] = now
            write_json(spath, ss)
    if out:
        emit(out)


def stop_guard(data, ss, a, cfg, cwd, now):
    if (not cfg["stop_guard"] or data.get("agent_id") or data.get("stop_hook_active")
            or a["level"] < WRAPUP or ss.get("stop_fired", -1) >= a["level"]):
        return None
    root = git_root(cwd)
    worked = ss.get("edits") or (root and ss.get("base") and git_fingerprint(cwd) != ss["base"])
    if not worked:
        return None
    esc_t = ((ss.get("agents") or {}).get("main") or {}).get("esc_t", now)
    path, mtime = handoff_info(cwd, cfg)
    handoff_fresh = mtime is not None and mtime >= esc_t - 5
    dirty = bool(root) and cfg["auto_commit"] and is_dirty(root)
    if handoff_fresh and not dirty:
        return None
    todo = []
    if dirty:
        todo.append("commit a checkpoint of the work so far")
    if not handoff_fresh:
        todo.append("write %s unless everything the user asked for is finished" % cfg["handoff_file"])
    ss["stop_fired"] = a["level"]
    return {"decision": "block", "reason": (
        "[usage-guard] %s. Before ending this turn, leave the workspace at a stable point: %s. Then tell "
        "the user where things stand." % (describe(a["worst"], now), "; ".join(todo)))}


def session_start(data, ss, a, cfg, cwd, now, state):
    msgs = []
    if data.get("source") in ("compact", "clear"):
        # Earlier notes (and the protocol) are gone from context: announce again.
        ss["agents"] = {}
        if data.get("source") == "clear":
            ss.pop("base", None)
            ss.pop("edits", None)
            ss.pop("stop_fired", None)
    if "base" not in ss:
        ss["base"] = git_fingerprint(cwd)
    if a["level"] >= NOTICE:
        m = decide(ss, "main", a, cfg, now)
        if m:
            msgs.append(m)
    path, mtime = handoff_info(cwd, cfg)
    if mtime and now - mtime < cfg["resume_hint_days"] * 86400 and data.get("source") in (None, "startup", "resume"):
        msgs.append("[usage-guard] %s exists from earlier work (updated %s ago). If the user wants to continue "
                    "previous work, read it first; when its work is finished, delete it."
                    % (cfg["handoff_file"], fmt_dur((now - mtime) / 60.0)))
    out = ctx("SessionStart", "\n".join(msgs)) if msgs else {}

    ddir = data_dir()
    sync_statusline_copy(ddir)
    flag = os.path.join(ddir, "onboarded")
    if not os.path.exists(flag) and not state.get("statusline_t"):
        try:
            open(flag, "w").close()
        except OSError:
            pass
        out["systemMessage"] = ("usage-guard is active. For live, per-response limit data run "
                                "/usage-guard:setup (taps your status line; keeps any existing one).")
    cleanup_sessions(now)
    return out or None


def sync_statusline_copy(ddir):
    """Keep the status line's stable copy of this script current across plugin updates."""
    dst = os.path.join(ddir, "usage_guard.py")
    if not os.path.exists(dst):
        return
    src = os.path.abspath(__file__)
    if os.path.abspath(dst) == src:
        return
    try:
        with open(src, "rb") as f:
            a = f.read()
        with open(dst, "rb") as f:
            b = f.read()
        if a != b:
            with open(dst + ".tmp", "wb") as f:
                f.write(a)
            os.replace(dst + ".tmp", dst)
    except OSError:
        pass


def cleanup_sessions(now):
    d = os.path.join(data_dir(), "sessions")
    try:
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if now - os.path.getmtime(p) > 3 * 86400:
                os.unlink(p)
    except OSError:
        pass


def hook_stop_failure(data, cfg, cwd):
    if data.get("error") not in (None, "rate_limit"):
        return
    now = time.time()
    path = usage_path()
    with Locked(path):
        state = load_usage()
        state["limit_hit"] = {"t": now, "session": data.get("session_id"), "details": data.get("error_details")}
        write_json(path, state)
    where = None
    if cfg["auto_snapshot"]:
        try:
            where = write_snapshot(cwd, cfg, data.get("session_id"), data.get("transcript_path"))
        except Exception:
            where = None
    if cfg["notify"]:
        body = "Claude hit the usage limit." + (" Hand-off: %s" % where if where else "")
        emit({"terminalSequence": "\033]9;%s\007" % body.replace("\007", "").replace("\033", "")})


# --------------------------------------------------------------------------- #
# Status line
# --------------------------------------------------------------------------- #

COLORS = {OK: "\033[32m", NOTICE: "\033[33m", WRAPUP: "\033[38;5;208m", CRITICAL: "\033[31m"}


def segment(a, now, mode):
    if mode == "never" or not a["windows"] or (mode == "elevated" and a["level"] == OK):
        return ""
    parts = []
    for w in a["windows"]:
        if w["level"] == OK and w["pct"] < 50 and w["name"] not in ("five_hour", "seven_day"):
            continue
        s = "%s %s%.0f%%" % ("7d" if w["name"] == "seven_day" else w["label"], "~" if w["estimated"] else "", w["pct"])
        if w["level"] >= NOTICE:
            if w["eta"] is not None and (w["ttr"] is None or w["eta"] < w["ttr"]):
                s += " ~%s left" % fmt_dur(w["eta"])
            elif w["ttr"] is not None:
                s += " ⟳%s" % fmt_dur(w["ttr"])
        parts.append("%s%s\033[0m" % (COLORS[w["level"]], s))
    return " · ".join(parts)


def cmd_statusline():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    now = time.time()
    ddir = data_dir()
    path = usage_path()
    rl = data.get("rate_limits")
    windows = extract_windows(rl) if isinstance(rl, dict) else {}
    with Locked(path):
        state = load_usage()
        changed = ingest(state, windows, "statusline", now) if windows else False
        sid = data.get("session_id")
        mid = (data.get("model") or {}).get("id") if isinstance(data.get("model"), dict) else None
        models = state.setdefault("models", {})
        if sid and mid and models.get(sid) != mid:
            models[sid] = mid
            if len(models) > 30:
                for k in list(models)[:-30]:
                    del models[k]
            changed = True
        if changed or now - state.get("statusline_t", 0) > 30:
            state["statusline_t"] = now
            write_json(path, state)
    cfg = load_config(data.get("cwd"))
    seg = segment(assess(state, cfg, now, mid), now, cfg["statusline_segment"]) if cfg["enabled"] else ""

    wrap = read_json(os.path.join(ddir, "statusline_wrap.json"), {}) or {}
    if wrap.get("command"):
        _, out = run(wrap["command"], input_text=raw, timeout=5, shell=True, cwd=data.get("cwd") or None)
        out = out.rstrip("\n")
        if seg:
            out = (out + "  " + seg) if out else seg
        sys.stdout.write(out + "\n")
        return
    model = (data.get("model") or {}).get("display_name") if isinstance(data.get("model"), dict) else None
    cw = data.get("context_window") or {}
    bits = [b for b in (model, ("ctx %.0f%%" % cw["used_percentage"]) if isinstance(cw.get("used_percentage"), (int, float)) else None, seg) if b]
    sys.stdout.write(" · ".join(bits) + "\n")


def settings_path():
    return os.path.join(config_dir(), "settings.json")


def cmd_install_statusline():
    ddir = data_dir()
    sp = settings_path()
    settings = read_json(sp, {}) if os.path.exists(sp) else {}
    if not isinstance(settings, dict):
        print("usage-guard: %s is not valid JSON; not touching it." % sp)
        return
    dst = os.path.join(ddir, "usage_guard.py")
    with open(os.path.abspath(__file__), "rb") as f:
        code = f.read()
    with open(dst, "wb") as f:
        f.write(code)
    open(os.path.join(ddir, ".usage-guard-data"), "w").close()
    ours = '"%s" "%s" statusline' % (sys.executable.replace("\\", "/"), dst.replace("\\", "/"))
    current = settings.get("statusLine") if isinstance(settings.get("statusLine"), dict) else None
    wrap_path = os.path.join(ddir, "statusline_wrap.json")
    if current and "usage_guard.py" in str(current.get("command", "")):
        settings["statusLine"]["command"] = ours
        print("usage-guard: status line tap already installed (refreshed).")
    else:
        if current and current.get("command"):
            write_json(wrap_path, current)
            print("usage-guard: your existing status line is kept and wrapped: %s" % current["command"])
        else:
            write_json(wrap_path, {})
        if os.path.exists(sp):
            with open(sp, "rb") as f:
                backup = f.read()
            with open(sp + ".usage-guard.bak", "wb") as f:
                f.write(backup)
        new = {"type": "command", "command": ours}
        if current and "padding" in current:
            new["padding"] = current["padding"]
        settings["statusLine"] = new
        print("usage-guard: status line tap installed in %s" % sp)
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("usage-guard: limits now update on every response. Undo: /usage-guard:setup uninstall")


def cmd_uninstall_statusline():
    ddir = data_dir()
    sp = settings_path()
    settings = read_json(sp, {})
    current = settings.get("statusLine") if isinstance(settings, dict) else None
    if not (isinstance(current, dict) and "usage_guard.py" in str(current.get("command", ""))):
        print("usage-guard: status line tap is not installed.")
        return
    prev = read_json(os.path.join(ddir, "statusline_wrap.json"), {}) or {}
    if prev.get("command"):
        settings["statusLine"] = prev
        print("usage-guard: restored your previous status line.")
    else:
        del settings["statusLine"]
        print("usage-guard: status line removed.")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


# --------------------------------------------------------------------------- #
# Status report
# --------------------------------------------------------------------------- #

def cmd_status():
    now = time.time()
    cfg = load_config(os.getcwd())
    state = load_usage()
    a = assess(state, cfg, now)
    print("usage-guard %s — overall: %s%s" % (VERSION, LEVEL_NAMES[a["level"]], "" if cfg["enabled"] else " (disabled)"))
    if not a["windows"]:
        print("  No limit data yet. Subscription limits appear after the first response once the status line")
        print("  tap is installed (/usage-guard:setup), or via the usage endpoint fallback.")
    for w in a["windows"]:
        rate = ("%.1f%%/h" % (w["rate"] * 60)) if w["rate"] is not None else "n/a"
        eta = fmt_dur(w["eta"]) if w["eta"] is not None else "n/a"
        reset = ("%s (in %s)" % (fmt_clock(w["resets_at"], now), fmt_dur(w["ttr"]))) if w["resets_at"] else "n/a"
        print("  %-14s %5.1f%%  level=%-8s burn=%-9s to-limit=%-7s resets=%s  [%s, %s old]" % (
            w["label"], w["pct"], LEVEL_NAMES[w["level"]], rate, eta, reset, w["src"], fmt_dur(w["age"] / 60.0)))
    sl = state.get("statusline_t")
    print("  status line tap: %s" % ("last seen %s ago" % fmt_dur((now - sl) / 60.0) if sl else "not seen (run /usage-guard:setup)"))
    api = state.get("api") or {}
    if api.get("last_t"):
        print("  usage endpoint: last try %s ago, %s" % (fmt_dur((now - api["last_t"]) / 60.0),
                                                       "ok" if not api.get("err") else "error: " + str(api["err"])))
    print("  thresholds: notice %g%% / wrap-up %g%% / critical %g%%; hand-off file: %s" % (
        cfg["notice_pct"], cfg["wrapup_pct"], cfg["critical_pct"], cfg["handoff_file"]))
    if state.get("limit_hit"):
        print("  last limit hit: %s ago" % fmt_dur((now - state["limit_hit"]["t"]) / 60.0))


def main(argv):
    global _DATA_OVERRIDE
    args = []
    for arg in argv:
        if arg.startswith("--data="):
            _DATA_OVERRIDE = arg.split("=", 1)[1] or None
        else:
            args.append(arg)
    cmd = args[0] if args else "status"
    if cmd == "hook":
        cmd_hook(args[1] if len(args) > 1 else "")
    elif cmd == "statusline":
        cmd_statusline()
    elif cmd == "fetch":
        cmd_fetch()
    elif cmd == "install-statusline" or (cmd == "setup" and "uninstall" not in args[1:]):
        cmd_install_statusline()
    elif cmd in ("uninstall-statusline", "setup"):
        cmd_uninstall_statusline()
    elif cmd == "snapshot":
        print(write_snapshot(os.getcwd(), load_config(os.getcwd()), reason="manual snapshot"))
    else:
        cmd_status()


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:  # never break Claude Code
        if os.environ.get("USAGE_GUARD_DEBUG"):
            raise
        if len(sys.argv) > 1 and sys.argv[1] in ("status", "setup", "install-statusline", "uninstall-statusline", "snapshot"):
            print("usage-guard error: %s" % exc)
    sys.exit(0)
