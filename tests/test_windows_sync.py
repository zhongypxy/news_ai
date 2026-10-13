"""Real Windows PowerShell/Git integration; only temporary local repositories, no network."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell integration')
def test_sync_accepts_normal_git_stderr_and_preserves_local_changes(tmp_path):
    def git(*args):
        return subprocess.run(['git', *map(str, args)], check=True, capture_output=True, text=True)

    remote, seed, local = [tmp_path/name for name in ['remote.git', 'seed', 'local']]
    git('init', '--bare', '--initial-branch=main', remote)
    git('clone', remote, seed)
    git('-C', seed, 'config', 'user.name', 'Sync Test')
    git('-C', seed, 'config', 'user.email', 'sync@example.test')
    (seed/'scripts').mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1]/'scripts/sync-windows.ps1', seed/'scripts/sync-windows.ps1')
    (seed/'.gitignore').write_text('logs/\n', 'utf-8')
    (seed/'archive.txt').write_text('edition one', 'utf-8')
    git('-C', seed, 'add', '.')
    git('-C', seed, 'commit', '-m', 'Initial edition')
    git('-C', seed, 'push', 'origin', 'main')
    git('clone', remote, local)
    (seed/'archive.txt').write_text('edition two', 'utf-8')
    git('-C', seed, 'commit', '-am', 'Next edition')
    git('-C', seed, 'push', 'origin', 'main')
    shell = str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
    args = [shell, '-NoProfile', '-NonInteractive', '-File', str(local/'scripts/sync-windows.ps1')]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (local/'archive.txt').read_text('utf-8') == 'edition two'
    assert 'Sync succeeded' in (local/'logs/windows-sync.log').read_text()
    (local/'archive.txt').write_text('local work', 'utf-8')
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert (local/'archive.txt').read_text('utf-8') == 'local work'
