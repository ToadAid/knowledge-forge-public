@echo off
rem ToadAid Knowledge Forge - native Windows ordinary installer (cmd.exe transport)
rem
rem Thin transport only. ALL installer policy lives in the Python standard-library
rem core next to the package source:
rem
rem   src\knowledge_forge\windows_installer.py
rem
rem This wrapper selects ONE interpreter by an explicit rule, refuses Python older
rem than 3.10, runs the core isolated (-I: no PYTHON* variables, no user site,
rem no script-directory imports; -B: no bytecode writes), forwards the operator's
rem arguments and preserves the exit code. It is meant to be typed by the human
rem installing Knowledge Forge. The two commands it installs are .exe launchers
rem that never pass agent arguments through cmd.exe. It installs and runs NO
rem operator, needs NO administrator rights, and never edits PATH or the registry.
rem
rem Interpreter rule:
rem   1. KNOWLEDGE_FORGE_PYTHON = absolute path to python.exe, if set
rem   2. otherwise the Windows Python Launcher:  py -3
rem   3. otherwise the first "python" on PATH
setlocal EnableExtensions DisableDelayedExpansion
set "KF_CORE=%~dp0..\src\knowledge_forge\windows_installer.py"
set "KF_VERSION_CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 10) else 3)"

if defined KNOWLEDGE_FORGE_PYTHON goto use_explicit
where py >nul 2>nul
if not errorlevel 1 goto use_launcher
where python >nul 2>nul
if not errorlevel 1 goto use_path_python
>&2 echo knowledge-forge-install-agent: Python 3.10 or newer was not found. Install Python, or set KNOWLEDGE_FORGE_PYTHON to the full path of python.exe.
exit /b 2

:use_explicit
"%KNOWLEDGE_FORGE_PYTHON%" -I -B -c "%KF_VERSION_CHECK%" >nul 2>nul
if errorlevel 1 goto unsupported
"%KNOWLEDGE_FORGE_PYTHON%" -I -B "%KF_CORE%" %*
exit /b %ERRORLEVEL%

:use_launcher
py -3 -I -B -c "%KF_VERSION_CHECK%" >nul 2>nul
if errorlevel 1 goto unsupported
py -3 -I -B "%KF_CORE%" %*
exit /b %ERRORLEVEL%

:use_path_python
python -I -B -c "%KF_VERSION_CHECK%" >nul 2>nul
if errorlevel 1 goto unsupported
python -I -B "%KF_CORE%" %*
exit /b %ERRORLEVEL%

:unsupported
>&2 echo knowledge-forge-install-agent: the selected Python is missing or older than 3.10 (nothing was written).
exit /b 2
