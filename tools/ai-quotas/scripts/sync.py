#!/usr/bin/env python3
"""
ai-quotas-sync: Fetch live quota and rate limit status from local authenticated
AI providers (Claude Code, OpenAI Codex/ChatGPT, OpenCode Go, DeepSeek) and save records to $XDG_STATE_HOME/ai-quotas/
following the ai-quotas contract.
"""
import json
import os
import sys
import datetime
import urllib.request
import urllib.error

def get_state_dir():
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and xdg.strip():
        return os.path.join(xdg.strip(), "ai-quotas")
    home = os.environ.get("HOME", os.path.expanduser("~"))
    return os.path.join(home, ".local", "state", "ai-quotas")

def fetch_claude(quota_dir, now_iso):
    home = os.environ.get("HOME", os.path.expanduser("~"))
    creds_path = os.path.join(home, ".claude", ".credentials.json")
    if not os.path.exists(creds_path):
        return {"provider": "claude", "status": "skipped", "reason": "no credentials at ~/.claude/.credentials.json"}

    try:
        with open(creds_path, "r", encoding="utf-8") as f:
            creds = json.load(f)
        token = creds.get("claudeAiOauth", {}).get("accessToken")
        if not token:
            return {"provider": "claude", "status": "error", "reason": "no accessToken in claudeAiOauth"}

        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-version": "2023-06-01",
                "User-Agent": "claude-code/2.1.224",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        written = []
        # 5-hour window
        five_h = data.get("five_hour")
        if five_h and five_h.get("utilization") is not None:
            record_5h = {
                "provider": "claude",
                "kind": "window",
                "used": round(float(five_h["utilization"]), 2),
                "limit": 100,
                "unit": "percent",
                "label": "5h window",
                "display_name": "Claude Pro",
                "resets_at": five_h.get("resets_at"),
                "fetched_at": now_iso,
                "source": "local-log",
            }
            path_5h = os.path.join(quota_dir, "claude-5h.json")
            with open(path_5h, "w", encoding="utf-8") as f:
                json.dump(record_5h, f, indent=2)
            written.append("claude-5h.json")

        # 7-day / weekly window
        seven_d = data.get("seven_day")
        if seven_d and seven_d.get("utilization") is not None:
            record_weekly = {
                "provider": "claude",
                "kind": "window",
                "used": round(float(seven_d["utilization"]), 2),
                "limit": 100,
                "unit": "percent",
                "label": "Weekly",
                "display_name": "Claude Pro",
                "resets_at": seven_d.get("resets_at"),
                "fetched_at": now_iso,
                "source": "local-log",
            }
            path_weekly = os.path.join(quota_dir, "claude-weekly.json")
            with open(path_weekly, "w", encoding="utf-8") as f:
                json.dump(record_weekly, f, indent=2)
            written.append("claude-weekly.json")

        return {"provider": "claude", "status": "ok", "written": written, "data": data}
    except Exception as e:
        return {"provider": "claude", "status": "error", "reason": str(e)}

def fetch_codex(quota_dir, now_iso):
    home = os.environ.get("HOME", os.path.expanduser("~"))
    auth_path = os.path.join(home, ".codex", "auth.json")
    if not os.path.exists(auth_path):
        return {"provider": "chatgpt", "status": "skipped", "reason": "no credentials at ~/.codex/auth.json"}

    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            auth = json.load(f)
        tokens = auth.get("tokens", {})
        token = tokens.get("access_token")
        if not token:
            return {"provider": "chatgpt", "status": "error", "reason": "no access_token in tokens"}

        account_id = tokens.get("account_id")
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "codex-cli/0.144.6",
            "Accept": "application/json",
        }
        if account_id:
            headers["chatgpt-account-id"] = account_id

        req = urllib.request.Request(
            "https://chatgpt.com/backend-api/wham/usage",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        written = []
        display_name = "ChatGPT Plus"
        plan = data.get("plan_type")
        if plan and "pro" in plan.lower():
            display_name = "ChatGPT Pro"

        rate_limit = data.get("rate_limit", {})
        primary = rate_limit.get("primary_window")
        if primary:
            used = primary.get("used_percent", 0)
            reset_at_ts = primary.get("reset_at")
            resets_at_iso = None
            if reset_at_ts:
                resets_at_iso = datetime.datetime.fromtimestamp(reset_at_ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            label = "Weekly" if primary.get("duration_seconds", 0) >= 86400 * 5 else "Window"
            filename = "chatgpt-weekly.json" if label == "Weekly" else "chatgpt.json"
            record = {
                "provider": "chatgpt",
                "kind": "window",
                "used": round(float(used), 2),
                "limit": 100,
                "unit": "percent",
                "label": label,
                "display_name": display_name,
                "resets_at": resets_at_iso,
                "fetched_at": now_iso,
                "source": "local-log",
            }
            with open(os.path.join(quota_dir, filename), "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            written.append(filename)

        secondary = rate_limit.get("secondary_window")
        if secondary:
            used = secondary.get("used_percent", 0)
            reset_at_ts = secondary.get("reset_at")
            resets_at_iso = None
            if reset_at_ts:
                resets_at_iso = datetime.datetime.fromtimestamp(reset_at_ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            record_sec = {
                "provider": "chatgpt",
                "kind": "window",
                "used": round(float(used), 2),
                "limit": 100,
                "unit": "percent",
                "label": "5h window",
                "display_name": display_name,
                "resets_at": resets_at_iso,
                "fetched_at": now_iso,
                "source": "local-log",
            }
            with open(os.path.join(quota_dir, "chatgpt-5h.json"), "w", encoding="utf-8") as f:
                json.dump(record_sec, f, indent=2)
            written.append("chatgpt-5h.json")

        return {"provider": "chatgpt", "status": "ok", "written": written, "data": data}
    except Exception as e:
        return {"provider": "chatgpt", "status": "error", "reason": str(e)}

def fetch_opencode(quota_dir, now_iso):
    home = os.environ.get("HOME", os.path.expanduser("~"))
    auth_path = os.path.join(home, ".local", "share", "opencode", "auth.json")
    if not os.path.exists(auth_path):
        return {"provider": "opencode", "status": "skipped", "reason": "no credentials at ~/.local/share/opencode/auth.json"}

    try:
        with open(auth_path, "r", encoding="utf-8") as f:
            auth = json.load(f)
        key = auth.get("opencode-go", {}).get("key") or auth.get("opencode", {}).get("key")
        if not key:
            return {"provider": "opencode", "status": "skipped", "reason": "no opencode-go key in auth.json"}

        req = urllib.request.Request(
            "https://opencode.ai/zen/go/v1/usage",
            headers={
                "Authorization": f"Bearer {key}",
                "User-Agent": "opencode/1.18.18",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        written = []
        usage = data.get("usage", {})
        windows = [
            ("5h window", "opencode-5h.json", usage.get("rolling")),
            ("Weekly", "opencode-weekly.json", usage.get("weekly")),
            ("Monthly", "opencode-monthly.json", usage.get("monthly")),
        ]
        for label, filename, win in windows:
            if win and win.get("percent") is not None:
                record = {
                    "provider": "opencode",
                    "kind": "window",
                    "used": float(win["percent"]),
                    "limit": 100.0,
                    "unit": "percent",
                    "label": label,
                    "display_name": "OpenCode Go",
                    "resets_at": win.get("resetsAt"),
                    "fetched_at": now_iso,
                    "source": "local-log",
                }
                with open(os.path.join(quota_dir, filename), "w", encoding="utf-8") as f:
                    json.dump(record, f, indent=2)
                written.append(filename)

        return {"provider": "opencode", "status": "ok", "written": written, "data": data}
    except Exception as e:
        return {"provider": "opencode", "status": "error", "reason": str(e)}

def fetch_deepseek(quota_dir, now_iso):
    home = os.environ.get("HOME", os.path.expanduser("~"))
    auth_path = os.path.join(home, ".local", "share", "opencode", "auth.json")
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key and os.path.exists(auth_path):
        with open(auth_path, "r", encoding="utf-8") as f:
            auth = json.load(f)
        ds = auth.get("deepseek", {})
        key = ds.get("key") if isinstance(ds, dict) else ds

    if not key:
        return {"provider": "deepseek", "status": "skipped", "reason": "no credentials"}

    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        written = []
        infos = data.get("balance_infos", [])
        if infos:
            usd_info = next((i for i in infos if i.get("currency") == "USD"), infos[0])
            cur = usd_info.get("currency", "USD")
            balance = float(usd_info.get("total_balance", 0))
            record = {
                "provider": "deepseek",
                "kind": "balance",
                "limit": balance,
                "currency": cur,
                "unit": "currency",
                "display_name": "DeepSeek API",
                "fetched_at": now_iso,
                "source": "local-log",
            }
            path = os.path.join(quota_dir, "deepseek.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            written.append("deepseek.json")

        return {"provider": "deepseek", "status": "ok", "written": written, "data": data}
    except Exception as e:
        return {"provider": "deepseek", "status": "error", "reason": str(e)}

def main():
    quota_dir = get_state_dir()
    os.makedirs(quota_dir, exist_ok=True)
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    claude_res = fetch_claude(quota_dir, now_iso)
    codex_res = fetch_codex(quota_dir, now_iso)
    opencode_res = fetch_opencode(quota_dir, now_iso)
    deepseek_res = fetch_deepseek(quota_dir, now_iso)

    print(f"ai-quotas state dir: {quota_dir}")
    print(f"Claude: {claude_res['status']} ({', '.join(claude_res.get('written', [])) if claude_res.get('written') else claude_res.get('reason')})")
    print(f"Codex / ChatGPT: {codex_res['status']} ({', '.join(codex_res.get('written', [])) if codex_res.get('written') else codex_res.get('reason')})")
    print(f"OpenCode Go: {opencode_res['status']} ({', '.join(opencode_res.get('written', [])) if opencode_res.get('written') else opencode_res.get('reason')})")
    print(f"DeepSeek: {deepseek_res['status']} ({', '.join(deepseek_res.get('written', [])) if deepseek_res.get('written') else deepseek_res.get('reason')})")

if __name__ == "__main__":
    main()
