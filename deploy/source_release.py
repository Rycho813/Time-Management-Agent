#!/usr/bin/env python3
"""Operator tools for an isolated test deployment; never run on import."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

AGENT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path('/opt/timemap-test')
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def checked_repo(repo, expected):
    repo = Path(repo).resolve()
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('Expected commit must be a full lowercase 40-character SHA.')
    if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        raise ValueError('Expected repository root, not a nested directory.')
    if git(repo, 'rev-parse', 'HEAD') != expected:
        raise ValueError('Checkout differs from the approved commit.')
    if git(repo, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Repository contains local changes or untracked files.')
    return repo


def docker_env():
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY',
                'DOCKER_CERT_PATH', 'COMPOSE_PROFILES', 'COMPOSE_FILE'):
        env.pop(key, None)
    return env


def docker(*args, capture=True):
    result = subprocess.run(DOCKER + list(args), env=docker_env(), text=True,
                            check=True, capture_output=capture)
    return result.stdout.strip() if capture else None


def image_tag(agent_sha, frontend_sha):
    return 'timemap-source-test:' + agent_sha[:12] + '-' + frontend_sha[:12]


def new_file(path, text, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w') as file:
        file.write(text)


def image_check(record):
    for key in ('agent_commit', 'frontend_commit'):
        if not re.fullmatch(r'[0-9a-f]{40}', record[key]):
            raise ValueError('Invalid recorded commit.')
    if record['image_tag'] != image_tag(record['agent_commit'], record['frontend_commit']):
        raise ValueError('Image tag does not match commits.')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', record['image_id']):
        raise ValueError('Invalid recorded image ID.')
    image = json.loads(docker('image', 'inspect', record['image_tag']))[0]
    labels = image['Config'].get('Labels') or {}
    if (image['Id'] != record['image_id'] or image['Architecture'] != 'amd64'
            or image['Os'] != 'linux' or image['Config']['User'] != '10001:10001'
            or labels.get('org.opencontainers.image.revision') != record['agent_commit']
            or labels.get('timemap.frontend.revision') != record['frontend_commit']):
        raise ValueError('Image identity/platform/labels differ from the approved source.')


def build(args):
    agent = checked_repo(AGENT, args.agent_sha)
    frontend = checked_repo(args.frontend, args.frontend_sha)
    record_path = Path(args.record).absolute()
    if record_path.exists() or record_path.is_symlink() or not record_path.parent.is_dir():
        raise ValueError('Record needs an existing parent and a new target filename.')
    record_path = record_path.resolve()
    if any(record_path.is_relative_to(p) for p in (agent, frontend)):
        raise ValueError('Build record must be outside both source repositories.')
    tag = image_tag(args.agent_sha, args.frontend_sha)
    if docker('image', 'ls', '--filter', 'reference=' + tag, '--format', '{{.ID}}'):
        raise ValueError('Image tag already exists; refusing to replace a previous build.')
    command = ['build', '--platform', 'linux/amd64', '-t', tag,
               '--label', 'org.opencontainers.image.revision=' + args.agent_sha,
               '--label', 'timemap.frontend.revision=' + args.frontend_sha,
               '-f', str(agent / 'deploy/Dockerfile')]
    if args.ca_bundle:
        ca = Path(args.ca_bundle).resolve(strict=True)
        command += ['--secret', 'id=proxy_ca,src=' + str(ca)]
    docker(*command, str(agent), capture=False)
    # Reject source changes made while the build was running.
    checked_repo(agent, args.agent_sha)
    checked_repo(frontend, args.frontend_sha)
    image = json.loads(docker('image', 'inspect', tag))[0]
    record = {'format': 1, 'agent_dir': str(agent), 'frontend_dir': str(frontend),
              'agent_commit': args.agent_sha, 'frontend_commit': args.frontend_sha,
              'image_tag': tag, 'image_id': image['Id'], 'image_size_bytes': image['Size'],
              'platform': 'linux/amd64', 'database_schema': 2,
              'built_at_utc': datetime.now(timezone.utc).isoformat()}
    image_check(record)
    new_file(record_path, json.dumps(record, indent=2) + '\n', 0o644)
    print('BUILD_RECORDED=' + str(record_path))
    print('IMAGE_ID=' + record['image_id'])


def verify_record(record):
    if record.get('format') != 1 or Path(record['agent_dir']).resolve() != AGENT:
        raise ValueError('Record belongs to another source checkout.')
    checked_repo(AGENT, record['agent_commit'])
    checked_repo(record['frontend_dir'], record['frontend_commit'])
    image_check(record)


def prepare(args):
    if os.geteuid() != 0:
        raise ValueError('Use sudo for initial test-directory preparation.')
    record = json.loads(Path(args.record).read_text())
    verify_record(record)
    root = Path(args.root).absolute()
    if root.resolve() != root:
        raise ValueError('Test root must not resolve through a symlink.')
    targets = [root / n for n in ('config', 'data', 'backups', 'current')]
    runtime = AGENT / 'deploy/runtime.env'
    if any(p.exists() or p.is_symlink() for p in targets + [runtime]):
        raise ValueError('Existing config/data/pointer detected; refusing overwrite.')
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    for path in targets[:3]:
        path.mkdir(mode=0o700)
    for path in targets[1:3]:
        os.chown(path, 10001, 10001)
    new_file(root / 'config/runtime.env', '\n'.join([
        'TIMEMAP_DB=/data/timemap.sqlite3',
        'TIMEMAP_RULES=/app/config/activity_rules.json',
        'TIMEMAP_ORIGINS=https://timemap.rychoz.com',
        'TIMEMAP_DEVICE_TOKEN=' + secrets.token_urlsafe(32),
        'TIMEMAP_ALLOW_MODEL=0', 'TIMEMAP_ALLOW_SEND=0', '',
    ]))
    new_file(root / 'config/compose.env', 'TIMEMAP_IMAGE=' + record['image_tag']
             + '\nTIMEMAP_DATA_DIR=' + str(root / 'data') + '\n')
    new_file(root / 'config/release.json', json.dumps(record, indent=2) + '\n')
    runtime.symlink_to(root / 'config/runtime.env')
    (root / 'current').symlink_to(AGENT.parent)
    print('CONFIG_READY: private config generated; token not printed; no services started.')


def compose(args):
    root = Path(args.root).absolute()
    command = args.compose_args
    if command[:1] == ['--']:
        command = command[1:]
    if not command or command[0] not in ('config', 'up', 'run', 'restart', 'stop', 'ps', 'logs'):
        raise ValueError('Allowed: config --quiet, up, run, restart, stop, ps, logs.')
    if command[0] == 'config' and command != ['config', '--quiet']:
        raise ValueError('Only config --quiet is allowed; never print resolved secrets.')
    if 'sender' in command or '--profile' in command:
        raise ValueError('Sending is outside the initial source-deployment test.')
    # Recovery/status remain available even when a build/tag/source check fails.
    if command[0] not in ('stop', 'ps', 'logs'):
        record = json.loads((root / 'config/release.json').read_text())
        verify_record(record)
        values = dict(line.split('=', 1) for line in (root / 'config/runtime.env').read_text().splitlines()
                      if line and not line.startswith('#'))
        if any(values.get(k) != '0' for k in ('TIMEMAP_ALLOW_MODEL', 'TIMEMAP_ALLOW_SEND')):
            raise ValueError('Initial testing requires model/send to remain disabled.')
    if command[0] in ('up', 'run'):
        command = [command[0], '--pull', 'never'] + command[1:]
    docker('compose', '--project-name', 'timemap-test', '--env-file', str(root / 'config/compose.env'),
           '-f', str(AGENT / 'deploy/compose.yaml'), *command, capture=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    p = commands.add_parser('build')
    p.add_argument('--agent-sha', required=True)
    p.add_argument('--frontend-sha', required=True)
    p.add_argument('--frontend', required=True)
    p.add_argument('--record', required=True)
    p.add_argument('--ca-bundle')
    p.set_defaults(handler=build)
    p = commands.add_parser('prepare')
    p.add_argument('--record', required=True)
    p.add_argument('--root', default=str(DEFAULT_ROOT))
    p.set_defaults(handler=prepare)
    p = commands.add_parser('compose')
    p.add_argument('--root', default=str(DEFAULT_ROOT))
    p.add_argument('compose_args', nargs=argparse.REMAINDER)
    p.set_defaults(handler=compose)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        # Do not print subprocess env, private records, or resolved configuration.
        raise SystemExit('STOP: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))


if __name__ == '__main__':
    main()
