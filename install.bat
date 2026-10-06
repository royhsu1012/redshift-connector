@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  redshift-connector-bade : one-click setup for Windows
rem
rem    1. installs / upgrades the package (the JDBC driver is bundled inside it)
rem    2. checks Java and the driver
rem    3. if Java is missing, installs Eclipse Temurin JRE 21 through winget
rem
rem  It installs into whichever Python is active. To target a conda env or a
rem  venv, activate it first and run this file from that prompt.
rem
rem  Usage: install.bat [pip-target]      default: redshift-connector-bade
rem ---------------------------------------------------------------------------

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=redshift-connector-bade"
set "EXITCODE=1"

set "PY="
python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY goto :no_python

echo.
echo [1/2] Installing %TARGET% ...
%PY% -m pip install --upgrade "%TARGET%"
if errorlevel 1 goto :pip_failed

echo.
echo [2/2] Checking Java and the bundled driver ...
%PY% -m redshift_connector_bade
set "RC=%errorlevel%"
if "%RC%"=="0" goto :ok
if not "%RC%"=="2" goto :check_failed

echo.
echo Java was not found. Installing Eclipse Temurin JRE 21 with winget ...
where winget >nul 2>nul || goto :no_winget
winget install -e --id EclipseAdoptium.Temurin.21.JRE --source winget
if errorlevel 1 goto :java_failed

echo.
echo Checking again ...
%PY% -m redshift_connector_bade
if errorlevel 1 goto :reopen

:ok
echo.
echo ============================================================
echo  Done. Use it from Python:
echo.
echo    from redshift_connector_bade import RedshiftClient
echo    df = RedshiftClient("jdbc:redshift://...").query("select 1")
echo ============================================================
set "EXITCODE=0"
goto :end

:no_python
echo Python was not found. Install Python 3.8 or newer from
echo https://www.python.org/downloads/ and run this file again.
goto :end

:pip_failed
echo.
echo pip could not install the package. Check the network / proxy and try again.
goto :end

:check_failed
echo.
echo The package was installed but the self-check failed. See the message above.
goto :end

:no_winget
echo winget is not available on this PC. Install a Java runtime (version 8 or newer)
echo manually, for example from https://adoptium.net/temurin/releases/ ,
echo and run this file again.
goto :end

:java_failed
echo.
echo winget could not install Java. Install a Java runtime (version 8 or newer)
echo manually, for example from https://adoptium.net/temurin/releases/ ,
echo and run this file again.
goto :end

:reopen
echo.
echo Java was installed but is not visible to this window yet.
echo Close this window and run install.bat once more.
goto :end

:end
echo.
pause
exit /b %EXITCODE%
