# How it works

## The two halves

**cmux side.** cmux persists, per terminal surface, an optional "resume
binding": a command to run when that surface is restored. Bindings only run
automatically if the command was approved once (cmux signs the approval
record). `vmc` and `vms` register `bin/remote-restore` as the binding for
their pane via `cmux surface resume set --kind vm --source vmc`. The `vmc`
source is what routes the first use through cmux's approval prompt; the same
approval then covers every pane that uses the command. They also write the
pane's target name to `~/.cache/cmux-remote/panes/<pane-uuid>` on the host.

**Remote side.** The remote never knows about cmux. It knows the pane id,
passed in as `CMUX_SURFACE_ID`, and it has hooks:

- Claude Code: `SessionStart`, `Stop`, `SessionEnd` run
  `~/.claude/cmux-remote-session.sh`, which calls `lib/cmux-remote-state.py hook claude`.
- Codex: `SessionStart`, `UserPromptSubmit`, `Stop` in `~/.codex/hooks.json`
  call `lib/cmux-remote-state.py hook codex`. Codex does not emit `SessionStart`
  for an explicit `codex resume <uuid>`, so `lib/cmux-codex` wraps the binary
  and registers that case itself.
- The remote `~/.bashrc` block adds `_cmux_track` to `PROMPT_COMMAND`, which
  writes a plain "shell in directory X" record on every prompt (and marks the
  previous record failed if the last command exited non-zero).

All of them write `~/.cache/cmux-remote/sessions/<pane-uuid>.json` on the
remote, atomically under a lock. The host reads it over the transport when a
pane restores.

## Transports

`lib/cmux-remote-transport.sh` exposes five operations, each resolving a
target name to `backend:address:root` from the config:

| Operation | lima | ssh |
|---|---|---|
| exec a snippet | `limactl shell --workdir / <vm> -- bash -c …` | `ssh -o BatchMode=yes $CMUX_SSH_OPTS <dest> -- "bash -c …"` |
| interactive shell with env | `limactl shell <vm> -- env K=V bash -ic …` | `ssh -t … <dest> -- "env K=V bash -ic …"` |
| copy a file | `limactl copy` | `scp` |
| remote `$HOME` | cached once per target | same |
| ready | start if stopped, repair, probe | probe with backoff |

Remote snippets are run by bash on the remote. Roots and directories go
through `cmux_remote_quote`: a leading `~` becomes `"$HOME"` so it expands in
the remote's home, everything else is `printf %q`-escaped, so paths with
spaces work. The ssh path quotes the whole snippet with `printf %q` once
more, which assumes the remote login shell understands backslash escapes
(bash, zsh, sh all do).

## The record

```json
{"agent": "claude", "session_id": "…", "cwd": "/home/me/project",
 "event": "SessionStart", "surface_id": "…", "target": "ubuntu",
 "version": 3, "updated_at": 1790258905.25}
```

Rules that keep it correct:

- A `Stop` or `SessionEnd` for a session id that is not the current one is
  ignored, so a stale process cannot overwrite a newer session.
- `SessionEnd` with reason `prompt_input_exit` or `logout` (the user ended it)
  downgrades the record to a shell record. Any other reason keeps it.
- A non-zero agent exit sets `preserve_on_prompt`, so the following shell
  prompt does not erase the session. An explicit new `vmc` launch resets it.
- Claude stores transcripts per directory. On resume, `lib/cmux-remote-run.sh`
  reads the transcript's own `cwd` and changes to it before running
  `claude --resume`, instead of trusting the saved cwd.

## Restore sequence

1. cmux restores the layout and types `bin/remote-restore` into each pane.
2. `remote-restore` reads the pane's target from the host index.
3. `lib/cmux-remote-ready.py` takes a per-target lock. For Lima, if the VM is
   not running it first removes pid/socket files older than the host boot,
   starts the VM (once; other panes wait on the lock), then probes
   `test -r <root>/lib/cmux-remote-run.sh` with backoff until the remote
   answers. An instance Lima does not know is refused, not created. For ssh
   it only probes, without the lock. Readiness is cached per Lima host-agent pid.
4. The pane's record is fetched (`cmux-remote-state.py read-env` on the
   remote). Only its `KEY=value` lines are evaluated on the host; output with
   no record counts as a failed read and the mapping is left alone.
5. An interactive bash is opened over the transport, sourcing
   `lib/cmux-remote-run.sh` with the record's values in the environment.
6. The runner expands a leading `~` in the saved directory, `cd`s, runs the agent through `lib/cmux-remote-agent` (which
   applies `CMUX_AGENT_MODE`), and when the agent exits for any reason execs
   an interactive remote shell. The pane therefore never collapses back to a
   host shell.
7. If ssh itself fails (exit 255) within 30 seconds, `remote-restore` retries
   up to three times, then prints a message and waits for Enter to retry.

## Why not tmux

An earlier version kept agents alive in remote tmux sessions and reattached.
It survived cmux restarts but not reboots, it needed a tag store to map panes
to windows, and the reattach path broke in ways that were hard to see.
Persisting the conversation (the transcript) rather than the process is
simpler and survives everything except an in-flight generation.
