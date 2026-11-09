#!/usr/bin/env python3
"""Job finish/fail notifications for Trips Local AI.
Config: ~/localai/gui/notify.json (untracked). See notify.example.json.
Telegram: needs a Bot API token + chat_id (the inbound Telegram bridge cannot send).
Email: optional local sendmail/postfix fallback when enabled.
"""
from __future__ import annotations
import json, subprocess, urllib.request, urllib.parse
from pathlib import Path

GUI = Path.home() / "localai" / "gui"
CFG = GUI / "notify.json"
EXAMPLE = GUI / "notify.example.json"

def load_cfg():
    if not CFG.exists():
        return json.loads(EXAMPLE.read_text()) if EXAMPLE.exists() else {"enabled": False}
    return json.load(open(CFG))

def status():
    c = load_cfg()
    tg = c.get("telegram") or {}
    em = c.get("email") or {}
    return {
        "enabled": bool(c.get("enabled")),
        "telegram_enabled": bool(tg.get("enabled")),
        "email_enabled": bool(em.get("enabled")),
        "telegram_configured": bool(tg.get("bot_token") and tg.get("chat_id")),
        "email_configured": bool(em.get("to")),
        "config_path": str(CFG),
        "example_path": str(EXAMPLE),
        "notes": [
            "The inbound Telegram bridge only receives messages; it cannot send.",
            "To enable Telegram alerts, put a BotFather bot_token and your chat_id in notify.json.",
            "Email uses local sendmail when enabled; postfix on this Mac is loopback-only unless a relay is configured.",
        ],
    }

def _send_telegram(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3500], "disable_web_page_preview": "1"}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def _send_email(to: str, subject: str, body: str, frm: str):
    msg = f"From: {frm}\nTo: {to}\nSubject: {subject}\nContent-Type: text/plain; charset=utf-8\n\n{body}\n"
    p = subprocess.run(["/usr/sbin/sendmail", "-t", "-i"], input=msg.encode(), capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode()[:300] or f"sendmail rc={p.returncode}")
    return {"ok": True}

def notify_job(job: dict):
    c = load_cfg()
    if not c.get("enabled"):
        return {"skipped": "disabled"}
    status = job.get("status") or "?"
    name = job.get("folder_name") or job.get("folder") or job.get("id")
    passes = ", ".join(job.get("passes") or [])
    err = job.get("error") or ""
    lines = [
        f"Trips Local AI — job {status}",
        f"Shoot: {name}",
        f"Passes: {passes or '—'}",
        f"Id: {job.get('id')}",
    ]
    if job.get("verify"):
        lines.append(f"Verify ratings/colours changed: {job['verify'].get('rating_or_colour_changed')}")
    pr = job.get("pass_results") or {}
    if pr:
        bits = []
        for k, v in pr.items():
            a = (v or {}).get("apply") or {}
            bits.append(f"{k}:{a.get('applied_keywords', a.get('skipped', 'ok'))}")
        lines.append("Results: " + "; ".join(bits)[:500])
    if err:
        lines.append(f"Error: {err[:400]}")
    text = "\n".join(lines)
    out = {}
    tg = c.get("telegram") or {}
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        try:
            out["telegram"] = _send_telegram(tg["bot_token"], str(tg["chat_id"]), text)
        except Exception as e:
            out["telegram_error"] = str(e)
    em = c.get("email") or {}
    if em.get("enabled") and em.get("to"):
        try:
            out["email"] = _send_email(em["to"], f"Trips Local AI: {status} — {name}", text, em.get("from") or "trips-local-ai@localhost")
        except Exception as e:
            out["email_error"] = str(e)
    if not out:
        out["skipped"] = "no channel configured"
    return out
