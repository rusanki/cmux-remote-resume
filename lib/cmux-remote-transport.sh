# Transport layer (bash). Source after cmux-remote-env.sh. Every function takes
# a target NAME first and resolves it; remote commands are shell snippets run by
# bash on the remote, so '~' in a remote root expands there.
#
#   cmux_remote_exec  NAME SCRIPT            run SCRIPT non-interactively, inherit stdio
#   cmux_remote_shell NAME SCRIPT [K=V ...]  exec an interactive bash running SCRIPT with env
#   cmux_remote_copy  NAME LOCAL REMOTE      copy a file to the remote
#   cmux_remote_home  NAME                   print the remote $HOME (cached)
#   cmux_remote_ready NAME                   start (lima) and wait until the remote answers

# Word-split CMUX_SSH_OPTS into the array named by $1 (bash 3.2 does not split
# inside a `local -a x=(...)` declaration, so this goes through read -a).
_cmux_ssh_opts_into() {
    read -r -a "$1" <<< "${CMUX_SSH_OPTS:-}"
}

cmux_remote_exec() {
    local name="$1" script="$2"
    cmux_target_resolve "$name" || return 1
    case "$T_BACKEND" in
        lima) limactl shell --workdir / "$T_ADDR" -- bash -c "$script" ;;
        ssh)
            local -a opts=(); _cmux_ssh_opts_into opts
            ssh -o BatchMode=yes "${opts[@]+"${opts[@]}"}" "$T_ADDR" -- "bash -c $(printf '%q' "$script")" ;;
    esac
}

cmux_remote_shell() {
    local name="$1" script="$2"; shift 2
    cmux_target_resolve "$name" || return 1
    local -a assignments=("$@")
    case "$T_BACKEND" in
        lima)
            exec limactl shell "$T_ADDR" -- env "${assignments[@]+"${assignments[@]}"}" bash -ic "$script" ;;
        ssh)
            local -a opts=(); _cmux_ssh_opts_into opts
            local remote="env"
            local a; for a in "${assignments[@]+"${assignments[@]}"}"; do remote+=" $(printf '%q' "$a")"; done
            remote+=" bash -ic $(printf '%q' "$script")"
            exec ssh -t "${opts[@]+"${opts[@]}"}" "$T_ADDR" -- "$remote" ;;
    esac
}

cmux_remote_copy() {
    local name="$1" local_path="$2" remote_path="$3"
    cmux_target_resolve "$name" || return 1
    case "$T_BACKEND" in
        lima) limactl copy "$local_path" "$T_ADDR:$remote_path" ;;
        ssh)
            local -a opts=(); _cmux_ssh_opts_into opts
            scp -q "${opts[@]+"${opts[@]}"}" "$local_path" "$T_ADDR:$remote_path" ;;
    esac
}

cmux_remote_home() {
    local name="$1"
    cmux_target_resolve "$name" || return 1
    local cache="$CMUX_REMOTE_CACHE/home-$T_NAME"
    if [ ! -s "$cache" ]; then
        mkdir -p "$CMUX_REMOTE_CACHE"
        cmux_remote_exec "$name" 'printf %s "$HOME"' > "$cache" 2>/dev/null || { rm -f "$cache"; return 1; }
    fi
    cat "$cache"
}

cmux_remote_ready() {
    local name="$1"
    cmux_target_resolve "$name" || return 1
    CMUX_REMOTE_PROBE="test -r $T_ROOT/lib/cmux-remote-run.sh" \
        python3 "$CMUX_REMOTE_HOME/lib/cmux-remote-ready.py" "$T_BACKEND" "$T_ADDR"
}
