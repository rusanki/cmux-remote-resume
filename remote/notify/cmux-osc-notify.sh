#!/usr/bin/env bash
msg=$(jq -r '.message // "needs your input"' 2>/dev/null | tr -d '\000-\037')
[ -z "$msg" ] && msg="needs your input"
exec bash "$HOME/.claude/cmux-osc-emit.sh" "cmux: $msg"
