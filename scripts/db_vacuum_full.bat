@echo off
chcp 65001 > nul
REM ============================================================
REM  DB 공간 회수 (VACUUM FULL) — 사무실 PC 실행용 (더블클릭)
REM
REM  왜: 정리 배치(cleanup)가 raw_text·raw_response·제외 본문을 NULL로
REM      비워도 PostgreSQL은 파일 크기를 줄이지 않는다. Supabase 용량
REM      (500 MB 한도, 넘으면 읽기 전용)에 반영되려면 VACUUM FULL로
REM      raw_items·analyses 테이블을 다시 써야 한다.
REM
REM  언제: 배치가 없는 시간에만. 두 테이블을 통째로 잠그므로 수집·분석
REM      배치와 겹치면 그쪽이 기다리다 실패한다. 5~10분 걸린다.
REM      피할 시간(KST): 03:30~07:30 (정리·분석·수집), 13:30~15:30, 22:00~23:30
REM      권장: 평일 09:00~12:00 또는 16:00~21:00
REM      운영 화면(관리 ^> 데이터 관리 ^> 정리 실행)의 "복제 컬럼 비우기"를
REM      먼저 돌린 뒤 실행해야 회수량이 크다.
REM
REM  준비: scripts\.env 에 SUPABASE_DB_URL (기존 배치와 동일). 처음이면
REM      scripts\.venv 가 있어야 한다 (python -m venv .venv; pip install -r requirements.txt)
REM  GitHub Actions에서는 돌리지 않는다 (cleanup.py --vacuum-full 이 거부한다).
REM ============================================================
cd /d "%~dp0"
set PYTHONPATH=%~dp0
".\.venv\Scripts\python.exe" -m kiro_batch.cleanup --vacuum-full
echo.
echo ============================================
echo  끝났습니다. 위의 '회수 MB'를 확인하세요.
echo  운영 화면(관리 ^> 데이터 관리)의 용량 수치는
echo  새로고침하면 바로 반영됩니다. 이 창은 닫아도 됩니다.
echo ============================================
pause
