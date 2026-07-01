#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/tlatmsrud/claude-code-web.git"
APP_DIR_NAME="claude-code-web"
VENV_DIR=".venv"
DEFAULT_BRANCH="main"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 1) 소스 코드 위치 결정
if [ -f "$SCRIPT_DIR/app.py" ] && [ -d "$SCRIPT_DIR/.git" ]; then
  # run.sh가 이미 클론된 저장소 내부에 있음
  APP_DIR="$SCRIPT_DIR"
elif [ -f "$SCRIPT_DIR/app.py" ]; then
  # 소스만 받은 상태(app.py는 있는데 .git이 없음) → 현재 디렉토리를 git 저장소로 승격.
  # 이 과정에서 저장소 상의 파일들은 origin/$DEFAULT_BRANCH 버전으로 덮어써지며,
  # 저장소에 없는 사용자 로컬 파일(예: 개인 노트, .venv, .env 등)은 그대로 유지됨.
  APP_DIR="$SCRIPT_DIR"
  cd "$APP_DIR"
  echo "[migrate] converting source-only install to git-tracked ($REPO_URL)"
  git init -q
  git remote add origin "$REPO_URL" 2>/dev/null || git remote set-url origin "$REPO_URL"
  if git fetch --quiet origin "$DEFAULT_BRANCH"; then
    git symbolic-ref HEAD "refs/heads/$DEFAULT_BRANCH"
    git reset --hard "origin/$DEFAULT_BRANCH"
    git branch --set-upstream-to "origin/$DEFAULT_BRANCH" "$DEFAULT_BRANCH" 2>/dev/null || true
    echo "[migrate] done — auto-update is now available"
  else
    echo "[warn] fetch failed; keeping local source (auto-update won't work until next successful run)"
  fi
elif [ -d "$SCRIPT_DIR/$APP_DIR_NAME/.git" ]; then
  # 같은 위치에 이미 클론된 디렉토리가 있음
  APP_DIR="$SCRIPT_DIR/$APP_DIR_NAME"
else
  # 처음 실행 → 저장소 클론
  APP_DIR="$SCRIPT_DIR/$APP_DIR_NAME"
  echo "[setup] cloning $REPO_URL"
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

# 2) 최신 코드 pull
if [ -d ".git" ]; then
  CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "$DEFAULT_BRANCH")"
  echo "[sync] pulling latest changes from origin/$CURRENT_BRANCH"
  if ! git pull --ff-only origin "$CURRENT_BRANCH"; then
    echo "[warn] git pull failed; continuing with current local code"
  fi
fi

# 3) 가상환경 준비
if [ ! -d "$VENV_DIR" ]; then
  echo "[setup] creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# 4) 의존성 설치 / 업데이트
echo "[setup] installing requirements"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# 5) 앱 실행
exec streamlit run app.py --server.port 8501 --server.headless true
