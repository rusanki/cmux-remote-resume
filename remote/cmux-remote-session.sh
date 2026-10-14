#!/usr/bin/env bash
# Claude Code hook entrypoint installed at ~/.claude/cmux-remote-session.sh.
# Errors must never block the agent.
python3 "@ROOT@/lib/cmux-remote-state.py" hook claude "${1:-}" || true
