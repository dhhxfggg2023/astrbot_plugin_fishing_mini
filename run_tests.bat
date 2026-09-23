@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 测试里有大量 ✅/❌ 输出：不强制 UTF-8 的话，在默认 GBK 控制台上会直接
REM UnicodeEncodeError 崩掉（test_backup_index / test_ci_workflow 都踩过）
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set OUT=%~dp0test_results.txt
echo ==== plugin test run %DATE% %TIME% ==== > "%OUT%"
echo cwd=%CD% >> "%OUT%"
echo. >> "%OUT%"

REM --- find a python interpreter ---
set PYExe=
where python >nul 2>nul && set PYExe=python
if not defined PYExe (
  where py >nul 2>nul && set PYExe=py -3
)
if not defined PYExe (
  echo [python not found on PATH - trying common locations] >> "%OUT%"
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python310"
    "C:\Python312"
    "C:\Python311"
  ) do (
    if exist "%%~D\python.exe" set PYExe="%%~D\python.exe"
  )
)

if not defined PYExe (
  echo ERROR: no python interpreter found. >> "%OUT%"
  echo ERROR: no python interpreter found.
  goto done
)

echo Using interpreter: %PYExe% >> "%OUT%"
echo. >> "%OUT%"

echo ---- test_local.py (full) ---- >> "%OUT%"
%PYExe% test_local.py >> "%OUT%" 2>&1
echo test_local exit=%ERRORLEVEL% >> "%OUT%"

echo. >> "%OUT%"
echo ---- node test_editor_ui.js ---- >> "%OUT%"
where node >nul 2>nul
if %ERRORLEVEL%==0 (
  node test_editor_ui.js >> "%OUT%" 2>&1
  echo editor_ui exit=%ERRORLEVEL% >> "%OUT%"
) else (
  echo [node not found on PATH - skipped] >> "%OUT%"
)

echo. >> "%OUT%"
echo ==== done ==== >> "%OUT%"

:done
echo.
echo Done. Results are in: test_results.txt
echo Tell Claude: "read test_results.txt"
echo.
pause
