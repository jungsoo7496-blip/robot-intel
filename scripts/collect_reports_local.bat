@echo off
chcp 65001 > nul
REM ============================================================
REM  보고서 수집 — 사무실 PC 실행용 (더블클릭)
REM
REM  용도: POINT(국립중앙도서관)는 데이터센터 IP를 차단해서
REM  GitHub 자동 수집(collect-reports.yml)은 POINT를 아예 건너뛴다
REM  (REPORT_SKIP_SOURCES=point). 이 PC(국내 IP)가 POINT의 유일한
REM  수집 경로다. 주 1회면 충분하다.
REM  Gemini 분석은 자동 배치가 이어서 한다.
REM ============================================================
cd /d "%~dp0"
set PYTHONPATH=%~dp0
REM 실행 단위는 '채널'이 아니라 '(수집원 하위 갈래 × 검색어) 조합'이다
REM (2026-09-08). 지금 조합은 50개 — 검색어를 쓰는 갈래 7개 × 보고서
REM 검색어 7개 + 검색어를 안 쓰는 prism 1개(그중 POINT가 14개).
REM (데이터베이스 변경 26 적용 전에는 옛 방식대로 채널 24개가 돈다.)
REM 여기서는 한 번에 전부 돌도록 여유 있게 60으로 둔다.
set REPORT_MAX_TASKS_PER_RUN=60
REM 시간 예산(기본 480초=8분)은 GitHub 실행이 15분 제한에 걸리지 않게
REM 하는 값이라, 이 PC에서 그대로 쓰면 절반쯤에서 멈춘다. 여기는 제한이
REM 없으므로 30분으로 늘린다. 보통 10~15분이면 끝나고, 시간이 다 되면
REM 남은 조합은 다음 실행이 이어서 한다(오래 안 돌린 조합부터).
set REPORT_FETCH_BUDGET_SECONDS=1800
echo  보고서 수집을 시작합니다. 10~30분 걸릴 수 있으니 창을 닫지 마세요.
echo.
".\.venv\Scripts\python.exe" -m kiro_batch.collect_reports
echo.
echo ============================================
echo  수집이 끝났습니다. 이 창은 닫아도 됩니다.
echo ============================================
pause
