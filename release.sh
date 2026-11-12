#!/bin/bash
# Release Runway: the only way changes reach the helper on Austin's Mac and the published page.
#   ./release.sh            check everything and show what changed since the last release (changes nothing)
#   ./release.sh --install  the same, then install the helper and tag the release (needs a clean tree)
# Publishing the page is a separate step (Claude's Artifact publish of dist/runway.html).
set -e
cd "$(dirname "$0")"
echo "1/3 tests (including the contract in tests/test_contract.py)"
./test.sh -q
echo "2/3 build and render check"
python3 build.py --render
last=$(git describe --tags --abbrev=0 --match 'release-*' 2>/dev/null || true)
echo "3/3 what changed since ${last:-the first release} (read this before installing):"
git log --format='  %h %s' ${last:+$last..}HEAD
git diff --stat ${last:+$last..}HEAD -- helper/runway.py src/runway.src.html tests/test_contract.py | tail -5
if git diff --quiet ${last:+$last..}HEAD -- tests/test_contract.py 2>/dev/null || [ -z "$last" ]; then :; else
  echo "  !! tests/test_contract.py changed since the last release. That needs Austin's say-so."; fi
if [ "$1" = "--install" ]; then
  [ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Commit first: the tree has changes."; exit 1; }
  cp helper/runway.py "$HOME/.ai-planner/runway.py"
  v=$(python3 helper/runway.py version | sed 's/@@VERSION //')
  tag="release-v$v-$(date +%Y%m%d-%H%M)"; git tag "$tag"
  echo "Installed helper v$v and tagged $tag. Now publish dist/runway.html."
fi
