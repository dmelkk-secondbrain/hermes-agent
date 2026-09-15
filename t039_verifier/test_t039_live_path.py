"""Independent live-source verification harness for T-039.

This suite makes no provider call. It drives the real dispatcher argv builder,
the real non-quiet ``cli.main(query=..., quiet=False)`` branch, and the real
Kanban SQLite consumer. Only ``HermesCLI.chat`` (the model-producing call) and
purely presentational methods are replaced.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli
from hermes_cli import kanban_db as kb
from hermes_cli import profiles

RATE_LIMIT_RESULT = {"failed": True, "failure_reason": "rate_limit"}
TARGET_ROOT = Path(os.environ["T039_TARGET_ROOT"]).resolve()


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id="t_synthetic_live",
        assignee="tokensol",
        tenant=None,
        branch_name=None,
        current_run_id=None,
        claim_lock=None,
        goal_mode=False,
        goal_max_turns=None,
        max_runtime_seconds=None,
        skills=None,
        model_override=None,
    )


def _stub_presentation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.HermesCLI, "_show_security_advisories", lambda self: None)
    monkeypatch.setattr(
        cli.HermesCLI,
        "_print_exit_summary",
        lambda self, clear_screen=False: None,
    )


def _stub_rate_limit_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    def _chat(self, message, images=None):
        self._last_turn_result = dict(RATE_LIMIT_RESULT)
        return ""

    monkeypatch.setattr(cli.HermesCLI, "chat", _chat)


def test_target_import_identity() -> None:
    assert Path(cli.__file__).resolve() == TARGET_ROOT / "cli.py"
    assert Path(kb.__file__).resolve() == TARGET_ROOT / "hermes_cli" / "kanban_db.py"
    assert Path(sys.executable).resolve() == Path(os.environ["T039_TARGET_PYTHON"]).resolve()


def test_actual_dispatcher_nonquiet_q_rate_limit_exits_75(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "hermes-home"
    board = home / "kanban.db"
    home.mkdir()
    captured: dict[str, object] = {}

    class _Popen:
        pid = 42420

        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

        def poll(self):
            return None

    monkeypatch.setattr(kb, "_resolve_hermes_argv", lambda: ["hermes"])
    monkeypatch.setattr(profiles, "resolve_profile_env", lambda _profile: str(home))
    monkeypatch.setattr(kb, "kanban_db_path", lambda **_kwargs: board_path)
    monkeypatch.setattr(kb, "workspaces_root", lambda board=None: tmp_path)
    monkeypatch.setattr(kb, "worker_logs_dir", lambda board=None: tmp_path)
    monkeypatch.setattr(kb, "_rotate_worker_log", lambda *args: None)
    monkeypatch.setattr(kb.subprocess, "Popen", _Popen)

    board_path = board
    pid = kb._default_spawn(_task(), str(tmp_path / "worker"), board="default")
    assert pid == 42420
    command = captured["cmd"]
    assert command[-3:] == ["chat", "-q", "work kanban task t_synthetic_live"]

    spawn_env = captured["kwargs"]["env"]
    assert spawn_env["HERMES_KANBAN_TASK"] == "t_synthetic_live"
    assert spawn_env["HERMES_HOME"] == str(home)
    monkeypatch.setenv("HERMES_KANBAN_TASK", spawn_env["HERMES_KANBAN_TASK"])
    monkeypatch.setenv("HERMES_HOME", spawn_env["HERMES_HOME"])
    monkeypatch.setenv("HERMES_KANBAN_DB", str(board))
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    _stub_presentation(monkeypatch)
    _stub_rate_limit_chat(monkeypatch)

    actual_exit = 0
    try:
        cli.main(query=command[-1], quiet=False, ignore_user_config=True)
    except SystemExit as excinfo:
        actual_exit = excinfo.code
    assert actual_exit == int(os.environ.get("T039_EXPECTED_EXIT", "75"))
    assert kb.KANBAN_RATE_LIMIT_EXIT_CODE == 75


def test_rate_limit_consumer_preserves_budget_and_enforces_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    board = tmp_path / "consumer.db"
    monkeypatch.setattr(kb, "kanban_db_path", lambda **_kwargs: board)
    monkeypatch.setenv("HERMES_KANBAN_CRASH_GRACE_SECONDS", "0")
    monkeypatch.setenv("HERMES_KANBAN_RATE_LIMIT_COOLDOWN_SECONDS", "300")
    monkeypatch.setattr(kb, "_pid_alive", lambda _pid: False)

    with kb.connect() as conn:
        task_id = kb.create_task(conn, title="synthetic quota", assignee="tokensol")
        host = kb._claimer_id().split(":", 1)[0]
        for index in range(6):
            pid = 43000 + index
            kb.claim_task(conn, task_id, claimer=f"{host}:synthetic-{index}")
            conn.execute(
                "UPDATE tasks SET worker_pid=?, consecutive_failures=0 WHERE id=?",
                (pid, task_id),
            )
            conn.commit()
            kb._record_worker_exit(pid, kb.KANBAN_RATE_LIMIT_EXIT_CODE << 8)
            assert kb._classify_worker_exit(pid) == ("rate_limited", 75)
            assert task_id not in kb.detect_crashed_workers(conn)
            task = kb.get_task(conn, task_id)
            assert task.status == "ready"
            assert task.consecutive_failures == 0

        row = conn.execute(
            "SELECT MAX(ended_at) AS ended_at FROM task_runs WHERE task_id=?",
            (task_id,),
        ).fetchone()
        ended_at = int(row["ended_at"])
        outcomes = [
            item["outcome"]
            for item in conn.execute(
                "SELECT outcome FROM task_runs WHERE task_id=?", (task_id,)
            ).fetchall()
        ]
        assert outcomes == ["rate_limited"] * 6
        monkeypatch.setattr(kb.time, "time", lambda: ended_at + 100)
        assert kb.check_respawn_guard(conn, task_id) == "rate_limit_cooldown"
        monkeypatch.setattr(kb.time, "time", lambda: ended_at + 400)
        assert kb.check_respawn_guard(conn, task_id) is None


def test_ordinary_nonkanban_nonquiet_query_keeps_implicit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    _stub_presentation(monkeypatch)
    _stub_rate_limit_chat(monkeypatch)
    assert cli.main(query="ordinary query", quiet=False, ignore_user_config=True) is None


def test_interactive_path_still_calls_run_without_process_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    called = []
    monkeypatch.setattr(cli.HermesCLI, "run", lambda self: called.append(True))
    assert cli.main(ignore_user_config=True) is None
    assert called == [True]
