@echo off
rem ============================================================
rem  Keymento launcher  --  keep this file ASCII-only and CRLF.
rem  cmd.exe parses .bat with the active codepage, so non-ASCII
rem  text here breaks parsing before chcp can take effect, and
rem  LF-only line endings can make "goto"/"call :label" miss
rem  (.gitattributes forces CRLF for *.bat).
rem  Korean messages are printed by Python
rem  (tools\ensure_env.py, main.py --guide).
rem
rem  Meant to work on any Windows PC:
rem    1. Python to run with:  %KEYMENTO_PYTHON%  >  .venv  >  venv
rem       A venv that does not run here (copied from another PC,
rem       Python removed, older than 3.9) is moved aside to
rem       <name>-unused-NNNN -- never deleted.
rem    2. No venv: .venv is created from the best Python found:
rem         py -3.12 > -3.11 > -3.10 > -3.9 > py -3 > python > python3
rem       python-rtmidi has no Windows wheels for Python 3.13+.
rem    3. tools\ensure_env.py installs missing packages (first run
rem       only). If it reports that this Python is too new for
rem       python-rtmidi, .venv is rebuilt with Python 3.9-3.12 when
rem       one is installed.
rem    4. src\piano-ar\main.py runs with the shortcut/flags given.
rem  "run setup" only prepares the environment. Runs started by a
rem  double-click pause on errors so the message stays readable.
rem ============================================================
setlocal EnableExtensions
rem pushd (not cd) also works from a network share (UNC path).
pushd "%~dp0"

rem UTF-8 console: Python prints Korean and emoji, which die on cp949.
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

rem Started by a double-click or from Explorer ("cmd /c ...")?
set "KM_PAUSE="
setlocal EnableDelayedExpansion
set "KM_CL=!cmdcmdline!"
if /i not "!KM_CL:/c =!"=="!KM_CL!" (endlocal & set "KM_PAUSE=1") else endlocal

set "MAIN=src\piano-ar\main.py"
set "PY="
set "KM_VENV="
set "KM_REBUILT="
set "KM_SETUP="
set "KM_ENSURE_ARGS="
if /i "%~1"=="setup" (
    set "KM_SETUP=1"
    set "KM_ENSURE_ARGS=--force"
)

if defined KEYMENTO_PYTHON call :try_python "%KEYMENTO_PYTHON%"
if not defined PY call :try_venv .venv
if not defined PY call :try_venv venv
if not defined PY call :create_venv
if not defined PY goto :fail

:ensure
"%PY%" tools\ensure_env.py %KM_ENSURE_ARGS%
set "KM_RC=%ERRORLEVEL%"
if "%KM_RC%"=="0" goto :launch
if not "%KM_RC%"=="3" goto :fail
if not defined KM_VENV goto :fail
if defined KM_REBUILT goto :fail
rem This Python is too new for python-rtmidi: rebuild with 3.9-3.12.
set "KM_REBUILT=1"
call :find_base old
if not defined KM_BASE goto :fail
call :move_aside "%KM_VENV%"
if errorlevel 1 goto :fail
set "PY="
call :make_venv
if not defined PY goto :fail
goto :ensure

:launch
if defined KM_SETUP goto :setup_done

if /i "%~1"=="nocam"    goto :shortcut
if /i "%~1"=="demo"     goto :shortcut
if /i "%~1"=="selftest" goto :shortcut
if /i "%~1"=="ports"    goto :shortcut
if /i "%~1"=="base"     goto :shortcut
if /i "%~1"=="help"     goto :shortcut
if /i "%~1"=="/?"       goto :shortcut
goto :passthrough

rem Collect everything after the shortcut word so extra flags still reach
rem main.py -- e.g. "run base --midi-port 2".
:shortcut
set "WORD=%~1"
shift
set "REST="
:collect
if "%~1"=="" goto :dispatch
set "REST=%REST% %1"
shift
goto :collect

:dispatch
if /i "%WORD%"=="nocam"    goto :nocam
if /i "%WORD%"=="demo"     goto :demo
if /i "%WORD%"=="selftest" goto :selftest
if /i "%WORD%"=="ports"    goto :ports
if /i "%WORD%"=="base"     goto :base
goto :help

:nocam
"%PY%" %MAIN% --no-camera%REST%
goto :end

:demo
"%PY%" %MAIN% --demo%REST%
goto :end

:selftest
"%PY%" %MAIN% --demo --no-camera%REST%
goto :end

:ports
"%PY%" %MAIN% --list-midi%REST%
goto :end

:base
"%PY%" %MAIN% --detect-base%REST%
goto :end

:help
"%PY%" %MAIN% --guide
goto :end

:passthrough
"%PY%" %MAIN% %*

:end
set "KM_RC=%ERRORLEVEL%"
if not "%KM_RC%"=="0" if defined KM_PAUSE pause
popd
endlocal & exit /b %KM_RC%

:setup_done
echo [*] Keymento environment is ready: %PY%
popd
endlocal & exit /b 0

:fail
echo.
echo [!] Keymento did not start. Read the messages above.
if defined KM_PAUSE pause
popd
endlocal & exit /b 1

rem ------------------------------------------------------------
rem  Subroutines
rem ------------------------------------------------------------

rem :try_python "<python.exe>"  --  use an explicit interpreter.
:try_python
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && goto :try_python_ok
echo [!] KEYMENTO_PYTHON does not run or is older than Python 3.9: "%~1"
exit /b 1
:try_python_ok
set "PY=%~1"
exit /b 0

rem :try_venv <dir>  --  use <dir>\Scripts\python.exe if it runs here.
:try_venv
if not exist "%~1\Scripts\python.exe" exit /b 1
"%~1\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && goto :try_venv_ok
echo [!] %~1 cannot be used on this PC - broken, copied from another PC, or older than Python 3.9.
call :move_aside "%~1"
exit /b 1
:try_venv_ok
set "PY=%~1\Scripts\python.exe"
set "KM_VENV=%~1"
exit /b 0

rem :move_aside <dir>  --  rename instead of deleting.
:move_aside
set "KM_ASIDE=%~1-unused-%RANDOM%"
if exist "%KM_ASIDE%" set "KM_ASIDE=%~1-unused-%RANDOM%%RANDOM%"
echo [*] Moving %~1 aside to %KM_ASIDE% - not deleted, remove it whenever you like.
ren "%~1" "%KM_ASIDE%" >nul 2>&1 && exit /b 0
echo [!] Could not rename %~1. Close programs that use it, or delete it, then run again.
exit /b 1

rem :create_venv  --  first run: make .venv from the best Python found.
:create_venv
call :find_base any
if defined KM_BASE goto :make_venv
echo [!] Python 3.9 or newer was not found on this PC.
echo     Install Python 3.12 from https://www.python.org/downloads/
echo     keeping the "py launcher" option, or ticking "Add python.exe to PATH",
echo     then run this again.
exit /b 1

rem :make_venv  --  create .venv with %KM_BASE%.
:make_venv
if exist ".venv" call :move_aside ".venv"
if exist ".venv" exit /b 1
echo [*] Creating .venv with: %KM_BASE%
%KM_BASE% -m venv .venv
if errorlevel 1 goto :make_venv_fail
if not exist ".venv\Scripts\python.exe" goto :make_venv_fail
set "PY=.venv\Scripts\python.exe"
set "KM_VENV=.venv"
exit /b 0
:make_venv_fail
echo [!] Could not create .venv with %KM_BASE%.
exit /b 1

rem :find_base any|old  --  sets KM_BASE to a Python command.
rem   old: only 3.9-3.12 (python-rtmidi wheels exist for those).
:find_base
set "KM_BASE="
for %%V in (3.12 3.11 3.10 3.9) do if not defined KM_BASE call :probe "py -%%V" "sys.version_info >= (3, 9)"
if defined KM_BASE exit /b 0
set "KM_WANT=sys.version_info >= (3, 9)"
if /i "%~1"=="old" set "KM_WANT=(3, 9) <= sys.version_info[:2] < (3, 13)"
if /i not "%~1"=="old" call :probe "py -3" "%KM_WANT%"
if not defined KM_BASE call :probe "python" "%KM_WANT%"
if not defined KM_BASE call :probe "python3" "%KM_WANT%"
if defined KM_BASE exit /b 0
exit /b 1

rem :probe "<command>" "<condition>"  --  KM_BASE=<command> if it runs and
rem   the condition holds. Skips the Microsoft Store "python" stub, which
rem   only prints an install hint and fails.
:probe
%~1 -c "import sys; sys.exit(0 if %~2 else 1)" >nul 2>&1 && set "KM_BASE=%~1"
exit /b 0
