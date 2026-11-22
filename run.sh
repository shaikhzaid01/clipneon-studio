#!/usr/bin/env bash
# ClipNeon Studio Launcher (Linux/macOS)
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
exec .venv/bin/python app.py
