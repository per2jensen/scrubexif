#!/bin/bash
# Usage: scripts/git_commit_if_changed.sh <path> <message>
git add "$1"
if ! git diff --cached --quiet "$1"; then
  git commit -m "$2"
fi
