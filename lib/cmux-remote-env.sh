# Shared configuration loader. Sourced by every host launcher (bash) and by the
# zsh integration; written so both shells can read it.
#
# Sets CMUX_REMOTE_HOME (this checkout), reads ~/.config/cmux-remote/config for
# keys that are not already in the environment, and defines
# cmux_target_resolve, which turns a target name into backend/address/root.
#
# Targets:  CMUX_TARGET_<name>=<backend>:<address>[:<remote-root>]
#   backend  lima   address is the Lima instance name
#            ssh    address is an ssh destination or ~/.ssh/config alias
#   root     where this repository lives on the remote. Default for lima is
#            this checkout's own path (Lima's home mount), for ssh it is
#            ~/.cmux-remote-resume (install.sh syncs it there).
# A name with no CMUX_TARGET_<name> line means lima:<name>; the readiness
# helper refuses to start (and so to create) a Lima instance that does not exist.
if [ -z "${CMUX_REMOTE_HOME:-}" ]; then
    # shellcheck disable=SC2296  # the inner expansion is zsh's, reached only there
    _cmux_self="${BASH_SOURCE[0]:-${(%):-%x}}"
    CMUX_REMOTE_HOME=$(cd "$(dirname "$_cmux_self")/.." && pwd -P)
    unset _cmux_self
fi
export CMUX_REMOTE_HOME
: "${CMUX_REMOTE_CONFIG:=$HOME/.config/cmux-remote/config}"
if [ -f "$CMUX_REMOTE_CONFIG" ]; then
    while IFS= read -r _cmux_line || [ -n "$_cmux_line" ]; do
        case "$_cmux_line" in ''|'#'*) continue ;; esac
        _cmux_key=${_cmux_line%%=*}
        _cmux_val=${_cmux_line#*=}
        case "$_cmux_key" in
            CMUX_*) case "$_cmux_key" in *[!A-Za-z0-9_]*) continue ;; esac ;;
            *) continue ;;
        esac
        _cmux_val=${_cmux_val%\"}; _cmux_val=${_cmux_val#\"}
        _cmux_val=${_cmux_val%\'}; _cmux_val=${_cmux_val#\'}
        if eval "[ -z \"\${$_cmux_key+set}\" ]"; then
            eval "export $_cmux_key=\"\$_cmux_val\""
        fi
    done < "$CMUX_REMOTE_CONFIG"
    unset _cmux_line _cmux_key _cmux_val
fi
: "${CMUX_TARGET_DEFAULT:=ubuntu}"
: "${CMUX_AGENT_MODE:=default}"
: "${CMUX_CLI:=${CMUX_BUNDLED_CLI_PATH:-/Applications/cmux.app/Contents/Resources/bin/cmux}}"
: "${CMUX_SSH_OPTS:=}"
: "${CMUX_REMOTE_CACHE:=$HOME/.cache/cmux-remote}"
export CMUX_TARGET_DEFAULT CMUX_AGENT_MODE CMUX_CLI CMUX_SSH_OPTS CMUX_REMOTE_CACHE

# cmux_target_resolve NAME -> sets T_NAME T_BACKEND T_ADDR T_ROOT; returns 1 if invalid.
cmux_target_resolve() {
    T_NAME="$1"
    case "$T_NAME" in ''|*[!A-Za-z0-9_-]*) echo "cmux-remote: invalid target name '$T_NAME'" >&2; return 1 ;; esac
    _cmux_var="CMUX_TARGET_$(printf '%s' "$T_NAME" | tr -- '-' '_')"
    eval "_cmux_spec=\${$_cmux_var:-}"
    [ -n "$_cmux_spec" ] || _cmux_spec="lima:$T_NAME"
    T_BACKEND=${_cmux_spec%%:*}
    _cmux_rest=${_cmux_spec#*:}
    case "$_cmux_rest" in
        *:*) T_ADDR=${_cmux_rest%%:*}; T_ROOT=${_cmux_rest#*:} ;;
        *) T_ADDR=$_cmux_rest; T_ROOT='' ;;
    esac
    unset _cmux_var _cmux_spec _cmux_rest
    # shellcheck disable=SC2088  # the ssh default is a literal ~, expanded on the remote by cmux_remote_quote
    case "$T_BACKEND" in
        lima) [ -n "$T_ROOT" ] || T_ROOT="$CMUX_REMOTE_HOME" ;;
        ssh) [ -n "$T_ROOT" ] || T_ROOT='~/.cmux-remote-resume' ;;
        *) echo "cmux-remote: target '$T_NAME' has unknown backend '$T_BACKEND' (use lima or ssh)" >&2; return 1 ;;
    esac
    [ -n "$T_ADDR" ] || { echo "cmux-remote: target '$T_NAME' has no address" >&2; return 1; }
    return 0
}
