# Troubleshooting

## Every Lima pane fails right after a reboot, and running `remote-restore` by hand minutes later works

Lima backend only. Cause: Lima leaves `~/.lima/<vm>/ha.pid` and `vz.pid` behind when the host
reboots. Lima's `ReadPIDFile` treats a pid as alive if *any* process has it,
so when a login-time process reuses the number, `limactl list` reports the
instance as `Broken` and `limactl start` fails with
`errors inspecting instance`. Every restoring pane hits that within a tenth of
a second. Once the transient process exits the pid is free again, Lima sees
`Stopped`, and a manual start works.

Fix: `lib/cmux-remote-ready.py` deletes `*.pid` and `*.sock` files in the
instance directory that are older than the host boot time
(`sysctl kern.boottime`) before starting. Do **not** use `limactl stop -f`
for this: it sends SIGKILL to whatever process currently owns the stale pid.

To check by hand:

```bash
limactl list ubuntu --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["errors"])'
sysctl -n kern.boottime; stat -f '%m %N' ~/.lima/ubuntu/*.pid
```

## A pane shows a host shell instead of the remote

Look at the pane text before retyping anything.

- `remote-restore: 'x' is not reachable` followed by an Enter prompt: the
  remote did not answer within the probe timeout. Press Enter. For Lima,
  `limactl start <vm>` in another pane and read its output; for ssh, try
  `cmux-remote exec <target> true` and read ssh's error.
- `remote-restore: not inside a cmux pane`: the command was run outside a
  cmux pane. It only makes sense as a pane's resume command.
- Nothing at all, straight to a host prompt: the binding did not run. Check
  cmux Settings > Terminal > Resume Commands for `bin/remote-restore` with policy
  "auto". If it shows "manual", re-register from a pane with `vmc`; the
  `--source vmc` route is what triggers the approval prompt.

## `claude --resume` says no conversation found

The session transcript lives under `~/.claude/projects/<encoded cwd>/` in the
remote. `cmux-remote-run.sh` resolves the directory from the transcript, so this
normally means the transcript was deleted or the session was ended with
`/exit` (which clears the mapping, by design). The pane falls back to a VM
shell with the record retained; start a fresh `claude` there.

## The pane restores to the wrong target

The host index `~/.cache/cmux-remote/panes/<pane-uuid>` names the target.
It is written by `vmc`/`vms`; a pane that was never started through them
falls back to `CMUX_TARGET_DEFAULT`. `cmux-remote record <target>` shows
what each target has saved for the current pane.

## `codex resume` is not registered

Codex emits no `SessionStart` on an explicit resume. The wrapper in
`lib/cmux-codex` handles it, and the remote `~/.bashrc` block aliases `codex`
to the wrapper while keeping your own flags. If you invoke the binary by its
full path, or your remote shell is not bash, the wrapper is bypassed. Run
`alias codex` inside a remote pane to check.

## Hooks are installed but nothing is recorded

- Hooks must be trusted. For Codex, `install-remote.py` writes the trust
  hashes to `~/.codex/config.toml`; `remote/check-hooks.py` prints what the
  Codex app server thinks of them.
- The remote copy of the repo must be current. After a `git pull`, run
  `./install.sh --sync` so ssh targets get the new files.
- An already-running agent does not pick up new hooks. Restart it.

## Many panes, slow restore

Lima startup is serialized per instance; the first pane boots it, the rest
wait. On a warm VM the readiness probe runs once and is cached, so 20 to 30
panes reconnect within a few seconds. For ssh hosts, put `ControlMaster auto`,
`ControlPath` and `ControlPersist` in `~/.ssh/config` so panes share one
connection. sshd defaults (`MaxSessions 10`, `MaxStartups 10:30:60`) can drop
connections if you open far more panes than that at once; raise them in the
remote's `sshd_config` if you see `Connection closed by … [preauth]`.

## Debugging a single pane

```bash
cmux-remote record <target>            # the record remote-restore would act on
cmux-remote exec <target> 'echo ok'    # is the transport working at all
cmux-remote resolve <target>           # backend, address, remote root
```

Run them from the pane in question; the pane uuid is taken from its
`CMUX_SURFACE_ID`.
