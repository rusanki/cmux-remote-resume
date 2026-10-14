# Troubleshooting

## Every VM pane fails right after a reboot, and running `vm-restore` by hand minutes later works

Cause: Lima leaves `~/.lima/<vm>/ha.pid` and `vz.pid` behind when the host
reboots. Lima's `ReadPIDFile` treats a pid as alive if *any* process has it,
so when a login-time process reuses the number, `limactl list` reports the
instance as `Broken` and `limactl start` fails with
`errors inspecting instance`. Every restoring pane hits that within a tenth of
a second. Once the transient process exits the pid is free again, Lima sees
`Stopped`, and a manual start works.

Fix: `lib/cmux-vm-ready.py` deletes `*.pid` and `*.sock` files in the
instance directory that are older than the host boot time
(`sysctl kern.boottime`) before starting. Do **not** use `limactl stop -f`
for this: it sends SIGKILL to whatever process currently owns the stale pid.

To check by hand:

```bash
limactl list ubuntu --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["errors"])'
sysctl -n kern.boottime; stat -f '%m %N' ~/.lima/ubuntu/*.pid
```

## A pane shows a host shell instead of the VM

Look at the pane text before retyping anything.

- `vm-restore: VM 'x' is not reachable` followed by an Enter prompt: the VM
  did not answer within the probe timeout. Press Enter. If it keeps failing,
  `limactl start <vm>` in another pane and read its output.
- `cmux session tracking: missing CMUX_SURFACE_ID`: the command was run
  outside a cmux pane. It only makes sense as a pane's resume command.
- Nothing at all, straight to a host prompt: the binding did not run. Check
  cmux Settings > Terminal > Resume Commands for `bin/vm-restore` with policy
  "auto". If it shows "manual", re-register from a pane with `vmc`; the
  `--source vmc` route is what triggers the approval prompt.

## `claude --resume` says no conversation found

The session transcript lives under `~/.claude/projects/<encoded cwd>/` in the
guest. `cmux-vm-run.sh` resolves the directory from the transcript, so this
normally means the transcript was deleted or the session was ended with
`/exit` (which clears the mapping, by design). The pane falls back to a VM
shell with the record retained; start a fresh `claude` there.

## `codex resume` is not registered

Codex emits no `SessionStart` on an explicit resume. The wrapper in
`lib/cmux-codex` handles it, and the guest `~/.bashrc` block aliases `codex`
to the wrapper while keeping your own flags. If you invoke the binary by its
full path, or your shell is not bash, the wrapper is bypassed. Run
`alias codex` inside a VM pane to check.

## Hooks are installed but nothing is recorded

- Hooks must be trusted. For Codex, `install-guest.py` writes the trust
  hashes to `~/.codex/config.toml`; `guest/check-hooks.py` prints what the
  Codex app server thinks of them.
- The guest must be able to write `state/sessions/`. The mount has to be
  `writable: true` in `lima.yaml`.
- An already-running agent does not pick up new hooks. Restart it.

## Many panes, slow restore

Startup is serialized per VM; the first pane boots it, the rest wait. On a
warm VM the readiness probe runs once and is cached, so 20 to 30 panes
reconnect within a few seconds. sshd defaults (`MaxSessions 10`,
`MaxStartups 10:30:60`) can drop connections if you open far more panes than
that at once; raise them in the guest's `sshd_config` if you see
`Connection closed by … [preauth]`.

## Debugging a single pane

```bash
CMUX_SURFACE_ID=<pane uuid> python3 lib/cmux-vm-state.py read
```

prints the record `vm-restore` would act on. The pane uuid is in the pane's
environment as `CMUX_SURFACE_ID`.
