"""LLM 연결 모듈 (Anthropic Messages API 방식, 표준 라이브러리만 사용).

환경변수:
  ANTHROPIC_BASE_URL  접속 주소 (필수). 요청은 {BASE_URL}/v1/messages 로 보낸다.
  ANTHROPIC_API_KEY   API 키 (필수). 코드·로그에 출력하지 않는다.
  ANTHROPIC_MODEL     모델 이름 (선택, 기본값 DEFAULT_MODEL)

작업 폴더의 .env 파일이 있으면 읽어 들이되, 이미 설정된 환경변수는 덮어쓰지 않는다.

사용 예:
  from llm_client import ask, LLMError
  try:
      text = ask("안녕")
  except LLMError as e:
      ...  # 규칙 기반 결과만 출력하는 경로로 대체

연결 테스트:
  python llm_client.py "짧은 테스트 메시지"
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "claude-sonnet-5-5"
API_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 60


class LLMError(Exception):
    """LLM 호출 실패. 메시지에 API 키를 포함하지 않는다."""


def load_env(path=None):
    """KEY=VALUE 형식의 .env 파일을 읽어 os.environ에 없는 값만 채운다."""
    env_path = Path(path) if path else Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _config():
    load_env()
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    missing = [name for name, val in (("ANTHROPIC_BASE_URL", base_url),
                                      ("ANTHROPIC_API_KEY", api_key)) if not val]
    if missing:
        raise LLMError(f"환경변수 누락: {', '.join(missing)}")
    return base_url, api_key, model


def ask(prompt, system=None, max_tokens=1024, model=None, timeout=DEFAULT_TIMEOUT):
    """프롬프트를 보내고 응답 텍스트를 반환한다. 실패 시 LLMError."""
    base_url, api_key, default_model = _config()
    body = {
        "model": model or default_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    url = base_url if base_url.endswith("/v1/messages") else f"{base_url}/v1/messages"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise LLMError(f"HTTP {e.code}: {detail.replace(api_key, '***')}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"연결 실패: {e}") from None
    except json.JSONDecodeError:
        raise LLMError("응답이 JSON 형식이 아님") from None

    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    if not parts:
        raise LLMError("응답에 텍스트가 없음")
    return "".join(parts)


if __name__ == "__main__":
    message = " ".join(sys.argv[1:]) or "연결 테스트입니다. 'OK'라고만 답해주세요."
    try:
        print(ask(message, max_tokens=50))
    except LLMError as err:
        print(f"[LLM 오류] {err}", file=sys.stderr)
        sys.exit(1)
