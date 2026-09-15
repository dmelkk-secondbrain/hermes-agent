# T-039 durable offline verifier

The verifier is intentionally stored outside the runtime candidate. It is run by the selected target checkout's interpreter, from that checkout, and asserts the imported `cli.py` and `hermes_cli/kanban_db.py` paths. It stubs only `HermesCLI.chat` and presentation, uses a temporary Hermes home/SQLite DB, and makes no provider call.

Green candidate (expected: `18 passed`):

```bash
cd /home/brain/hermes-agent-worktrees/t039-reviewed-backport
T039_TARGET_ROOT=/home/brain/hermes-agent-worktrees/t039-reviewed-backport T039_TARGET_PYTHON=/home/brain/hermes-agent-worktrees/fleet-usage-visibility/.venv/bin/python T039_EXPECTED_EXIT=75 /home/brain/hermes-agent-worktrees/fleet-usage-visibility/.venv/bin/python -m pytest -q tests/hermes_cli/test_single_query_exit_contract.py tests/hermes_cli/test_kanban_db.py::test_classify_worker_exit_recognizes_rate_limit_sentinel tests/hermes_cli/test_kanban_db.py::test_rate_limit_exit_requeues_without_counting_failure tests/hermes_cli/test_kanban_db.py::test_real_crash_still_counts_and_trips_breaker tests/hermes_cli/test_kanban_db.py::test_respawn_guard_defers_rate_limited_within_cooldown /home/brain/hermes-agent-worktrees/t039-live-verifier/t039_verifier/test_t039_live_path.py
```

Baseline producer proof (expected assertion failure `0 == 75`, proving the old path does not emit exit 75):

```bash
cd /home/brain/hermes-agent-worktrees/t039-baseline-4281151a
T039_TARGET_ROOT=/home/brain/hermes-agent-worktrees/t039-baseline-4281151a T039_TARGET_PYTHON=/home/brain/hermes-agent-worktrees/fleet-usage-visibility/.venv/bin/python T039_EXPECTED_EXIT=75 /home/brain/hermes-agent-worktrees/fleet-usage-visibility/.venv/bin/python -m pytest -q /home/brain/hermes-agent-worktrees/t039-live-verifier/t039_verifier/test_t039_live_path.py::test_actual_dispatcher_nonquiet_q_rate_limit_exits_75
```

Fixture repair: the prior continuation changed the capture parameter name but still rejected `kanban_db_path(board=...)`. Both temporary-board replacements now use `lambda **_kwargs: board_path` / `lambda **_kwargs: board`, preserving the captured `Path` and the production keyword API.
