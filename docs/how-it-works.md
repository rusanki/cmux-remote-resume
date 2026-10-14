# How it works

## The two halves

**cmux side.** cmux persists, per terminal surface, an optional "resume
binding": a command to run when that surface is restored. Bindings only run
automatically if the command was approved once (cmux signs the approval
record). `vmc` and `vms` register `bin/vm-restore` as the binding for their
pane via `cmux surface resume set --kind vm --source vmc`. The `vmc` source is
what routes the first use through cmux's approval prompt; the same approval
then covers every pane that uses the command.

**Guest side.** The guest never knows about cmux. It knows the pane id,
passed in as `CMUX_SURFACE_ID`, and it has hooks:

- Claude Code: `SessionStart`, `Stop`, `SessionEnd` run
  `~/.claude/cmux-vm-session.sh`, which calls `lib/cmux-vm-state.py hook claude`.
- Codex: `SessionStart`, `UserPromptSubmit`, `Stop` in `~/.codex/hooks.json`
  call `lib/cmux-vm-state.py hook codex`. Codex does not emit `SessionStart`
  for an explicit `codex resume <uuid>`, so `lib/cmux-codex` wraps the
  binary and registers that case itself.
- The guest `~/.bashrc` block adds `_cmux_track` to `PROMPT_COMMAND`, which
  writes a plain "shell in directory X" record on every prompt (and marks the
  previous record failed if the last command exited non-zero).

All of them write `state/sessions/<pane-uuid>.json` atomically under a lock.
The repo is mounted into the VM, so the host reads the same file.

## The record

```json
{"agent": "claude", "session_id": "…", "cwd": "/home/me/project",
 "event": "SessionStart", "surface_id": "…", "vm_name": "ubuntu",
 "version": 2, "updated_at": 1790258905.25}
```

Rules that keep it correct:

- A `Stop` or `SessionEnd` for a session id that is not the current one is
  ignored, so a stale process cannot overwrite a newer session.
- `SessionEnd` with reason `prompt_input_exit` or `logout` (the user ended it)
  downgrades the record to a shell record. Any other reason keeps it.
- A non-zero agent exit sets `preserve_on_prompt`, so the following shell
  prompt does not erase the session. An explicit new `vmc` launch resets it.
- Claude stores transcripts per directory. On resume, `lib/cmux-vm-run.sh`
  reads the transcript's own `cwd` and changes to it before running
  `claude --resume`, instead of trusting the saved cwd.

## Restore sequence

1. cmux restores the layout and types `bin/vm-restore` into each VM pane.
2. `vm-restore` reads the pane's record (`cmux-vm-state.py read-env`).
3. `lib/cmux-vm-ready.py` takes a per-VM lock. If the VM is not running it
   first removes Lima pid/socket files older than the host boot, starts the
   VM (once, other panes wait on the lock), then probes
   `limactl shell … test -r lib/cmux-vm-run.sh` with backoff until the guest
   answers and the mount is visible. Readiness is cached per host-agent pid.
4. `limactl shell` opens an interactive bash that sources
   `lib/cmux-vm-run.sh` with the record's values in the environment.
5. The runner `cd`s, runs the agent through `lib/cmux-vm-agent` (which applies
   `CMUX_VM_AGENT_MODE`), and when the agent exits for any reason execs an
   interactive VM shell. The pane therefore never collapses back to a host
   shell, which is what used to happen with `exec claude || exec claude`.
6. If ssh itself fails (exit 255) within 30 seconds, `vm-restore` retries up
   to three times, then prints a message and waits for Enter to retry.

## Why not tmux

An earlier version of this setup kept agents alive in guest tmux sessions and
reattached. It survived cmux restarts but not reboots, it needed a tag store
to map panes to windows, and the reattach path broke in ways that were hard
to see. Persisting the conversation (the transcript) rather than the process
is simpler and survives everything except an in-flight generation.
