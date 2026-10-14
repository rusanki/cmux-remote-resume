#!/usr/bin/env bash
# Claude Code hook entrypoint installed at ~/.claude/cmux-vm-session.sh.
# Errors must never block the agent.
python3 "@ROOT@/lib/cmux-vm-state.py" hook claude "${1:-}" || true
