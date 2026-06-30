#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "[setup] creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

if ! python -c "import streamlit" >/dev/null 2>&1; then
  echo "[setup] installing requirements"
  pip install --upgrade pip >/dev/null
  pip install -r requirements.txt
fi

exec streamlit run app.py --server.port 8501 --server.headless true
