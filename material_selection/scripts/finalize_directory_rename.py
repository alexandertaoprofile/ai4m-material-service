"""Remove the legacy directory link after the previous 1105 process has stopped."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPOSITORY = Path('/data/se42/alpha_project/material_service_hub')
PORT = 1105


def listener_present(proc_root: Path = Path('/proc')) -> bool:
    for table in ('tcp', 'tcp6'):
        path = proc_root / 'net' / table
        if not path.is_file():
            continue
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 4 and fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == PORT:
                return True
    return False


def service_processes(repository: Path, proc_root: Path = Path('/proc')) -> list[int]:
    matches = []
    actual = (repository / 'material_selection').resolve()
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            cwd = Path(os.readlink(proc / 'cwd')).resolve()
            if cwd == actual and any(Path(arg).name == 'main.py' for arg in args if arg):
                matches.append(int(proc.name))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return matches


def validate_paths(repository: Path) -> tuple[Path, Path]:
    root = repository.resolve(strict=True)
    actual, legacy = root / 'material_selection', root / 'material_database'
    if not actual.is_dir() or actual.is_symlink() or actual.resolve() != actual:
        raise RuntimeError('正式目录不是预期的真实目录，已停止清理。')
    if legacy.is_symlink():
        if os.readlink(legacy) != 'material_selection' or legacy.resolve() != actual:
            raise RuntimeError('旧入口指向其他位置，已停止清理。')
    elif legacy.exists():
        raise RuntimeError('旧入口是实际文件或目录，已停止清理。')
    return actual, legacy


def finalize(repository: Path = REPOSITORY, proc_root: Path = Path('/proc'), dry_run: bool = False) -> dict:
    actual, legacy = validate_paths(repository)
    if not legacy.is_symlink():
        return {'status': 'already_clean', 'directory': str(actual)}
    processes = service_processes(repository, proc_root)
    if listener_present(proc_root) or processes:
        raise RuntimeError('1105 或原服务进程仍在运行。请先在 material-product-1105 会话按 Ctrl+C 停止，再运行清理命令。')
    if dry_run:
        return {'status': 'ready', 'directory': str(actual), 'action': '仅删除旧软链接'}
    # Recheck the exact target and running state immediately before unlinking.
    validate_paths(repository)
    if listener_present(proc_root) or service_processes(repository, proc_root):
        raise RuntimeError('发现服务进程已启动，已停止清理。')
    legacy.unlink()
    return {'status': 'cleaned', 'directory': str(actual), 'removed': str(legacy), 'data': '保持原值'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(finalize(dry_run=args.dry_run), ensure_ascii=False))
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
