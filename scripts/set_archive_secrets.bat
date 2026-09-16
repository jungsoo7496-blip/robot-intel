@echo off
chcp 65001 > nul
REM ============================================================
REM  옛 비공개 저장소(robotreport)에 금고 미러용 비밀값 2개 등록 — 더블클릭
REM
REM  주간 백업(backup.yml)의 미러 단계가 Supabase 파일 칸에서 금고 파일을
REM  내려받아 이 저장소의 Release에 영구 보관한다. 그때 쓰는 열쇠 2개다.
REM  값은 이 PC 안에서만 움직인다. 준비: gh 로그인 (gh auth login --web)
REM ============================================================
cd /d "%~dp0"
set REPO=jungsoo7496-blip/robotreport
set TMPFILE=%TEMP%\archive-secrets.env

gh auth status >nul 2>&1
if errorlevel 1 (
  echo  gh 로그인이 안 되어 있습니다. PowerShell 에서 먼저: gh auth login --web
  pause
  exit /b 1
)
if not exist ".env" (
  echo  .env 파일이 없습니다. scripts 폴더에서 실행해야 합니다.
  pause
  exit /b 1
)

findstr /B /L /C:"NEXT_PUBLIC_SUPABASE_URL=" /C:"SUPABASE_SERVICE_ROLE_KEY=" .env > "%TMPFILE%"
for /f %%n in ('find /c /v "" ^< "%TMPFILE%"') do set COUNT=%%n
echo  .env 에서 %COUNT%개 찾음 - 2개여야 함
if not "%COUNT%"=="2" (
  echo  개수가 맞지 않습니다. .env 를 확인하세요.
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
echo  등록된 비밀값 목록 - 이름만 보입니다:
echo ============================================
gh secret list -R %REPO%
echo.
echo  끝났습니다. NEXT_PUBLIC_SUPABASE_URL 과 SUPABASE_SERVICE_ROLE_KEY 가 보이면 성공입니다.
pause
