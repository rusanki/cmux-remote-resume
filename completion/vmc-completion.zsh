# zsh tab-completion for `vmc` / `vms`: completes GUEST (Lima VM) directories
# level by level over the warm ssh ControlMaster, with a short per-directory
# cache, and seeds suggestions from directories you used before.
# Sourced by zsh/cmux-lima.zsh.

_cmux_vm_name() { print -r -- "${CMUX_VM_NAME:-ubuntu}"; }
_cmux_vmc_cache_dir="$HOME/.cache/cmux-vmc"
_cmux_vmc_history="$_cmux_vmc_cache_dir/history"

# The guest home directory, resolved once per VM and cached.
_cmux_vmc_guest_home() {
  emulate -L zsh
  local vm="$(_cmux_vm_name)" cf="$_cmux_vmc_cache_dir/home-${(%):-%n}-$vm"
  mkdir -p "$_cmux_vmc_cache_dir" 2>/dev/null
  if [[ ! -s "$cf" ]]; then
    limactl shell --workdir / "$vm" -- sh -c 'printf %s "$HOME"' >| "$cf" 2>/dev/null || return 1
  fi
  cat "$cf"
}

# List immediate subdirectories of a guest dir, cached ~60s per parent.
_cmux_vmc_list_dirs() {
  emulate -L zsh
  local base="$1"
  [[ -z "$base" ]] && base="/"
  mkdir -p "$_cmux_vmc_cache_dir/tree" 2>/dev/null
  local key="${base//\//%}"
  local cf="$_cmux_vmc_cache_dir/tree/$key"
  local now=$EPOCHSECONDS mtime=0
  [[ -f "$cf" ]] && mtime=$(zstat +mtime "$cf" 2>/dev/null || stat -f %m "$cf" 2>/dev/null || echo 0)
  if [[ ! -f "$cf" || $(( now - mtime )) -gt 60 ]]; then
    limactl shell --workdir / "$(_cmux_vm_name)" -- bash -c "ls -1d ${base:q}/*/ 2>/dev/null" >| "$cf" 2>/dev/null
  fi
  [[ -f "$cf" ]] && cat "$cf"
}

_vmc() {
  emulate -L zsh
  zmodload -F zsh/stat b:zstat 2>/dev/null
  zmodload zsh/datetime 2>/dev/null
  local cur="${words[CURRENT]}"

  if [[ "${words[CURRENT-1]}" == --agent ]]; then
    compadd claude codex
    return
  fi
  if [[ "$cur" == -* ]]; then
    compadd -- --claude --codex --agent --resume --help
    return
  fi

  local base
  if [[ "$cur" == */* ]]; then
    base="${cur%/*}"
    [[ -z "$base" ]] && base="/"
  else
    base="$(_cmux_vmc_guest_home)"
    [[ -z "$base" ]] && base="/"
  fi

  local -a dirs
  dirs=(${(f)"$(_cmux_vmc_list_dirs "$base")"})
  dirs=(${dirs%/})

  local -a recents
  if [[ "$cur" != */* && -f "$_cmux_vmc_history" ]]; then
    recents=(${(f)"$(tail -r "$_cmux_vmc_history" 2>/dev/null | awk '!seen[$0]++' | head -20)"})
  fi

  local expl
  if (( ${#recents} )); then
    _description -V vmc-recent expl 'recent VM dirs'
    compadd "${expl[@]}" -Q -S / -- "${recents[@]}"
  fi
  if (( ${#dirs} )); then
    _description -V vmc-dirs expl 'VM directories'
    compadd "${expl[@]}" -Q -S / -- "${dirs[@]}"
  fi
}

compdef _vmc vmc vms
