#!/bin/sh
pkill -f als_tiling.py 2>/dev/null
pkill -f focus-guard.py 2>/dev/null
sleep 0.5
/home/aslate/dev/als_tiling/venv/bin/python3 /home/aslate/dev/als_tiling/als_tiling.py &
exec /home/aslate/dev/als_tiling/venv/bin/python3 /home/aslate/dev/als_tiling/focus-guard.py
