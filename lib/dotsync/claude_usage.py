"""Ask Claude Code how much of a login's 5-hour and weekly limits is used.

Claude Code answers an undocumented `get_usage` control request on its
stream-json input without sending a prompt, so a probe costs no tokens. A
config folder that never logged in answers (recorded 2026-10-04) with a
success whose `subscription_type` and `rate_limits` are null.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

TIMEOUT_SECONDS = 30
_REQUEST = json.dumps(
    {"type": "control_request", "request_id": "u1", "request": {"subtype": "get_usage"}}
) + "\n"


def probe(claude: str, config_dir: Path | None) -> dict:
    """Usage of the login in `config_dir`, or in Claude's default folder when None."""
    env = dict(os.environ)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if config_dir is not None:
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    cmd = [
        claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
        "--verbose", "--strict-mcp-config", "--disable-slash-commands",
    ]
    try:
        result = subprocess.run(
            cmd, input=_REQUEST, capture_output=True, text=True, env=env, timeout=TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return _error("timeout")
    answer = parse(result.stdout)
    if answer is not None:
        return answer
    if result.returncode != 0:
        return _error(f"claude exited with {result.returncode}")
    return _error("no answer from claude")


def parse(stdout: str) -> dict | None:
    """The usage in a probe's output, or None when it holds no answer."""
    for line in stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("type") != "control_response":
            continue
        response = message.get("response")
        if not isinstance(response, dict) or response.get("subtype") != "success":
            return _error("claude refused the usage request")
        data = response.get("response")
        data = data if isinstance(data, dict) else {}
        if data.get("subscription_type") is None:
            return {"status": "login_required", "five_hour": None, "seven_day": None}
        limits = data.get("rate_limits")
        if not data.get("rate_limits_available") or not isinstance(limits, dict):
            return _error("usage not available")
        return {
            "status": "ok",
            "five_hour": _window(limits.get("five_hour")),
            "seven_day": _window(limits.get("seven_day")),
        }
    return None


def _window(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("utilization")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    return {"percent": min(100, max(0, round(used))), "resets_at": _utc(raw.get("resets_at"))}


def _utc(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error(reason: str) -> dict:
    return {"status": "error", "error": reason, "five_hour": None, "seven_day": None}
