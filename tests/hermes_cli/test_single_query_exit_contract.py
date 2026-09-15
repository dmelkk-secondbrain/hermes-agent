"""Regression coverage for dispatcher-spawned non-quiet ``chat -q`` workers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli
from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE


@pytest.fixture(autouse=True)
def _clear_kanban_env(monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)


@pytest.mark.parametrize("result", [None, {}, {"failed": False}, {"final_response": "ok"}])
def test_single_query_success_or_no_result_exits_zero(result):
    assert cli._single_query_exit_code(result) == 0


@pytest.mark.parametrize("reason", ["rate_limit", "billing"])
def test_rate_limit_sentinel_is_limited_to_kanban_workers(monkeypatch, reason):
    result = {"failed": True, "failure_reason": reason}
    assert cli._single_query_exit_code(result) == 1
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_synthetic")
    assert cli._single_query_exit_code(result) == KANBAN_RATE_LIMIT_EXIT_CODE


def test_ordinary_kanban_failure_is_not_rate_limited(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_synthetic")
    assert cli._single_query_exit_code({"failed": True, "failure_reason": "tool_error"}) == 1


def test_dispatcher_spawn_command_uses_nonquiet_q_path(monkeypatch, tmp_path):
    """The producer path under repair is the actual dispatcher argv, not ``-Q``."""
    from hermes_cli import kanban_db as kb

    task = SimpleNamespace(
        id="t_synthetic", assignee="tokenterra", tenant=None, branch_name=None,
        current_run_id=None, claim_lock=None, goal_mode=False, goal_max_turns=None,
        max_runtime_seconds=None, skills=None, model_override=None,
    )
    captured = {}

    class _Popen:
        pid = 12345
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
        def poll(self):
            return None

    from hermes_cli import profiles

    monkeypatch.setattr(kb, "_resolve_hermes_argv", lambda: ["hermes"])
    monkeypatch.setattr(profiles, "resolve_profile_env", lambda _p: str(tmp_path))
    monkeypatch.setattr(kb, "kanban_db_path", lambda board=None: tmp_path / "kanban.db")
    monkeypatch.setattr(kb, "workspaces_root", lambda board=None: tmp_path)
    monkeypatch.setattr(kb, "worker_logs_dir", lambda board=None: tmp_path)
    monkeypatch.setattr(kb, "_rotate_worker_log", lambda *a: None)
    monkeypatch.setattr(kb.subprocess, "Popen", _Popen)

    assert kb._default_spawn(task, str(tmp_path), board="default") == 12345
    assert captured["cmd"][-3:] == ["chat", "-q", "work kanban task t_synthetic"]


def test_rate_limit_exit_is_classified_without_failure_budget(monkeypatch):
    """Synthetic 429 producer sentinel reaches the existing consumer contract."""
    from hermes_cli import kanban_db as kb

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_synthetic")
    assert cli._single_query_exit_code({"failed": True, "failure_reason": "rate_limit"}) == 75
    pid = 42424
    kb._record_worker_exit(pid, KANBAN_RATE_LIMIT_EXIT_CODE << 8)
    assert kb._classify_worker_exit(pid) == ("rate_limited", KANBAN_RATE_LIMIT_EXIT_CODE)
