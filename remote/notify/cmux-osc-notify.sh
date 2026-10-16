#!/usr/bin/env bash
# Claude Code "Notification" hook: forward the message as an OSC 9 notification.
# python3 is already required on the remote; nothing else is needed.
msg=$(python3 -c '
import json, sys
try:
    message = json.load(sys.stdin).get("message") or ""
except Exception:
    message = ""
print("".join(ch for ch in str(message) if ch >= " "))
' 2>/dev/null)
[ -z "$msg" ] && msg="needs your input"
exec bash "$HOME/.claude/cmux-osc-emit.sh" "cmux: $msg"
