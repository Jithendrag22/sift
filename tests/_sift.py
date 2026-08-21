"""Shared test plumbing: put `src/` on the path and locate the CLI entrypoint."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
CLI = ROOT / "sift"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def git(*args, cwd, check=True):
    """Run git quietly in `cwd`."""
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}:\n{r.stderr}")
    return r


def git_init(path):
    """A repo that commits without depending on the user's global git config."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", ".", cwd=path)
    git("config", "user.email", "test@sift.invalid", cwd=path)
    git("config", "user.name", "sift-tests", cwd=path)
    git("config", "commit.gpgsign", "false", cwd=path)
    return path


def commit_all(path, message):
    git("add", "-A", cwd=path)
    git("commit", "-q", "--allow-empty", "-m", message, cwd=path)
    return git("rev-parse", "HEAD", cwd=path).stdout.strip()


def run_cli(*args, cwd=None):
    """Invoke the real `sift` executable and return (rc, stdout, stderr)."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, str(CLI), *args], cwd=cwd and str(cwd),
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr
