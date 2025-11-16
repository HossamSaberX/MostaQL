#!/bin/bash

echo "=== Modified tracked files ==="
git diff

echo -e "\n=== Untracked file diffs ==="
for file in $(git ls-files --others --exclude-standard); do
  echo -e "\n--- $file ---"
  git diff --no-index /dev/null "$file"
done
