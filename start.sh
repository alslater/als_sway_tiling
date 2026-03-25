#!/bin/sh
DIR="$(cd "$(dirname "$0")" && pwd)"
pkill -f als_tiling.py 2>/dev/null
pkill -f focus-guard.py 2>/dev/null
pkill -f link-focus.py 2>/dev/null
sleep 0.5
"$DIR/venv/bin/python3" "$DIR/als_tiling.py" &
"$DIR/venv/bin/python3" "$DIR/link-focus.py" &
exec "$DIR/venv/bin/python3" "$DIR/focus-guard.py"
