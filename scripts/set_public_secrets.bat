@echo off
chcp 65001 > nul
REM ============================================================
REM  공개 저장소(robot-intel)에 비밀값 14개 등록 — 더블클릭
REM
REM  이 폴더의 .env 에서 14개 이름만 골라 gh 로 올린다.
REM  (2026-09-17: 파일 금고용 NEXT_PUBLIC_SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY 추가 — cleanup.yml이 쓴다)
REM  값은 이 PC 안에서만 움직인다 (화면에도, 다른 곳에도 안 나간다).
REM  준비: gh 로그인 (PowerShell에서: gh auth login --web)
REM ============================================================
cd /d "%~dp0"
set REPO=jungsoo7496-blip/robot-intel
set TMPFILE=%TEMP%\robot-intel-secrets.env

gh auth status >nul 2>&1
if errorlevel 1 (
  echo.
  echo  gh 로그인이 안 되어 있습니다. PowerShell 에서 먼저 실행하세요:
  echo.
  echo      gh auth login --web
  echo.
  pause
  exit /b 1
)

if not exist ".env" (
  echo  .env 파일이 없습니다. scripts 폴더에서 실행해야 합니다.
  pause
  exit /b 1
)

findstr /B /L /C:"DATA_GO_KR_API_KEY=" /C:"GEMINI_API_KEY=" /C:"NANET_DETAIL_API_KEY=" /C:"NAVER_CLIENT_ID=" /C:"NAVER_CLIENT_SECRET=" /C:"NEXT_PUBLIC_SUPABASE_URL=" /C:"NKIS_API_KEY=" /C:"PRISM_API_KEY=" /C:"REPORT_RELAY_TOKEN=" /C:"SCIENCEON_AUTH_KEY=" /C:"SCIENCEON_CLIENT_ID=" /C:"SCIENCEON_MAC_ADDRESS=" /C:"SUPABASE_DB_URL=" /C:"SUPABASE_SERVICE_ROLE_KEY=" .env > "%TMPFILE%"

for /f %%n in ('find /c /v "" ^< "%TMPFILE%"') do set COUNT=%%n
echo  .env 에서 %COUNT%개 찾음 (14개여야 함)
if not "%COUNT%"=="14" (
  echo  개수가 맞지 않습니다. .env 에 빠진 이름이 있는지 확인하세요.
  del "%TMPFILE%"
  pause
  exit /b 1
)

echo  %REPO% 에 올리는 중...
gh secret set -R %REPO% -f "%TMPFILE%"
set RESULT=%errorlevel%
del "%TMPFILE%"

echo.
if not "%RESULT%"=="0" (
  echo  실패했습니다 - 위 메시지를 확인하세요.
  pause
  exit /b %RESULT%
)

echo ============================================
echo  등록된 비밀값 목록 (이름만 보입니다):
echo ============================================
gh secret list -R %REPO%
echo.
echo  끝났습니다. 14개가 보이면 성공입니다. 이 창은 닫아도 됩니다.
pause
