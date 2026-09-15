#!/usr/bin/env python3
"""T-041 bounded, read-only custody capture and preflight verifier.

observe is intentionally read-only: it samples an externally launched process and
hermes.service for exactly 120 seconds.  It never signals a process or changes
systemd, Git, Kanban, or configuration.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WINDOW_SECONDS = 120
INTERVAL_SECONDS = 5
COMMAND_TIMEOUT_SECONDS = 5


def utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def run(argv: list[str]) -> dict:
    try:
        result = subprocess.run(argv, text=True, capture_output=True,
                                timeout=COMMAND_TIMEOUT_SECONDS, check=False)
        return {'argv': argv, 'exit': result.returncode,
                'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {'argv': argv, 'exit': 'timeout', 'stdout': '', 'stderr': ''}


def cgroup(pid: int) -> dict:
    result = run(['cat', f'/proc/{pid}/cgroup'])
    return {'pid': pid, 'read': result, 'value': result['stdout']}


def git_evidence(source: Path, candidate: str) -> dict:
    head = run(['git', '-C', str(source), 'rev-parse', 'HEAD'])
    tree = run(['git', '-C', str(source), 'rev-parse', 'HEAD^{tree}'])
    present = run(['git', '-C', str(source), 'cat-file', '-e', f'{candidate}^{{commit}}'])
    return {'source': str(source), 'head': head, 'tree': tree,
            'candidate': candidate, 'candidate_present_in_source_db': present['exit'] == 0,
            'candidate_check': present}


def observe(args: argparse.Namespace) -> int:
    output = Path(args.output)
    if output.exists():
        print('refusing to overwrite fixed capture', file=sys.stderr)
        return 2
    start = time.monotonic()
    home = Path(tempfile.mkdtemp(prefix='t041-custody-home-'))
    data = {'mode': 'read-only-observe', 'started_utc': utc(), 'window_seconds': WINDOW_SECONDS,
            'interval_seconds': INTERVAL_SECONDS, 'command_timeout_seconds': COMMAND_TIMEOUT_SECONDS,
            'probe': {'pid': os.getpid(), 'ppid': os.getppid(), 'pgid': os.getpgid(0), 'sid': os.getsid(0),
                      'cgroup': cgroup(os.getpid())}, 'temporary_home': str(home),
            'gateway': run(['systemctl', 'show', 'hermes.service', '--property=MainPID',
                            '--property=ActiveState', '--property=SubState', '--property=ControlGroup', '--no-pager']),
            'source_import': git_evidence(Path(args.source), args.candidate), 'samples': []}
    try:
        while time.monotonic() - start < WINDOW_SECONDS:
            service = run(['systemctl', 'show', 'hermes.service', '--property=MainPID',
                           '--property=ControlGroup', '--no-pager'])
            data['samples'].append({'utc': utc(), 'probe': cgroup(os.getpid()), 'gateway': service})
            remaining = WINDOW_SECONDS - (time.monotonic() - start)
            if remaining > 0:
                time.sleep(min(INTERVAL_SECONDS, remaining))
        data['ended_utc'] = utc()
        data['elapsed_seconds'] = round(time.monotonic() - start, 3)
        data['pass'] = ('/system.slice/hermes.service' not in data['probe']['cgroup']['value'] and
                        len(data['samples']) >= WINDOW_SECONDS // INTERVAL_SECONDS)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
        return 0 if data['pass'] else 1
    finally:
        shutil.rmtree(home, ignore_errors=True)


def verify_fixture(args: argparse.Namespace) -> int:
    fixture = json.loads(Path(args.fixture).read_text())
    errors = []
    if fixture.get('elapsed_seconds', 0) > WINDOW_SECONDS:
        errors.append('observation ceiling exceeded')
    if fixture.get('command_exit') == 'timeout':
        errors.append('command timeout')
    if not fixture.get('start_evidence'):
        errors.append('missing start evidence')
    pids = fixture.get('pids', [])
    if len(pids) < 2 or len(set(pids)) == 1:
        errors.append('unchanged or missing PID evidence')
    print(json.dumps({'pass': not errors, 'errors': errors}, sort_keys=True))
    return 0 if not errors else 1


def rollback_guard(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    status = run(['git', '-C', str(repo), 'status', '--porcelain'])
    head = run(['git', '-C', str(repo), 'rev-parse', 'HEAD'])
    errors = []
    if status['exit'] != 0 or status['stdout']:
        errors.append('dirty tree refusal')
    if head['exit'] != 0 or head['stdout'] != args.expected_head:
        errors.append('wrong HEAD refusal')
    print(json.dumps({'pass': not errors, 'errors': errors}, sort_keys=True))
    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser('observe')
    p.add_argument('--output', required=True)
    p.add_argument('--source', required=True)
    p.add_argument('--candidate', required=True)
    p.set_defaults(func=observe)
    p = sub.add_parser('verify-fixture')
    p.add_argument('--fixture', required=True)
    p.set_defaults(func=verify_fixture)
    p = sub.add_parser('rollback-guard')
    p.add_argument('--repo', required=True)
    p.add_argument('--expected-head', required=True)
    p.set_defaults(func=rollback_guard)
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
