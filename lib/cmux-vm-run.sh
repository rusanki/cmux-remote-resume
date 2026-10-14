#!/usr/bin/env bash
# Sourced by the guest shell that a launcher or vm-restore opens. Records the
# launch, moves to the saved directory, runs the saved agent with an explicit
# permission policy, and always ends in an interactive VM shell so a pane never
# collapses back to the host.
CMUX_LIMA_GUEST_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
export CMUX_VM_GUEST_ROOT="${CMUX_VM_GUEST_ROOT:-$CMUX_LIMA_GUEST_ROOT}"
_cmux_lib="$CMUX_LIMA_GUEST_ROOT/lib"
python3 "$_cmux_lib/cmux-vm-state.py" launch || exit 1
cd "${CMUX_VM_CD:-$HOME}" || {
    python3 "$_cmux_lib/cmux-vm-state.py" failed
    echo "VM directory unavailable: $CMUX_VM_CD" >&2
    exec bash -i
}
case "${CMUX_VM_AGENT:-}" in
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
                    python3 "$_cmux_lib/cmux-vm-state.py" failed
                    echo "Session directory unavailable: $realcwd" >&2
                    exec bash -i
                }
                export CMUX_VM_CD="$PWD"
            fi
        fi
        "$_cmux_lib/cmux-trust-cwd"
        if [ -n "${CMUX_RESUME_SID:-}" ]; then
            "$_cmux_lib/cmux-vm-agent" claude --resume "$CMUX_RESUME_SID"
        else
            "$_cmux_lib/cmux-vm-agent" claude
        fi
        agent_status=$?
        ;;
    codex)
        if [ -n "${CMUX_RESUME_SID:-}" ]; then
            "$_cmux_lib/cmux-vm-agent" codex resume "$CMUX_RESUME_SID"
        else
            "$_cmux_lib/cmux-vm-agent" codex
        fi
        agent_status=$?
        ;;
    '') exec bash -i ;;
    *) echo "Unknown saved agent: $CMUX_VM_AGENT" >&2; exit 2 ;;
esac
if [ "$agent_status" -ne 0 ]; then
    python3 "$_cmux_lib/cmux-vm-state.py" failed
    echo "${CMUX_VM_AGENT} exited ($agent_status). The saved session is retained for the next restore." >&2
fi
export CMUX_VM_CD="$PWD"
exec bash -i
