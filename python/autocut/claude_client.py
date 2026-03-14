"""Claude Code CLI 래퍼 — claude -p (pipe mode) subprocess 호출

Stage 2 비전 태깅과 LLM 편집을 Claude로 수행할 때 사용.
기존 Ollama 파이프라인과 병렬로 동작하며, ai_engine="claude" 옵션 시 활성화.
"""

import json
import os
import subprocess
import sys
import time


def _log(msg: str):
    print(f"[claude] {msg}", file=sys.stderr, flush=True)


def _build_env() -> dict:
    """중첩 세션 방지를 위해 CLAUDECODE 환경 변수를 해제한 env 반환"""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def check_claude_available() -> bool:
    """claude CLI가 설치되어 있는지 확인"""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=_build_env(),
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def call_claude_text(
    prompt: str,
    model: str = "sonnet",
    json_schema: dict | None = None,
    timeout: int = 300,
    max_retries: int = 2,
) -> str:
    """Claude -p로 텍스트 프롬프트 전송, 응답 텍스트 반환

    json_schema가 주어지면 --json-schema로 구조화 출력을 강제하고,
    응답 JSON의 result 필드를 반환한다.
    """
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]
    if json_schema:
        cmd.extend(["--json-schema", json.dumps(json_schema)])

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_build_env(),
            )

            if result.returncode != 0:
                _log(f"Claude 호출 실패 (attempt {attempt+1}): {result.stderr[:200]}")
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return ""

            data = json.loads(result.stdout)
            # json_schema 사용 시 structured_output 필드에 파싱된 객체가 들어옴
            if json_schema and data.get("structured_output"):
                return json.dumps(data["structured_output"], ensure_ascii=False)
            return data.get("result", "")

        except subprocess.TimeoutExpired:
            _log(f"Claude 타임아웃 ({timeout}초, attempt {attempt+1})")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return ""
        except json.JSONDecodeError:
            _log(f"Claude 응답 JSON 파싱 실패 (attempt {attempt+1})")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return ""
        except Exception as e:
            _log(f"Claude 호출 예외 (attempt {attempt+1}): {e}")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return ""

    return ""


def call_claude_vision(
    prompt: str,
    image_paths: list[str],
    model: str = "sonnet",
    timeout: int = 180,
    max_retries: int = 2,
) -> str:
    """Claude -p로 이미지 분석 요청

    --tools "Read"로 Read 도구만 허용하여 이미지 파일을 읽게 한다.
    프롬프트에 이미지 경로를 포함시켜 Claude가 Read 도구로 접근하도록 유도.
    """
    image_list = "\n".join(f"- {path}" for path in image_paths)
    full_prompt = (
        f"다음 이미지 파일들을 Read 도구로 읽어서 분석하세요.\n\n"
        f"{image_list}\n\n"
        f"{prompt}"
    )

    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_build_env(),
            )

            if result.returncode != 0:
                _log(f"Claude Vision 호출 실패 (attempt {attempt+1}): {result.stderr[:200]}")
                if attempt < max_retries:
                    time.sleep(2)
                    continue
                return ""

            data = json.loads(result.stdout)
            return data.get("result", "")

        except subprocess.TimeoutExpired:
            _log(f"Claude Vision 타임아웃 ({timeout}초, attempt {attempt+1})")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return ""
        except json.JSONDecodeError:
            _log(f"Claude Vision 응답 JSON 파싱 실패 (attempt {attempt+1})")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return ""
        except Exception as e:
            _log(f"Claude Vision 호출 예외 (attempt {attempt+1}): {e}")
            if attempt < max_retries:
                time.sleep(2)
                continue
            return ""

    return ""
