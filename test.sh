#!/bin/bash
set -e
cd "$(dirname "$0")"

uv run setup.py build_ext --inplace
uv run pytest -s