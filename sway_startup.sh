#!/bin/sh
DIR="$(cd "$(dirname $(realpath "$0"))" && pwd)"
exec "$DIR/venv/bin/python3" "$DIR/sway_startup.py"
