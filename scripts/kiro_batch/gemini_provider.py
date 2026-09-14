"""AIProvider 인터페이스와 GeminiProvider (AIR-004, tasks §8.1).

- 모델명은 설정값으로 주입한다. 다른 공급자 자동 폴백은 Phase 1 제외.
- 테스트에서는 AIProvider를 구현한 모의 공급자를 주입한다.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


class RateLimitError(Exception):
    """429 — 무료 한도 초과. 실패가 아니라 지연으로 처리한다 (설계 §3.2)."""


class ProviderServerError(Exception):
    """5xx 등 일시적 서버 오류 — 지수 백오프 재시도 대상."""


class InvalidResponseError(Exception):
    """JSON 파싱 불가 등 응답 형식 오류 — 자동 재시도 대상 (FR-007)."""


@dataclass
class AIResult:
    data: dict
    model_name: str
    input_tokens: int | None
    output_tokens: int | None
    raw_text: str
    # 응답 metadata의 실제 서빙 모델 버전 (telemetry 0019 — 기본 None 호환)
    served_model: str | None = None


class AIProvider(ABC):
    """기사 분석과 브리프 섹션 생성을 분리한 공급자 인터페이스."""

    @abstractmethod
    def analyze_article(self, prompt: str, model_name: str) -> AIResult: ...

    @abstractmethod
    def generate_brief_section(self, prompt: str, model_name: str) -> AIResult: ...


class GeminiProvider(AIProvider):
    # 단일 호출이 무한정 매달리면 배치 예산이 통째로 날아간다 — 실측으로
    # 274초·300초 무응답이 확인돼(7회 중 2회) 배치 처리량이 33건→12건으로
    # 떨어졌다. 조간 배치는 하루 한 번뿐이라 여기서 정지하면 그날 아침이 빈다.
    REQUEST_TIMEOUT_MS = 45_000

    def __init__(self, api_key: str):
        # 지연 import: 테스트 환경에서 SDK 없이도 다른 모듈을 사용할 수 있게 한다
        from google import genai
        from google.genai import types

        self._genai = genai
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=self.REQUEST_TIMEOUT_MS),
        )

    def _generate_json(self, prompt: str, model_name: str, temperature: float) -> AIResult:
        from google.genai import errors, types

        try:
            response = self._client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                ),
            )
        except errors.APIError as e:
            code = getattr(e, "code", None) or getattr(e, "status_code", None)
            if code == 429:
                raise RateLimitError(str(e)) from e
            if code is not None and int(code) >= 500:
                raise ProviderServerError(str(e)) from e
            raise

        text = response.text or ""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise InvalidResponseError(f"JSON 파싱 실패: {e}") from e
        if not isinstance(data, dict):
            raise InvalidResponseError("응답이 JSON 객체가 아님")

        usage = getattr(response, "usage_metadata", None)
        return AIResult(
            data=data,
            model_name=model_name,
            input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
            raw_text=text,
            served_model=getattr(response, "model_version", None),
        )

    def analyze_article(self, prompt: str, model_name: str) -> AIResult:
        return self._generate_json(prompt, model_name, temperature=0.2)

    def generate_brief_section(self, prompt: str, model_name: str) -> AIResult:
        return self._generate_json(prompt, model_name, temperature=0.4)
