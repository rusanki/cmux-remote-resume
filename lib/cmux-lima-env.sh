# Shared configuration loader. Sourced by every host launcher (bash) and by the
# zsh integration. Sets CMUX_LIMA_ROOT (this checkout), reads
# ~/.config/cmux-lima/config for keys that are not already in the environment,
# and exports the derived paths the Python helpers rely on.
#
# Callers set CMUX_LIMA_ROOT before sourcing when they know it; otherwise it is
# derived from this file's location.
if [ -z "${CMUX_LIMA_ROOT:-}" ]; then
    _cmux_lima_self="${BASH_SOURCE[0]:-${(%):-%x}}"
    CMUX_LIMA_ROOT=$(cd "$(dirname "$_cmux_lima_self")/.." && pwd -P)
    unset _cmux_lima_self
fi
export CMUX_LIMA_ROOT
: "${CMUX_LIMA_CONFIG:=$HOME/.config/cmux-lima/config}"
if [ -f "$CMUX_LIMA_CONFIG" ]; then
    while IFS= read -r _cmux_line || [ -n "$_cmux_line" ]; do
        case "$_cmux_line" in ''|'#'*) continue ;; esac
        _cmux_key=${_cmux_line%%=*}
        _cmux_val=${_cmux_line#*=}
        case "$_cmux_key" in
            CMUX_[A-Z0-9_]*) ;;
            *) continue ;;
        esac
        _cmux_val=${_cmux_val%\"}; _cmux_val=${_cmux_val#\"}
        _cmux_val=${_cmux_val%\'}; _cmux_val=${_cmux_val#\'}
        if eval "[ -z \"\${$_cmux_key+set}\" ]"; then
            eval "export $_cmux_key=\"\$_cmux_val\""
        fi
    done < "$CMUX_LIMA_CONFIG"
    unset _cmux_line _cmux_key _cmux_val
fi
: "${CMUX_VM_NAME:=ubuntu}"
: "${CMUX_VM_GUEST_ROOT:=$CMUX_LIMA_ROOT}"
: "${CMUX_VM_AGENT_MODE:=default}"
: "${CMUX_CLI:=${CMUX_BUNDLED_CLI_PATH:-/Applications/cmux.app/Contents/Resources/bin/cmux}}"
: "${CMUX_VM_PROBE_PATH:=$CMUX_VM_GUEST_ROOT/lib/cmux-vm-run.sh}"
export CMUX_VM_NAME CMUX_VM_GUEST_ROOT CMUX_VM_AGENT_MODE CMUX_CLI CMUX_VM_PROBE_PATH
