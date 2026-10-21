#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -f "static/dist/index.html" ]; then
  echo "[Heads-Up Lab] Frontend build not found, building..."
  cd frontend
  if [ ! -d node_modules ]; then
    npm install
  fi
  npm run build
  cd ..
fi

python main.py "$@"
