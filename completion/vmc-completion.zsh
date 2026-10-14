# zsh tab-completion for `vmc` / `vms`: completes REMOTE directories level by
# level (over the warm ssh connection), with a short per-directory cache, and
# seeds suggestions from directories you used before on that target.
# Sourced by zsh/cmux-remote.zsh.

_cmux_remote_cache="${CMUX_REMOTE_CACHE:-$HOME/.cache/cmux-remote}"

# Target named on the command line so far, else the default.
_cmux_vmc_target() {
  emulate -L zsh
  local i
  for (( i = 2; i < CURRENT; i++ )); do
    case "${words[i]}" in
      -t|--target) print -r -- "${words[i+1]}"; return ;;
      --target=*) print -r -- "${words[i]#*=}"; return ;;
    esac
  done
  print -r -- "${CMUX_TARGET_DEFAULT:-ubuntu}"
}

_cmux_vmc_remote_home() {
  emulate -L zsh
  "$CMUX_REMOTE_HOME/bin/cmux-remote" home "$1" 2>/dev/null
}

# List immediate subdirectories of a remote dir, cached ~60s per parent.
_cmux_vmc_list_dirs() {
  emulate -L zsh
  local target="$1" base="$2"
  [[ -z "$base" ]] && base="/"
  mkdir -p "$_cmux_remote_cache/tree-$target" 2>/dev/null
  local key="${base//\//%}"
  local cf="$_cmux_remote_cache/tree-$target/$key"
  local now=$EPOCHSECONDS mtime=0
  [[ -f "$cf" ]] && mtime=$(zstat +mtime "$cf" 2>/dev/null || stat -f %m "$cf" 2>/dev/null || echo 0)
  if [[ ! -f "$cf" || $(( now - mtime )) -gt 60 ]]; then
    "$CMUX_REMOTE_HOME/bin/cmux-remote" exec "$target" "ls -1d ${${base%/}:q}/*/ 2>/dev/null" >| "$cf" 2>/dev/null
  fi
  [[ -f "$cf" ]] && cat "$cf"
}

_vmc() {
  emulate -L zsh
  zmodload -F zsh/stat b:zstat 2>/dev/null
  zmodload zsh/datetime 2>/dev/null
  local cur="${words[CURRENT]}" target="$(_cmux_vmc_target)"

  case "${words[CURRENT-1]}" in
    --agent) compadd claude codex; return ;;
    -t|--target)
      compadd -- $("$CMUX_REMOTE_HOME/bin/cmux-remote" targets 2>/dev/null | awk '{print $1}'); return ;;
  esac
  if [[ "$cur" == -* ]]; then
    compadd -- --claude --codex --agent --resume --target --help
    return
  fi

  # With nothing typed yet the candidates are recents plus the home dirs, and
  # zsh would insert only their common prefix (just "/" once a recent such as
  # /tmp exists), hiding the listing. Show the list instead; the next TAB then
  # opens the menu as usual.
  if [[ -z "$cur" && "$compstate[insert]" == *unambiguous ]]; then
    compstate[insert]=''
    compstate[list]='list force'
  fi

  local base
  if [[ "$cur" == */* ]]; then
    base="${cur%/*}"
    [[ -z "$base" ]] && base="/"
  else
    base="$(_cmux_vmc_remote_home "$target")"
    [[ -z "$base" ]] && base="/"
  fi

  local -a dirs
  dirs=(${(f)"$(_cmux_vmc_list_dirs "$target" "$base")"})
  dirs=(${dirs%/})

  local -a recents
  local hist="$_cmux_remote_cache/history-$target"
  if [[ "$cur" != */* && -f "$hist" ]]; then
    recents=(${(f)"$(tail -r "$hist" 2>/dev/null | awk '!seen[$0]++' | head -20)"})
  fi

  local expl
  if (( ${#recents} )); then
    _description -V vmc-recent expl "recent dirs on $target"
    compadd "${expl[@]}" -Q -S / -- "${recents[@]}"
  fi
  if (( ${#dirs} )); then
    _description -V vmc-dirs expl "directories on $target"
    compadd "${expl[@]}" -Q -S / -- "${dirs[@]}"
  fi
}

compdef _vmc vmc vms
