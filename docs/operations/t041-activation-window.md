# T-041 unexecuted controlled activation window

Status: unexecuted; requires a fresh explicit live authorization naming candidate
`55348647298cade110eeeefae48cc0d462dc1298`, runtime `766374ab4f90de2c28ea7ac79418da1c473a8f63`, one import/fast-forward, one service restart, and rollback authority.

## Admission and custody

The future controller must be an external native process whose recorded cgroup is outside `/system.slice/hermes.service`. Before any live command, it runs:

```
python3 tools/t041_custody.py observe --output /home/brain/vault/agent/output/2026-09-15-t041-external-custody-probe.json --source /home/brain/.hermes/hermes-agent --candidate 55348647298cade110eeeefae48cc0d462dc1298
```

This is read-only and exactly 120 seconds. It captures its PID/PPID/PGID/SID/cgroup, service identity, command exits, source HEAD/tree, and whether the candidate is already in the runtime object database. It creates a fresh prefixed temporary home and removes it. A nonzero exit is refusal.

Admission is not “stop Codex dispatch.” The future authorized controller must first classify the following exact commands and permissions without executing them now:

```
sudo -n systemctl stop hermes.service
sudo -n systemctl start hermes.service
git -C /home/brain/.hermes/hermes-agent fetch fork propose/t041-activation-custody
git -C /home/brain/.hermes/hermes-agent merge --ff-only 55348647298cade110eeeefae48cc0d462dc1298
```

The first two require systemd service-management authorization for `hermes.service`; the latter two require write permission to the runtime checkout and Git object database. Candidate import is explicit because a separate-clone commit may not exist in runtime objects. These commands remain unexecuted.

Immediately before a stop, the controller must obtain a read-only board/process snapshot and refuse unless there are zero unrelated active workers. Held, blocked, scheduled, and non-running cards are preserved; no scheduler configuration is changed. Since gateway dispatch continues while the service is active, the coherent future proposal is a single controlled `stop → import/ff-only → start` window, not merely pausing a Codex client.

## Failure and recovery

Every pre/post check failure refuses further work. If the runtime tree is dirty, runtime HEAD differs from the expected T-039 head, import/verification fails, or post-start checks fail, the authorized controller must recover source to the complete T-039 tree and must not claim service survival. `rollback-guard` refuses dirty or wrong-HEAD states before any future rollback command:

```
python3 tools/t041_custody.py rollback-guard --repo /home/brain/.hermes/hermes-agent --expected-head <authorized-expected-head>
```

The helper has no apply mode: it cannot stop/start services, alter Git, or mutate the board.
