"""KIRO 로봇 인텔리전스 배치 패키지.

수집 → 정규화 → 로컬 필터 → 클러스터링 → 큐 → Gemini 분석 → 게시
파이프라인을 GitHub Actions에서 실행한다.

진입점:
    python -m kiro_batch.collect   # 수집·필터·클러스터·큐 등록
    python -m kiro_batch.analyze   # 잠금 복구 + 분석 큐 처리
"""
