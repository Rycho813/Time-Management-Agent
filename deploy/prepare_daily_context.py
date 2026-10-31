#!/usr/bin/env python3
"""Export pinned, allowlisted daily sources to a new build context. Never runs Docker."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('migrate_and_summary.py', 'notion_migration.py', 'notion_read.py',
            'notion_write.py', 'llm_time_extract.py', 'time_summary.py',
            'time_comparison.py', 'project_tracker.py', 'life_calendar_exporter.py',
            'feishu_report.py', 'backend/core.py', 'backend/place_matching.py',
            'config/activity_rules.json', 'deploy/Dockerfile.daily')


def prepare(repo, expected, target):
    repo = Path(repo).resolve()
    target = Path(target).absolute()
    def git(*args):
        return subprocess.check_output(['git','-C',str(repo),*args])
    if not re.fullmatch(r'[0-9a-f]{40}', expected):
        raise ValueError('需要完整40位提交')
    if Path(git('rev-parse','--show-toplevel').decode().strip()).resolve() != repo:
        raise ValueError('需要仓库根目录')
    if git('rev-parse','HEAD').decode().strip() != expected:
        raise ValueError('源码提交不一致')
    if git('status','--porcelain','--untracked-files=normal').strip():
        raise ValueError('源码工作区存在修改')
    if (not target.parent.is_dir() or target.parent.resolve() != target.parent
            or target.is_relative_to(repo) or target.exists() or target.is_symlink()):
        raise ValueError('目标必须是仓库外的新目录，父目录必须已存在且不经符号链接')
    objects = {}
    for row in git('ls-tree','-rz','--full-tree',expected).split(b'\0'):
        if not row:
            continue
        meta, name = row.split(b'\t',1)
        mode, kind, oid = meta.decode().split()
        name = name.decode()
        path = PurePosixPath(name)
        selected = (name in REQUIRED
                    or (name.startswith('backend/') and path.suffix == '.py')
                    or name == 'backend/mock_activities.json')
        if not selected:
            continue
        if mode not in ('100644','100755') or kind != 'blob' or path.is_absolute() or '..' in path.parts:
            raise ValueError('选中的源文件不是普通文件')
        objects[name] = oid
    if any(name not in objects for name in REQUIRED):
        raise ValueError('固定提交缺少必需日报源文件')
    target.mkdir(mode=0o700)
    inventory = {}
    for name, oid in sorted(objects.items()):
        destination = target / ('Dockerfile' if name == 'deploy/Dockerfile.daily' else name)
        destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        raw = git('cat-file','blob',oid)
        with destination.open('xb') as file:
            file.write(raw)
        destination.chmod(0o600)
        inventory[str(destination.relative_to(target))] = hashlib.sha256(raw).hexdigest()
    # Sources remain untouched; no env/config secrets or repository ignore file are exported.
    result = {'agent_commit':expected, 'files':inventory, 'exports_environment_files':False,
              'runs_docker':False, 'starts_services':False}
    with (target/'context-manifest.json').open('x') as file:
        json.dump(result,file,indent=2)
    (target/'context-manifest.json').chmod(0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-sha',required=True)
    parser.add_argument('--target',required=True)
    args = parser.parse_args()
    try:
        result = prepare(ROOT,args.agent_sha,args.target)
    except (ValueError,OSError,subprocess.CalledProcessError):
        raise SystemExit('STOP: context preparation refused; no build or service startup performed') from None
    print('DAILY_CONTEXT_READY')
    print('AGENT_COMMIT='+result['agent_commit'])
    print('SOURCE_FILES='+str(len(result['files'])))


if __name__ == '__main__':
    main()
