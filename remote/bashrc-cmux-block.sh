# >>> CMUX-REMOTE-SHELL >>>
# Installed by cmux-remote-resume (remote/install-remote.py). Active only in
# shells opened from a cmux pane. Keeps the pane's directory record current
# and routes `codex` through the resume-aware wrapper, preserving your options.
if [ -n "${CMUX_SURFACE_ID:-}" ]; then
    CMUX_REMOTE_ROOT="${CMUX_REMOTE_ROOT:-@ROOT@}"
    if [ -n "${CMUX_REMOTE_CD:-}" ]; then cd "$CMUX_REMOTE_CD" 2>/dev/null; fi
    if alias codex >/dev/null 2>&1; then
        _cmux_alias=$(alias codex)
        _cmux_alias=${_cmux_alias#alias codex=}
        _cmux_alias=${_cmux_alias#\'}; _cmux_alias=${_cmux_alias%\'}
        _cmux_rest=${_cmux_alias#* }
        [ "$_cmux_rest" = "$_cmux_alias" ] && _cmux_rest=''
        alias codex="$CMUX_REMOTE_ROOT/lib/cmux-codex${_cmux_rest:+ $_cmux_rest}"
        unset _cmux_alias _cmux_rest
    else
        alias codex="$CMUX_REMOTE_ROOT/lib/cmux-codex"
    fi
    _cmux_track() {
        local previous_status=$?
        if [ "$previous_status" -ne 0 ]; then
            python3 "$CMUX_REMOTE_ROOT/lib/cmux-remote-state.py" failed
        fi
        python3 "$CMUX_REMOTE_ROOT/lib/cmux-remote-state.py" shell "$PWD"
    }
    case ";${PROMPT_COMMAND:-};" in
        *";_cmux_track;"*) : ;;
        *) PROMPT_COMMAND="_cmux_track${PROMPT_COMMAND:+;${PROMPT_COMMAND}}" ;;
    esac
fi
# <<< CMUX-REMOTE-SHELL <<<
