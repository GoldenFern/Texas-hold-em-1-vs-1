@echo off
setlocal
cd /d "%~dp0"

if not exist "static\dist\index.html" (
  echo [Heads-Up Lab] Frontend build not found, building...
  pushd frontend
  if not exist node_modules (
    call npm install
  )
  call npm run build
  popd
)

python main.py %*
endlocal
