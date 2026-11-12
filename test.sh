#!/bin/bash
# Run the test suite from the main checkout or any agent worktree. It tests the checkout you run it from
# (so an older worktree can call ~/Projects/runway/test.sh). The virtualenv lives once, in the main checkout,
# because worktrees don't get untracked folders, and is created on first use.
set -e
here="$(git rev-parse --show-toplevel 2>/dev/null)" || here="$(cd "$(dirname "$0")" && pwd)"
[ -d "$here/tests" ] || here="$(cd "$(dirname "$0")" && pwd)"
main="$(dirname "$(git -C "$here" rev-parse --path-format=absolute --git-common-dir)")"
venv="$main/.venv"
if [ ! -x "$venv/bin/python" ] || ! "$venv/bin/python" -c "import pytest" 2>/dev/null; then
  echo "Setting up $venv (one time)..." >&2
  /opt/homebrew/bin/python3 -m venv "$venv" 2>/dev/null || python3 -m venv "$venv"
  "$venv/bin/python" -m pip install -q pytest
fi
cd "$here"
exec "$venv/bin/python" -m pytest -q tests "$@"
