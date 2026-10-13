#!/usr/bin/env bash
# Build SciSketch for this OS:  ./build.sh   ->  dist/SciSketch/
set -euo pipefail
pip install -r requirements.txt -r requirements-build.txt
pyinstaller scisketch.spec --noconfirm --clean
echo "Built: dist/SciSketch/"
