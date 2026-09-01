@echo off
rem Double-click launcher for Windows. Kept ASCII-only on purpose: cmd.exe renders the
rem batch file with the console code page, so Chinese here would show up as garbage.
rem All user-facing Chinese lives in web\launch.py (Python writes the console via the
rem wide-char API, so it is safe there).
setlocal
cd /d "%~dp0"

rem The version check uses max instead of a comparison operator on purpose: cmd treats
rem < and > as redirection, and the check runs inside a parenthesised block.
set "PY="
for %%C in (py python python3) do (
  if not defined PY (
    %%C -c "import sys;v=sys.version_info[:2];sys.exit(0 if max(v,(3,8))==v else 1)" >nul 2>nul && set "PY=%%C"
  )
)

if not defined PY (
  echo [x] Python 3.8+ not found.
  echo     Download it from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH" during installation.
  echo.
  pause
  exit /b 1
)

%PY% web\launch.py %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [x] Launcher exited with code %CODE%
  pause
)
exit /b %CODE%
