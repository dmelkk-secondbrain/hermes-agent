#!/usr/bin/env python3
"""T-041 bounded, read-only custody capture and preflight verifier."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROCESS_CEILING_SECONDS = 120
OBSERVATION_SECONDS = 115
INTERVAL_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 5
GATEWAY_CGROUP = "/system.slice/hermes.service"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run(argv: list[str], *, deadline: float | None = None, env: dict[str, str] | None = None) -> dict:
    timeout = float(COMMAND_TIMEOUT_SECONDS)
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"argv": argv, "exit": "deadline", "stdout": "", "stderr": ""}
        timeout = min(timeout, remaining)
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout,
                                check=False, env=env)
        return {"argv": argv, "exit": result.returncode,
                "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"argv": argv, "exit": "timeout", "stdout": "", "stderr": ""}


def cgroup(pid: int, *, deadline: float, env: dict[str, str]) -> dict:
    result = run(["cat", f"/proc/{pid}/cgroup"], deadline=deadline, env=env)
    return {"pid": pid, "read": result, "value": result["stdout"]}


def git_evidence(source: Path, candidate: str, *, deadline: float, env: dict[str, str]) -> dict:
    head = run(["git", "-C", str(source), "rev-parse", "HEAD"], deadline=deadline, env=env)
    tree = run(["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], deadline=deadline, env=env)
    present = run(["git", "-C", str(source), "cat-file", "-e", f"{candidate}^{{commit}}"],
                  deadline=deadline, env=env)
    return {"source": str(source), "head": head, "tree": tree, "candidate": candidate,
            "candidate_present_in_source_db": present["exit"] == 0, "candidate_check": present}


def _command_ok(result: dict) -> bool:
    return result.get("exit") == 0


def _cgroup_ok(result: dict) -> bool:
    return _command_ok(result.get("read", {})) and bool(result.get("value")) and \
        GATEWAY_CGROUP not in result["value"]


def evaluate_observation(data: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if data.get("elapsed_seconds", math.inf) > data.get("process_ceiling_seconds", PROCESS_CEILING_SECONDS):
        errors.append("process ceiling exceeded")
    if not _cgroup_ok(data.get("probe", {}).get("cgroup", {})):
        errors.append("initial probe cgroup invalid")
    if not _command_ok(data.get("gateway", {})):
        errors.append("initial gateway query failed")
    source = data.get("source_import", {})
    for name in ("head", "tree"):
        if not _command_ok(source.get(name, {})):
            errors.append(f"source {name} query failed")
    if source.get("candidate_check", {}).get("exit") not in (0, 1):
        errors.append("candidate presence query failed")
    samples = data.get("samples", [])
    if len(samples) < data.get("required_samples", 1):
        errors.append("insufficient samples")
    for index, sample in enumerate(samples):
        if not _cgroup_ok(sample.get("probe", {})):
            errors.append(f"sample {index} probe cgroup invalid")
        if not _command_ok(sample.get("gateway", {})):
            errors.append(f"sample {index} gateway query failed")
    return not errors, errors


def observe(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if output.exists():
        print("refusing to overwrite fixed capture", file=sys.stderr)
        return 2
    start = time.monotonic()
    deadline = start + PROCESS_CEILING_SECONDS
    home = Path(tempfile.mkdtemp(prefix="t041-custody-home-"))
    env = os.environ.copy()
    env["HOME"] = str(home)
    required_samples = math.ceil(OBSERVATION_SECONDS / INTERVAL_SECONDS)
    data = {"mode": "read-only-observe", "started_utc": utc(),
            "process_ceiling_seconds": PROCESS_CEILING_SECONDS,
            "observation_seconds": OBSERVATION_SECONDS,
            "interval_seconds": INTERVAL_SECONDS, "required_samples": required_samples,
            "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
            "probe": {"pid": os.getpid(), "ppid": os.getppid(), "pgid": os.getpgid(0),
                      "sid": os.getsid(0), "cgroup": cgroup(os.getpid(), deadline=deadline, env=env)},
            "temporary_home": str(home),
            "gateway": run(["systemctl", "show", "hermes.service", "--property=MainPID",
                            "--property=ActiveState", "--property=SubState",
                            "--property=ControlGroup", "--no-pager"], deadline=deadline, env=env),
            "source_import": git_evidence(Path(args.source), args.candidate,
                                          deadline=deadline, env=env), "samples": []}
    try:
        for index in range(required_samples):
            if time.monotonic() >= deadline:
                break
            service = run(["systemctl", "show", "hermes.service", "--property=MainPID",
                           "--property=ControlGroup", "--no-pager"], deadline=deadline, env=env)
            data["samples"].append({"utc": utc(),
                                    "probe": cgroup(os.getpid(), deadline=deadline, env=env),
                                    "gateway": service})
            if index + 1 < required_samples:
                next_sample = start + ((index + 1) * INTERVAL_SECONDS)
                remaining = min(next_sample, deadline) - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
        data["ended_utc"] = utc()
        data["elapsed_seconds"] = round(time.monotonic() - start, 3)
        passed, errors = evaluate_observation(data)
        data["pass"] = passed
        data["errors"] = errors
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        return 0 if passed else 1
    finally:
        shutil.rmtree(home, ignore_errors=True)


def verify_fixture(args: argparse.Namespace) -> int:
    fixture = json.loads(Path(args.fixture).read_text())
    errors = []
    if fixture.get("elapsed_seconds", 0) > PROCESS_CEILING_SECONDS:
        errors.append("observation ceiling exceeded")
    if fixture.get("command_exit") == "timeout":
        errors.append("command timeout")
    if not fixture.get("start_evidence"):
        errors.append("missing start evidence")
    pids = fixture.get("pids", [])
    if len(pids) < 2 or len(set(pids)) == 1:
        errors.append("unchanged or missing PID evidence")
    print(json.dumps({"pass": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


def rollback_guard(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    env = os.environ.copy()
    status = run(["git", "-C", str(repo), "status", "--porcelain"], deadline=deadline, env=env)
    head = run(["git", "-C", str(repo), "rev-parse", "HEAD"], deadline=deadline, env=env)
    errors = []
    if status["exit"] != 0 or status["stdout"]:
        errors.append("dirty tree refusal")
    if head["exit"] != 0 or head["stdout"] != args.expected_head:
        errors.append("wrong HEAD refusal")
    print(json.dumps({"pass": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("observe")
    p.add_argument("--output", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--candidate", required=True)
    p.set_defaults(func=observe)
    p = sub.add_parser("verify-fixture")
    p.add_argument("--fixture", required=True)
    p.set_defaults(func=verify_fixture)
    p = sub.add_parser("rollback-guard")
    p.add_argument("--repo", required=True)
    p.add_argument("--expected-head", required=True)
    p.set_defaults(func=rollback_guard)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
