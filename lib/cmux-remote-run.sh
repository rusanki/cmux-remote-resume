#!/usr/bin/env bash
# Sourced by the interactive remote shell that a launcher or remote-restore
# opens. Records the launch, moves to the saved directory, runs the saved
# agent with an explicit permission policy, and always ends in an interactive
# remote shell so a pane never collapses back to the host.
CMUX_REMOTE_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
export CMUX_REMOTE_ROOT
_cmux_lib="$CMUX_REMOTE_ROOT/lib"
# The launchers accept "~/dir"; the tilde means this machine's home and is
# expanded here, before anything records or uses the directory.
case "${CMUX_REMOTE_CD:-}" in
    '~') CMUX_REMOTE_CD="$HOME" ;;
    '~'/*) CMUX_REMOTE_CD="$HOME${CMUX_REMOTE_CD#\~}" ;;
esac
export CMUX_REMOTE_CD
python3 "$_cmux_lib/cmux-remote-state.py" launch || exit 1
cd "${CMUX_REMOTE_CD:-$HOME}" || {
    python3 "$_cmux_lib/cmux-remote-state.py" failed
    echo "Directory unavailable: $CMUX_REMOTE_CD" >&2
    exec bash -i
}
case "${CMUX_REMOTE_AGENT:-}" in
    claude)
        if [ -n "${CMUX_RESUME_SID:-}" ]; then
            # Claude stores sessions per directory; resume from the directory
            # the transcript records, not from a guessed one.
            realcwd=$(python3 - "$CMUX_RESUME_SID" <<'PY'
import json, pathlib, sys, uuid
sid = str(uuid.UUID(sys.argv[1]))
for p in (pathlib.Path.home()/'.claude/projects').glob('*/' + sid + '.jsonl'):
    with p.open() as f:
        for line in f:
            try: d = json.loads(line)
            except ValueError: continue
            if d.get('cwd'):
                print(d['cwd']); sys.exit()
PY
)
            if [ -n "$realcwd" ]; then
                cd "$realcwd" || {
                    python3 "$_cmux_lib/cmux-remote-state.py" failed
                    echo "Session directory unavailable: $realcwd" >&2
                    exec bash -i
                }
                export CMUX_REMOTE_CD="$PWD"
            fi
        fi
        "$_cmux_lib/cmux-trust-cwd"
        if [ -n "${CMUX_RESUME_SID:-}" ]; then
            "$_cmux_lib/cmux-remote-agent" claude --resume "$CMUX_RESUME_SID"
        else
            "$_cmux_lib/cmux-remote-agent" claude
        fi
        agent_status=$?
        ;;
    codex)
        if [ -n "${CMUX_RESUME_SID:-}" ]; then
            "$_cmux_lib/cmux-remote-agent" codex resume "$CMUX_RESUME_SID"
        else
            "$_cmux_lib/cmux-remote-agent" codex
        fi
        agent_status=$?
        ;;
    '') exec bash -i ;;
    *) echo "Unknown saved agent: $CMUX_REMOTE_AGENT" >&2; exit 2 ;;
esac
if [ "$agent_status" -ne 0 ]; then
    python3 "$_cmux_lib/cmux-remote-state.py" failed
    echo "${CMUX_REMOTE_AGENT} exited ($agent_status). The saved session is retained for the next restore." >&2
fi
export CMUX_REMOTE_CD="$PWD"
exec bash -i
