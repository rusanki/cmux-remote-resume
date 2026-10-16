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

_cmux_vmc_targets() {
  emulate -L zsh
  "$CMUX_REMOTE_HOME/bin/cmux-remote" targets 2>/dev/null | awk '{print $1}'
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
  if [[ -f "$cf" ]]; then
    # zsh/stat first; then GNU stat, then BSD stat.
    mtime=$(zstat +mtime "$cf" 2>/dev/null || stat -c %Y "$cf" 2>/dev/null || stat -f %m "$cf" 2>/dev/null)
    [[ "$mtime" == <-> ]] || mtime=0
  fi
  if [[ ! -f "$cf" || $(( now - mtime )) -gt 60 ]]; then
    "$CMUX_REMOTE_HOME/bin/cmux-remote" exec "$target" "ls -1d ${${base%/}:q}/*/ 2>/dev/null" >| "$cf" 2>/dev/null
  fi
  [[ -f "$cf" ]] && cat "$cf"
}

# _cmux_vmc_add TAG DESCRIPTION WORD...: one completion group. Words are
# quoted by compadd as usual (a directory with a space inserts escaped), except
# "~"-prefixed ones, which go in verbatim so the tilde reaches the launcher and
# expands on the remote.
_cmux_vmc_add() {
  emulate -L zsh
  local tag="$1" desc="$2"; shift 2
  local -a expl plain tilde
  plain=(${@:#\~*}); tilde=(${(M)@:#\~*})
  _description -V "$tag" expl "$desc"
  (( ${#plain} )) && compadd "${expl[@]}" -S / -- "${plain[@]}"
  (( ${#tilde} )) && compadd "${expl[@]}" -Q -S / -- "${tilde[@]}"
}

_vmc() {
  emulate -L zsh
  zmodload -F zsh/stat b:zstat 2>/dev/null
  zmodload zsh/datetime 2>/dev/null
  local cur="${words[CURRENT]}" target="$(_cmux_vmc_target)"

  case "${words[CURRENT-1]}" in
    --agent) compadd claude codex; return ;;
    -r|--resume) return ;;                       # a session UUID, nothing to offer
    -t|--target) compadd -- $(_cmux_vmc_targets); return ;;
  esac
  case "$cur" in
    --target=*) compadd -P '--target=' -- $(_cmux_vmc_targets); return ;;
    --resume=*) return ;;
    -*) compadd -- --claude --codex --agent --resume --target --help; return ;;
  esac

  # "~" and "~/..." mean the remote home: list there, show the results in
  # tilde form so they match what was typed.
  local base home
  if [[ "$cur" == */* ]]; then
    base="${cur%/*}"
    [[ -z "$base" ]] && base="/"
  else
    base="$(_cmux_vmc_remote_home "$target")"
    [[ -z "$base" ]] && base="/"
  fi
  if [[ "$base" == '~' || "$base" == '~/'* ]]; then
    home="$(_cmux_vmc_remote_home "$target")"
    [[ -n "$home" ]] && base="${home%/}${base#\~}"
  fi

  local -a dirs
  dirs=(${(f)"$(_cmux_vmc_list_dirs "$target" "$base")"})
  dirs=(${dirs%/})
  [[ -n "$home" ]] && dirs=(${dirs/#${(b)home}/\~})

  local -a recents
  local hist="$_cmux_remote_cache/history-$target"
  if [[ "$cur" != */* && -f "$hist" ]]; then
    recents=(${(f)"$(<$hist)"})
    recents=(${(Oa)recents})        # newest first ...
    recents=(${(u)recents})         # ... keeping the first occurrence
    recents=(${recents[1,20]})
  fi

  (( ${#recents} )) && _cmux_vmc_add vmc-recent "recent dirs on $target" "${recents[@]}"
  (( ${#dirs} )) && _cmux_vmc_add vmc-dirs "directories on $target" "${dirs[@]}"

  # With nothing typed and recents present, the common prefix of recents and
  # home directories is usually just "/", and inserting it would hide the list.
  # Force the listing instead (written here, after the last _description: its
  # _setup resets compstate[list], so writing it earlier has no effect) and
  # skip the insertion unless the menu style wants the menu right away.
  if [[ -z "$cur" ]] && (( ${#recents} )) && (( ${#recents} + ${#dirs} > 1 )); then
    compstate[list]='list force'
    [[ -n "$_menu_style[(r)(yes|true|on|select*)]" ]] || compstate[insert]=''
  fi
}

compdef _vmc vmc vms
