#!/bin/bash
# Regenerate PROJECT_INFO.txt for every session, in ~140 s chunks (re-run until it prints ALLDONE).
# Usage: bash batch.sh [--reset]   (--reset starts over;  run "python3 projinfo.py --reindex" first if calibration libraries changed)
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="${ASTRO_ROOT:-$(dirname "$(dirname "$HERE")")}"
cd "$ROOT" || exit 1
if [ "$1" = "--reset" ] || [ ! -s "$HERE/sessions.txt" ]; then ls -d [A-Y]*/*/ | sed 's|/$||' | grep -vE '/(_to_delete|stacked)$' > "$HERE/sessions.txt"; : > "$HERE/done.txt"; : > "$HERE/run.log"; fi
touch "$HERE/done.txt"; start=$(date +%s)
while IFS= read -r s; do
  grep -qxF "$s" "$HERE/done.txt" && continue
  [ $(( $(date +%s) - start )) -gt 140 ] && { echo "PAUSE - run again"; exit 0; }
  timeout 150 python3 "$HERE/projinfo.py" -q "$s" 2>&1 | tail -1 >> "$HERE/run.log" && echo "$s" >> "$HERE/done.txt"
done < "$HERE/sessions.txt"; echo ALLDONE
