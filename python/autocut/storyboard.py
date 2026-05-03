"""장면 기반 내러티브 편집 -- 2-Pass LLM (CUT-only 출력)

Pass 1 (기획): 전체 스토리보드를 검토하여 콘텐츠 분석 + 편집 방향 수립
Pass 2 (편집): 버릴 장면(CUT/PARTIAL)만 출력, 언급하지 않은 장면은 자동 KEEP
"""

import json
import os
import re
import signal
import sys
import time

import httpx

from scene_detector import (
    TRAIL_KEYWORDS,
    generate_compact_storyboard,
    generate_narrative_storyboard,
    window_has_speech,
)

EDITING_MODEL = "qwen3:14b"
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


def _log(msg: str):
    print(f"[storyboard] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Pass 1: 기획 프롬프트 — 콘텐츠 분석 + 편집 방향 수립
# ---------------------------------------------------------------------------

PLANNING_PROMPT_TEMPLATE = """당신은 아웃도어(캠핑/등산/백패킹) 브이로그 전문 편집자입니다.
아래 스토리보드는 원본 __TOTAL_DURATION__ 영상의 전체 장면 목록입니다.
편집을 시작하기 전에, 전체 영상을 검토하고 편집 계획을 세우세요.

## 분석 항목

1. **콘텐츠 유형 파악**: 이 영상은 주로 무엇에 대한 영상인가? (캠핑, 등산, 백패킹, 요리, 장비 리뷰 등)
2. **핵심 내러티브 정리**: 영상의 시작부터 끝까지 어떤 이야기가 펼쳐지는가? 주요 흐름을 정리
3. **반드시 남겨야 할 장면**: 이 영상에서 빼면 안 되는 핵심 장면들 (장면 번호와 이유)
4. **제거 가능한 장면**: 반복되거나 서사에 불필요한 장면들 (장면 번호와 이유)

## 출력 형식

분석 내용을 서술한 후, 마지막에 반드시 아래 형식으로 결론을 작성하세요:

=== 편집 계획 ===
콘텐츠 유형: (한 줄 요약)
핵심 내러티브: (2~3줄)
편집 방향: (2~3줄, 어떤 장면을 중심으로 남기고 어떤 장면을 줄일지)

=== 스토리보드 ===
__STORYBOARD__"""


# ---------------------------------------------------------------------------
# Pass 2: 편집 프롬프트 — CUT/PARTIAL만 출력, 나머지 자동 KEEP
# ---------------------------------------------------------------------------

EDITING_PROMPT_TEMPLATE = """당신은 아웃도어(캠핑/등산/백패킹) 브이로그 전문 편집자입니다.
NG 장면은 이미 제거되었습니다. 사용 가능한 장면들로 편집본을 만드세요.

## 편집 계획 (사전 분석 결과)

__PLANNING_RESULT__

## 편집 방식

원본 영상은 총 __TOTAL_DURATION__입니다.
__DURATION_GUIDE__
__KEEP_BIAS__
**버릴 장면(cut/partial)만 출력하세요. 출력하지 않은 장면은 자동으로 전체 KEEP 처리됩니다.**

## 편집 원칙

1. 내러티브 흐름:
   - 도착→셋업→활동→식사→불멍→마무리의 자연스러운 흐름 유지
   - 등산/백패킹: 출발→트레일→뷰포인트→정상/목적지→캠프→하산 흐름 유지
   - 시간대 전환(낮→저녁→밤→아침)을 보여주는 장면은 출력하지 마세요 (자동 KEEP)

2. 장면 전환과 리듬감:
   - 잦은 장면 전환이 지루함을 방지한다
   - 60초 이상 비말소리 장면은 PARTIAL로 핵심만 남기기 (필수 제한 참조)
   - ★ 말소리 장면은 길어도 PARTIAL하지 마세요 — 자동 KEEP이 안전합니다
   - 토크/활동 장면 사이에 짧은 풍경 컷을 배치하면 리듬감이 생긴다

3. 크롭 추천:
   - wide 샷이 길게 이어지면 일부 윈도우에 "crop" 힌트를 붙인다
   - 크롭(확대)된 장면이 섞이면 영상이 고급스러워진다
   - 특히 요리 클로즈업, 불꽃 디테일, 음식 플레이팅에 추천

4. 반복 제거 (엄격 기준):
   - "반복"이란 같은 활동 + 같은 장소 + 같은 시간대에서 동일한 행동이 계속되는 경우만 해당
   - 같은 라벨(예: cooking)이라도 시간대나 장소가 다르면 반복이 아님 (아침 요리 vs 저녁 요리)
   - 요리: 준비→조리→플레이팅→먹기는 각각 다른 단계이므로 반복 아님
   - 확실한 반복만 CUT하고, 애매하면 KEEP (남기는 것이 안전)

5. 말소리(★) 보호 — 이 규칙은 규칙 2, 9의 길이 제한보다 우선합니다:
   - ★ 표시 장면은 출력하지 마세요 (자동 KEEP)
   - ★ 장면이 60초, 120초 이상이어도 PARTIAL하지 마세요. 말소리는 전부 살립니다
   - 유일한 예외: 완전히 같은 내용을 반복하는 ★ 장면만 CUT 가능
   - 대사 내용이 포함된 경우, 내용의 중요도를 판단에 활용하세요
     (장비 설명, 요리 해설, 감상 토크 = 높은 가치 / 단순 감탄사 = 낮은 가치)

6. 아웃도어 핵심 장면 보호 — 아래 장면들은 CUT하지 마세요:
   - 텐트/타프 설치(setting_up): 시작→완성 과정 (길면 PARTIAL)
   - 풍경/전망(scenery): 뷰포인트, 정상, 전경 — 20~30초 (길면 PARTIAL)
   - 야경/불멍(dark/fire_tending): 캠핑 핵심 분위기 — 20~30초 (길면 PARTIAL)
   - 트레일/등산로(walking+풍경 배경): 이동 자체가 콘텐츠 (길면 PARTIAL)
   - 장비 소개(showing_gear): 패킹/언패킹/장비 리뷰
   - 도착/출발 순간: 목적지 도착, 정상 도달, 캠핑장 입성
   - 요리/식사: 야외 요리는 핵심 콘텐츠, 각 끼니 대표 장면

7. 이동(walking/driving) — 적극 CUT 대상:
   - 주차장/도로/평지 단순 이동 → CUT
   - 차량 운전(driving) → CUT (말소리 있으면 PARTIAL, 말소리 윈도우 전부 유지)
   - 산길/숲길/트레일 하이킹 → PARTIAL (keep_windows 2~3개)
   - 도착/정상 도달 순간 → 출력하지 마세요 (자동 KEEP)
   - 말소리 있는 이동 → 출력하지 마세요 (자동 KEEP)

8. 이런 장면을 CUT하세요:
   - 빼도 서사에 지장 없는 장면
   - 동일 활동 반복 (대표 제외)
   - 대기/정지 장면 (말소리 없고 변화 없음)
   - 주차장/도로 단순 이동, 차량 운전(driving)
   - 같은 활동이 이미 다른 장면에서 유지되는 경우

9. PARTIAL 활용 (긴 비말소리 장면에 사용):
   - ★ 말소리 장면은 PARTIAL 대상이 아닙니다 (규칙 5 참조)
   - 비말소리 장면이 120초 이상이면 PARTIAL 고려
   - keep_windows로 남길 핵심 윈도우 번호만 지정 (시작/하이라이트/끝)
   - 요리: 준비→조리→완성 각 단계 대표 (최대 12개)
   - 셋업(setting_up): 과정 전체 (최대 12개)
   - 트레일 이동: 대표 풍경 (최대 6개 윈도우)
   - 풍경/야경: 분위기 전달 (최대 6개 윈도우)

## 필수 제한 (코드로도 강제됩니다)

- 차량 운전(driving): CUT (말소리 있으면 PARTIAL, 말소리 윈도우 전부 유지)
- 단순 이동(walking, 풍경 없음): CUT. 트레일/등산은 예외로 PARTIAL(최대 6윈도우)
- 풍경(scenery) 장면: 최소 1개는 CUT하지 마세요 — 최대 6윈도우 (60초)
- 야경/불멍(dark/fire_tending): 최소 1개는 CUT하지 마세요 — 최대 6윈도우 (60초)
- 셋업(setting_up): 최소 1개는 CUT하지 마세요 — 최대 12윈도우 (120초)
- 하나의 PARTIAL에 keep_windows 30개(300초) 초과 금지

## 출력

먼저 편집 판단 근거를 간단히 서술하세요.
그 후 === JSON === 구분자 아래에 **제거할 장면만** JSON 배열로 출력하세요.
**출력하지 않은 장면은 자동으로 전체 KEEP 처리됩니다.**

[
  {"scene": 5, "decision": "cut", "reason": "S04와 같은 요리 대기, 변화 없음"},
  {"scene": 8, "decision": "partial", "keep_windows": [30,31,48,49], "hint": "crop:48,49", "reason": "3분 요리 중 시작/완성만, 완성 클로즈업 크롭 추천"},
  {"scene": 12, "decision": "cut", "reason": "S11과 동일 활동 반복"},
  ...
]

hint 필드 (선택):
- "crop:윈도우번호들" -- 해당 윈도우에 크롭(확대) 편집 추천
- "insert" -- 활동 사이 분위기 인서트 컷으로 활용

=== 스토리보드 ===
__STORYBOARD__"""


# ---------------------------------------------------------------------------
# Ollama 유틸리티 (기존 유지)
# ---------------------------------------------------------------------------

def _check_model_available(model: str) -> bool:
    """Ollama에 모델이 존재하는지 확인"""
    try:
        resp = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=10.0)
        if resp.status_code != 200:
            _log(f"Ollama 연결 실패: HTTP {resp.status_code}")
            return False
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        available = any(model in m for m in models)
        if not available:
            _log(f"모델 '{model}' 없음. 사용 가능 모델: {models}")
        return available
    except Exception as e:
        _log(f"Ollama 연결 실패: {e}")
        return False


def _unload_other_models():
    """편집 모델 외 다른 모델을 메모리에서 해제"""
    try:
        resp = httpx.get(f"{OLLAMA_URL}/api/ps", timeout=10.0)
        if resp.status_code != 200:
            return
        loaded = resp.json().get("models", [])
        for m in loaded:
            name = m.get("name", "")
            if EDITING_MODEL not in name:
                size_gb = m.get("size", 0) / 1e9
                _log(f"모델 해제: {name} ({size_gb:.1f}GB)")
                httpx.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": name, "keep_alive": 0},
                    timeout=10.0,
                )
        if not loaded:
            _log("로드된 모델 없음")
    except Exception as e:
        _log(f"모델 해제 실패: {e}")


def _warmup_model() -> bool:
    """편집 모델 워밍업 -- 60초 내 응답 가능한지 확인"""
    _log(f"모델 워밍업: {EDITING_MODEL}")
    try:
        t0 = time.time()
        response = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": EDITING_MODEL,
                "prompt": "OK라고만 답하세요.",
                "stream": False,
                "options": {"num_predict": 10},
            },
            timeout=60.0,
        )
        elapsed = time.time() - t0
        if response.status_code == 200:
            result = response.json().get("response", "").strip()[:30]
            _log(f"워밍업 성공: '{result}' ({elapsed:.1f}s)")
            return True
        _log(f"워밍업 실패: HTTP {response.status_code} ({elapsed:.1f}s)")
        return False
    except httpx.TimeoutException:
        _log("워밍업 타임아웃 (60s) -- 모델이 너무 느림")
        return False
    except Exception as e:
        _log(f"워밍업 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# LLM 오류 / 일시정지 (기존 유지)
# ---------------------------------------------------------------------------

def _emit_llm_warning(message: str, stage: str = "editing", percent: int = 85):
    """LLM 오류 경고를 progress 이벤트로 전송 (llm_error 플래그 포함)"""
    data = {
        "type": "progress",
        "stage": stage,
        "percent": percent,
        "message": f"⚠ {message} -- Ollama 상태 확인 후 재개하세요.",
        "llm_error": True,
    }
    print(json.dumps(data, ensure_ascii=False), flush=True)


def _pause_for_llm_error(error_msg: str, stage: str = "editing", percent: int = 85):
    """LLM 오류 시 경고를 띄우고 프로세스 일시정지 (SIGSTOP)

    사용자가 Ollama를 확인하고 재개(SIGCONT)하면 자동으로 이어서 실행됩니다.
    """
    _log(f"LLM 오류 -- 일시정지: {error_msg}")
    _emit_llm_warning(error_msg, stage, percent)
    os.kill(os.getpid(), signal.SIGSTOP)
    _log("일시정지 해제 -- 재시도 준비")


def _strip_think_tags(text: str) -> str:
    """Qwen3 등 thinking 모드 모델의 <think>...</think> 태그 제거"""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# LLM 호출
# ---------------------------------------------------------------------------

def _call_llm(prompt: str) -> str:
    """LLM 호출 (스트리밍 모드 -- 토큰 간 120초 타임아웃)"""
    prompt_len = len(prompt)
    _log(f"LLM 호출 시작: 프롬프트 {prompt_len}자 (~{prompt_len // 3} 토큰 추정)")
    chunks: list[str] = []
    try:
        t0 = time.time()
        with httpx.stream(
            "POST",
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": EDITING_MODEL,
                "prompt": prompt,
                "stream": True,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 16384,
                    "num_ctx": 32768,
                },
            },
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0),
        ) as response:
            if response.status_code != 200:
                elapsed = time.time() - t0
                _log(f"LLM HTTP 오류: {response.status_code} ({elapsed:.1f}s)")
                return ""
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("response", "")
                    chunks.append(token)
                    if data.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue

        elapsed = time.time() - t0
        result = "".join(chunks)
        result = _strip_think_tags(result)
        _log(f"LLM 응답: {len(result)}자, {len(chunks)} 토큰, {elapsed:.1f}s")
        if not result:
            _log("LLM 빈 응답")
        elif len(result) < 20:
            _log(f"LLM 응답 너무 짧음: '{result}'")
        return result
    except httpx.TimeoutException:
        elapsed = time.time() - t0
        partial = "".join(chunks)
        _log(f"LLM 타임아웃 ({elapsed:.1f}s). 토큰 간 120초 무응답. 받은 {len(partial)}자")
        if partial:
            partial = _strip_think_tags(partial)
            _log(f"부분 응답 사용 시도: {partial[:100]}...")
            return partial
        return ""
    except httpx.ConnectError:
        _log("LLM 연결 실패: Ollama가 실행 중인지 확인하세요 (ollama serve)")
        return ""
    except Exception as e:
        _log(f"LLM 호출 실패: {type(e).__name__}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Pass 1: 기획 응답 파싱
# ---------------------------------------------------------------------------

def _parse_planning_output(text: str) -> dict:
    """Pass 1 기획 응답에서 편집 방향 추출

    반환: {"planning_text": str}
    주의: _call_llm이 이미 think 태그를 제거한 텍스트를 반환함.
    """
    clean = text

    # 기획 텍스트에서 === 편집 계획 === 섹션만 추출 (스토리보드 반복 방지)
    plan_sep = re.search(r"===\s*편집\s*계획\s*===", clean)
    if plan_sep:
        planning_text = clean[plan_sep.start():].strip()
        # === 스토리보드 === 이후 제거
        sb_sep = re.search(r"===\s*스토리보드\s*===", planning_text)
        if sb_sep:
            planning_text = planning_text[:sb_sep.start()].strip()
    else:
        # 구분자 없으면 전체 사용하되 스토리보드 부분 제거
        planning_text = clean.strip()
        sb_sep = re.search(r"===\s*스토리보드\s*===", planning_text)
        if sb_sep:
            planning_text = planning_text[:sb_sep.start()].strip()

    _log(f"기획 파싱: 텍스트 {len(planning_text)}자")
    return {"planning_text": planning_text}


def _run_planning_pass(
    prompt: str,
    stage: str = "planning",
    percent: int = 83,
) -> dict:
    """Pass 1 기획 LLM 호출 + 파싱 (재시도 포함)"""
    max_attempts = 3
    for attempt in range(max_attempts):
        _log(f"기획 Pass 호출 (시도 {attempt + 1}/{max_attempts})...")
        raw = _call_llm(prompt)
        if not raw:
            _log(f"기획 시도 {attempt + 1}: 빈 응답")
            continue

        result = _parse_planning_output(raw)
        if result["planning_text"]:
            return result

        _log(f"기획 시도 {attempt + 1}: 편집 계획 추출 실패. 응답: {raw[:200]}")

    # 모든 시도 실패 -> 일시정지
    _pause_for_llm_error(
        f"기획 Pass {max_attempts}회 실패",
        stage=stage,
        percent=percent,
    )
    # 재개 후 한 번 더 시도
    raw = _call_llm(prompt)
    if raw:
        result = _parse_planning_output(raw)
        if result["planning_text"]:
            return result

    # 최종 실패 시 기본값 반환
    _log("기획 Pass 최종 실패 — 기본값 사용")
    return {"planning_text": ""}


# ---------------------------------------------------------------------------
# LLM 호출 + 재시도 (Pass 2 편집용)
# ---------------------------------------------------------------------------

def _run_llm_with_retry(
    prompt: str,
    n_scenes: int,
    attempts: int = 2,
    stage: str = "editing",
    percent: int = 85,
) -> list[dict]:
    """LLM 호출 + 파싱 재시도 -- 실패 시 일시정지 후 무한 재시도

    LLM이 성공할 때까지 반복합니다. 연속 실패 시 경고를 띄우고
    프로세스를 일시정지하여 사용자가 Ollama를 확인할 수 있게 합니다.
    """
    total_attempts = 0
    while True:
        for attempt in range(attempts):
            total_attempts += 1
            _log(f"LLM 호출 (시도 {total_attempts}, 장면 {n_scenes}개)...")
            raw = _call_llm(prompt)
            if not raw:
                _log(f"시도 {total_attempts}: 빈 응답")
                continue
            decisions = _parse_editing_output(raw)
            if decisions is not None:
                _log(f"시도 {total_attempts} 성공: CUT/PARTIAL {len(decisions)}개 (전체 {n_scenes}개 중)")
                return decisions
            _log(f"시도 {total_attempts}: 파싱 실패. 응답 앞 200자: {raw[:200]}")

        # attempts회 연속 실패 -> 일시정지
        _pause_for_llm_error(
            f"LLM {attempts}회 연속 실패 (총 {total_attempts}회 시도)",
            stage=stage,
            percent=percent,
        )
        # 재개 후 워밍업 재확인
        _unload_other_models()
        if not _warmup_model():
            _pause_for_llm_error(
                "LLM 워밍업 실패 -- 모델이 응답하지 않습니다",
                stage=stage,
                percent=percent,
            )


# ---------------------------------------------------------------------------
# 출력 파싱
# ---------------------------------------------------------------------------

def _parse_editing_output(text: str) -> list[dict] | None:
    """CoT 추론부와 JSON 결정부를 분리 파싱

    1. <think>...</think> 태그 제거
    2. === JSON === 또는 ```json 구분자 탐색
    3. JSON 배열 파싱
    4. 추론부는 로그에 저장
    주의: _call_llm이 이미 think 태그를 제거한 텍스트를 반환함.
    """
    clean = text

    # 추론부와 JSON부 분리
    json_text = None
    reasoning = ""

    # === JSON === 구분자 탐색
    sep_match = re.search(r"===\s*JSON\s*===", clean)
    if sep_match:
        reasoning = clean[:sep_match.start()].strip()
        json_text = clean[sep_match.end():].strip()
    else:
        # ```json ... ``` 블록 탐색
        code_match = re.search(r"```json\s*\n?(.*?)```", clean, re.DOTALL)
        if code_match:
            json_text = code_match.group(1).strip()
            reasoning = clean[:code_match.start()].strip()
        else:
            # 구분자 없이 JSON 배열만 있는 경우
            bracket_start = clean.find("[")
            if bracket_start >= 0:
                reasoning = clean[:bracket_start].strip()
                json_text = clean[bracket_start:]

    # 추론부 로그
    if reasoning:
        # 처음 300자만 로그
        _log(f"LLM 추론: {reasoning[:300]}{'...' if len(reasoning) > 300 else ''}")

    if not json_text:
        _log("파싱 실패: JSON 부분을 찾을 수 없음")
        return None

    # JSON 배열 파싱 시도
    # 1차: 전체 JSON 배열
    try:
        arr_start = json_text.find("[")
        arr_end = json_text.rfind("]") + 1
        if arr_start >= 0 and arr_end > arr_start:
            decisions = json.loads(json_text[arr_start:arr_end])
            if isinstance(decisions, list):
                _log(f"JSON 배열 파싱 성공: {len(decisions)}개 판단")
                return decisions
    except json.JSONDecodeError:
        pass

    # 2차: 줄별 JSON 오브젝트 파싱
    decisions = []
    for line in json_text.split("\n"):
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            obj = json.loads(line)
            if "scene" in obj and "decision" in obj:
                decisions.append(obj)
        except json.JSONDecodeError:
            continue

    if decisions:
        _log(f"줄별 파싱 성공: {len(decisions)}개 판단")
        return decisions

    # 3차: 정규식 추출
    pattern = r'\{"scene"\s*:\s*(\d+)\s*,\s*"decision"\s*:\s*"(keep|cut|partial)"'
    matches = re.findall(pattern, json_text)
    if matches:
        decisions = [{"scene": int(m[0]), "decision": m[1], "reason": ""} for m in matches]
        _log(f"정규식 파싱 성공: {len(decisions)}개 판단")
        return decisions

    _log("파싱 실패: 유효한 판단을 찾을 수 없음")
    return None


# ---------------------------------------------------------------------------
# 누락 장면 처리
# ---------------------------------------------------------------------------

def _fill_missing_scenes(
    decisions: list[dict],
    scenes: list[dict],
) -> list[dict]:
    """LLM 출력에서 누락된 장면 처리 — 누락 = 자동 KEEP

    CUT-only 출력 방식: LLM이 언급하지 않은 장면은 모두 KEEP
    """
    mentioned_ids = {d.get("scene", -1) for d in decisions}
    missing_count = 0

    for scene in scenes:
        if scene["id"] not in mentioned_ids:
            missing_count += 1
            decisions.append({
                "scene": scene["id"],
                "decision": "keep",
                "reason": "자동 KEEP",
            })

    if missing_count:
        _log(f"누락 장면 자동 KEEP: {missing_count}개 (전체 {len(scenes)}개 중)")

    return decisions


# ---------------------------------------------------------------------------
# 긴 장면 자동 축소 (코드 레벨 안전장치)
# ---------------------------------------------------------------------------

def _cap_long_scenes(
    decisions: list[dict],
    scenes: list[dict],
    all_windows: list[dict],
) -> list[dict]:
    """긴 KEEP 장면을 자동으로 PARTIAL/CUT로 변환

    액션별 상한 (안전장치, LLM 판단 존중하며 극단적 경우만 제한):
    - driving: 말소리 없으면 CUT, 있으면 최대 30초 (3개 윈도우)
    - walking(단순 이동): 말소리 없으면 CUT, 있으면 최대 30초 (3개 윈도우)
    - walking(트레일/등산): 최대 60초 (6개 윈도우) — 아웃도어 핵심 보호
    - setting_up: 최대 120초 (12개 윈도우) — 캠핑 핵심 콘텐츠 보호
    - scenery/dark: 최대 60초 (6개 윈도우) — 야경/분위기 보호
    - 비말소리: 최대 90초 (9개 윈도우)
    - 말소리 포함: 최대 300초 (30개 윈도우, 말소리 윈도우 우선 균등 샘플링)
    """
    scene_map = {s["id"]: s for s in scenes}
    capped = 0
    cut_count = 0

    def _is_trail_walking(scene: dict) -> bool:
        """장면 설명에서 트레일/등산 여부 판별"""
        desc_text = " ".join(scene.get("descs", [])).lower()
        return any(kw in desc_text for kw in TRAIL_KEYWORDS)

    for d in decisions:
        if d.get("decision") not in ("keep", "partial"):
            continue

        scene = scene_map.get(d.get("scene", -1))
        if not scene:
            continue

        wids = scene["window_ids"]
        dur = scene["duration"]
        action = scene["action"]
        has_speech = scene["has_speech"]

        # PARTIAL의 keep_windows도 상한 적용
        if d["decision"] == "partial":
            wids = d.get("keep_windows", wids)
            dur = len(wids) * 10  # 윈도우당 ~10초 추정

        # 운전: 말소리 없으면 CUT, 있으면 말소리 윈도우 전부 유지 (최대 10)
        if action == "driving":
            if not has_speech:
                d["decision"] = "cut"
                d.pop("keep_windows", None)
                d["reason"] = f"(자동CUT·driving) {d.get('reason', '')}"
                cut_count += 1
                continue
            if dur > 35:
                speech_wids = [
                    wid for wid in wids
                    if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
                ]
                non_speech = [wid for wid in wids if wid not in set(speech_wids)]
                selected = list(speech_wids) + non_speech[:2]
                selected = sorted(selected)[:10]
                d["decision"] = "partial"
                d["keep_windows"] = selected
                d["reason"] = f"(자동축소·driving{dur:.0f}초→{len(selected)*10}초) {d.get('reason', '')}"
                capped += 1

        # 이동: 트레일/등산 vs 단순 이동 구분
        elif action == "walking":
            is_trail = _is_trail_walking(scene)
            if is_trail:
                # 트레일/등산: 최대 60초 (6윈도우) — 보호, 말소리 윈도우 우선
                if dur > 70:
                    if has_speech:
                        speech_wids = [
                            wid for wid in wids
                            if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
                        ]
                        non_speech = [wid for wid in wids if wid not in set(speech_wids)]
                        remaining = max(0, 6 - len(speech_wids))
                        selected = sorted(list(speech_wids) + non_speech[:remaining])
                    else:
                        selected = wids[:6]
                    d["decision"] = "partial"
                    d["keep_windows"] = selected
                    d["reason"] = f"(자동축소·트레일{dur:.0f}초→{len(selected)*10}초) {d.get('reason', '')}"
                    capped += 1
                # 60초 이하면 LLM 판단 그대로 유지
            else:
                # 단순 이동: 말소리 없으면 CUT, 있으면 말소리 윈도우 전부 유지 (최대 30)
                if not has_speech:
                    d["decision"] = "cut"
                    d.pop("keep_windows", None)
                    d["reason"] = f"(자동CUT·walking) {d.get('reason', '')}"
                    cut_count += 1
                    continue
                if dur > 35:
                    speech_wids = [
                        wid for wid in wids
                        if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
                    ]
                    non_speech = [wid for wid in wids if wid not in set(speech_wids)]
                    selected = list(speech_wids) + non_speech[:3]
                    selected = sorted(selected)[:30]
                    d["decision"] = "partial"
                    d["keep_windows"] = selected
                    d["reason"] = f"(자동축소·walking{dur:.0f}초→{len(selected)*10}초) {d.get('reason', '')}"
                    capped += 1

        # 셋업(텐트/타프 설치): 최대 120초 (12개 윈도우) — 핵심 콘텐츠 보호
        elif action == "setting_up" and dur > 130:
            target_w = 12
            if has_speech:
                speech_wids = [
                    wid for wid in wids
                    if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
                ]
                if len(speech_wids) >= target_w:
                    selected = speech_wids[:target_w]
                else:
                    selected = list(speech_wids)
                    non_speech = [wid for wid in wids if wid not in set(selected)]
                    remaining = target_w - len(selected)
                    if non_speech and remaining > 0:
                        step = max(1, len(non_speech) // remaining)
                        selected += non_speech[::step][:remaining]
                    selected.sort()
            else:
                step = max(1, len(wids) // target_w)
                selected = wids[::step][:target_w]
            d["decision"] = "partial"
            d["keep_windows"] = selected
            d["reason"] = f"(자동축소·셋업{dur:.0f}초→120초) {d.get('reason', '')}"
            capped += 1

        # 풍경/어두움/야경: 최대 60초 (6개 윈도우), 말소리 윈도우 우선
        elif action in ("scenery", "dark") and dur > 70:
            if has_speech:
                speech_wids = [
                    wid for wid in wids
                    if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
                ]
                non_speech = [wid for wid in wids if wid not in set(speech_wids)]
                remaining = max(0, 6 - len(speech_wids))
                selected = sorted(list(speech_wids) + non_speech[:remaining])
            else:
                selected = wids[:6]
            d["decision"] = "partial"
            d["keep_windows"] = selected
            d["reason"] = f"(자동축소·풍경{dur:.0f}초→{len(selected)*10}초) {d.get('reason', '')}"
            capped += 1

        # 비말소리: 최대 90초 (9윈도우)
        elif not has_speech and dur > 100:
            d["decision"] = "partial"
            d["keep_windows"] = wids[:9]
            d["reason"] = f"(자동축소·{dur:.0f}초→90초) {d.get('reason', '')}"
            capped += 1

        # 말소리 포함: 최대 300초 (30개 윈도우, 말소리 우선 균등 샘플링)
        elif dur > 310:
            target = 30
            speech_wids = [
                wid for wid in wids
                if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
            ]
            if len(speech_wids) > target:
                step = max(1, len(speech_wids) // target)
                selected = speech_wids[::step][:target]
            elif speech_wids:
                selected = list(speech_wids)
                remaining = target - len(selected)
                non_speech = [wid for wid in wids if wid not in set(selected)]
                if non_speech and remaining > 0:
                    selected.extend(non_speech[:remaining])
            else:
                step = max(1, len(wids) // target)
                selected = wids[::step][:target]

            selected.sort()
            if len(selected) < len(wids):
                d["decision"] = "partial"
                d["keep_windows"] = selected
                d["reason"] = f"(자동축소·{dur:.0f}초→{len(selected)*10}초) {d.get('reason', '')}"
                capped += 1

    if capped or cut_count:
        _log(f"자동 축소: {capped}개 PARTIAL, {cut_count}개 CUT")

    return decisions


# ---------------------------------------------------------------------------
# 말소리 보호 안전망
# ---------------------------------------------------------------------------

def _protect_speech_in_partial(
    decisions: list[dict],
    scenes: list[dict],
    all_windows: list[dict],
) -> list[dict]:
    """PARTIAL 결정에서 누락된 말소리 윈도우를 복원하는 최종 안전망

    LLM이나 _cap_long_scenes가 PARTIAL을 생성할 때 말소리 윈도우를 빠뜨린 경우,
    해당 윈도우를 keep_windows에 추가하여 말소리가 잘리지 않도록 보호한다.
    """
    scene_map = {s["id"]: s for s in scenes}
    restored = 0

    for d in decisions:
        if d.get("decision") != "partial":
            continue
        scene = scene_map.get(d.get("scene", -1))
        if not scene or not scene.get("has_speech"):
            continue

        keep_set = set(d.get("keep_windows", []))
        speech_wids = [
            wid for wid in scene["window_ids"]
            if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
        ]
        missing = [wid for wid in speech_wids if wid not in keep_set]
        if missing:
            new_keeps = sorted(keep_set | set(speech_wids))
            # 30윈도우 상한 유지 (speech 우선)
            if len(new_keeps) > 30:
                new_keeps = sorted(speech_wids)[:30]
            d["keep_windows"] = new_keeps
            restored += 1

    if restored:
        _log(f"말소리 보호: {restored}개 PARTIAL에 누락된 ★ 윈도우 복원")

    return decisions


# ---------------------------------------------------------------------------
# 비말소리 시간 계산 (목표 시간 비교용)
# ---------------------------------------------------------------------------

def _calc_nonspeech_keep_min(
    decisions: list[dict],
    scenes: list[dict],
    all_windows: list[dict],
) -> float:
    """현재 KEEP 결과 중 말소리 없는 윈도우의 합 (분 단위)

    target_minutes 비교 기준 — '말하는 장면 제외한 나머지 합'.
    """
    scene_map = {s["id"]: s for s in scenes}
    nonspeech_sec = 0.0

    for d in decisions:
        decision = d.get("decision", "keep")
        if decision == "cut":
            continue
        scene = scene_map.get(d.get("scene", -1))
        if not scene:
            continue

        if decision == "partial":
            wids = d.get("keep_windows", [])
        else:  # keep
            wids = scene.get("window_ids", [])

        for wid in wids:
            if 0 <= wid < len(all_windows):
                w = all_windows[wid]
                if not window_has_speech(w):
                    nonspeech_sec += w.get("end", 0) - w.get("start", 0)

    return nonspeech_sec / 60.0


# ---------------------------------------------------------------------------
# 모든 영상 파일에서 최소 1씬 보장
# ---------------------------------------------------------------------------

def _ensure_all_files_present(
    decisions: list[dict],
    scenes: list[dict],
    all_windows: list[dict],
) -> list[dict]:
    """가치 있는 영상에 한해 최소 1개 씬을 보존 (활용도 낮으면 LLM 결정 존중).

    사용자 실제 편집 패턴에서는 영상의 30%가 통째로 미사용된다. LLM이 영상을
    통째로 cut했다면 그건 보통 합리적 판단이므로 강제 복원하지 않는다.
    단, 영상에 점수 높은 씬(say >= 35)이 있으면 LLM 누락일 가능성이 있어 복원.
    """
    SCORE_THRESHOLD = 60  # 이 점수 이상인 씬을 가진 영상만 복원 (사용자 패턴: 30% 통째 미사용)

    decision_by_scene = {d["scene"]: d for d in decisions}

    file_keep_count: dict[int, int] = {}
    file_scenes: dict[int, list[dict]] = {}

    for scene in scenes:
        fi = scene.get("file_index", -1)
        if fi < 0:
            continue
        file_scenes.setdefault(fi, []).append(scene)

        d = decision_by_scene.get(scene["id"])
        decision = d.get("decision", "keep") if d else "keep"
        if decision != "cut":
            file_keep_count[fi] = file_keep_count.get(fi, 0) + 1

    restored = 0
    skipped_low_value = 0
    for fi, fscenes in file_scenes.items():
        if file_keep_count.get(fi, 0) > 0:
            continue

        max_score = max((s.get("score", 0) for s in fscenes), default=0)

        # 점수 낮은 영상은 LLM 판단 존중 (통째 cut OK)
        if max_score < SCORE_THRESHOLD:
            skipped_low_value += 1
            continue

        # 후보 선택: 점수 높은 씬
        candidate = max(
            fscenes,
            key=lambda s: (s.get("score", 0), s.get("avg_motion", 0.0)),
        )

        wids = candidate.get("window_ids", [])
        if not wids:
            continue

        # 모션이 가장 높은 윈도우 1개 선택
        best_wid = max(
            wids,
            key=lambda w: all_windows[w].get("motion", 0.0) if 0 <= w < len(all_windows) else 0.0,
        )

        new_decision = {
            "scene": candidate["id"],
            "decision": "partial",
            "keep_windows": [best_wid],
            "reason": f"(자동복원) 점수 {max_score:.0f}로 가치 있는 영상에 1씬 보존",
        }

        existing = decision_by_scene.get(candidate["id"])
        if existing:
            existing["decision"] = "partial"
            existing["keep_windows"] = [best_wid]
            existing["reason"] = new_decision["reason"]
        else:
            decisions.append(new_decision)
        restored += 1

    if restored:
        _log(f"파일 보존: {restored}개 영상에서 가치 있는 1씬 복원 (점수 {SCORE_THRESHOLD}+)")
    if skipped_low_value:
        _log(f"파일 통째 cut 허용: {skipped_low_value}개 영상 (점수 {SCORE_THRESHOLD} 미만)")

    return decisions


# ---------------------------------------------------------------------------
# 강제 trim — LLM이 목표 시간을 못 맞췄을 때 점수 낮은 씬부터 자동 cut/partial
# ---------------------------------------------------------------------------

def _force_trim_to_target(
    decisions: list[dict],
    scenes: list[dict],
    all_windows: list[dict],
    target_minutes: int,
    tolerance: float = 1.05,
) -> list[dict]:
    """목표 시간 초과 시 강제로 trim해서 target_minutes ± tolerance에 맞춤.

    전략:
      1. 현재 keep/partial 씬을 점수 오름차순으로 정렬
      2. 점수 낮은 것부터:
         - 비말소리 씬: cut
         - 말소리(★) 씬: keep_windows를 더 줄임 (말소리 윈도우만 우선 keep)
         - partial이면 keep_windows를 더 줄임
      3. 보호: 첫 씬, 마지막 씬, 라벨별 unique 1개는 cut 금지

    LLM이 충분히 못 줄였을 때 마지막 안전망. 흐름 보호는 유지하되 시간 우선.
    """
    target_sec = target_minutes * 60
    max_sec = target_sec * tolerance

    scene_map = {s["id"]: s for s in scenes}
    decision_by_scene = {d["scene"]: d for d in decisions}

    def calc_total() -> float:
        total = 0.0
        for d in decisions:
            decision = d.get("decision", "keep")
            if decision == "cut":
                continue
            scene = scene_map.get(d.get("scene", -1))
            if not scene:
                continue
            if decision == "partial":
                wids = d.get("keep_windows", [])
                for wid in wids:
                    if 0 <= wid < len(all_windows):
                        w = all_windows[wid]
                        total += w.get("end", 0) - w.get("start", 0)
            else:  # keep
                total += scene["end"] - scene["start"]
        return total

    # 보호 대상 씬 ID
    protected_ids: set[int] = set()
    if scenes:
        protected_ids.add(scenes[0]["id"])
        protected_ids.add(scenes[-1]["id"])
    # 라벨별 점수 가장 높은 1개는 보호 (셋업/요리/불멍/풍경 등 핵심 활동)
    label_top: dict[str, dict] = {}
    for s in scenes:
        action = s.get("action", "")
        if action not in label_top or s.get("score", 0) > label_top[action].get("score", 0):
            label_top[action] = s
    for s in label_top.values():
        protected_ids.add(s["id"])

    initial = calc_total()
    if initial <= max_sec:
        return decisions

    _log(f"강제 trim 시작: 현재 {initial/60:.1f}분 > 목표 {target_minutes}분 ({max_sec/60:.1f}분 한도)")

    # 점수 오름차순 + 비말소리 우선
    sortable = []
    for d in decisions:
        if d.get("decision") == "cut":
            continue
        scene = scene_map.get(d.get("scene", -1))
        if not scene:
            continue
        score = scene.get("score", 0)
        has_speech = scene.get("has_speech", False)
        # 비말소리(0) 먼저 처리, 말소리는 score 낮은 것부터
        sortable.append((0 if not has_speech else 1, score, d, scene))

    sortable.sort(key=lambda x: (x[0], x[1]))

    trimmed_count = 0
    for _, _, d, scene in sortable:
        if calc_total() <= max_sec:
            break

        scene_id = d["scene"]
        has_speech = scene.get("has_speech", False)
        decision = d.get("decision", "keep")

        if scene_id in protected_ids:
            # 보호 씬은 cut 금지. 단 partial로 줄이는 건 가능
            if decision == "keep":
                wids = scene.get("window_ids", [])
                if len(wids) > 1:
                    # 1윈도우만 (말소리면 말소리 윈도우 우선)
                    if has_speech:
                        speech_wids = [
                            w for w in wids
                            if 0 <= w < len(all_windows) and window_has_speech(all_windows[w])
                        ]
                        keep_w = [speech_wids[0]] if speech_wids else [wids[0]]
                    else:
                        keep_w = [max(wids, key=lambda w: all_windows[w].get("motion", 0.0)
                                      if 0 <= w < len(all_windows) else 0.0)]
                    d["decision"] = "partial"
                    d["keep_windows"] = keep_w
                    d["reason"] = f"(강제trim·보호씬축소) {d.get('reason', '')}"
                    trimmed_count += 1
            elif decision == "partial":
                kw = d.get("keep_windows", [])
                if len(kw) > 1:
                    d["keep_windows"] = kw[:1]
                    d["reason"] = f"(강제trim·partial축소) {d.get('reason', '')}"
                    trimmed_count += 1
            continue

        if not has_speech:
            # 비말소리: cut
            d["decision"] = "cut"
            d.pop("keep_windows", None)
            d["reason"] = f"(강제trim·cut) {d.get('reason', '')}"
            trimmed_count += 1
        else:
            # 말소리: keep_windows를 말소리 윈도우만 1~2개로
            wids = (
                d.get("keep_windows", [])
                if decision == "partial"
                else scene.get("window_ids", [])
            )
            speech_wids = [
                w for w in wids
                if 0 <= w < len(all_windows) and window_has_speech(all_windows[w])
            ]
            if speech_wids:
                d["decision"] = "partial"
                d["keep_windows"] = speech_wids[:2]
                d["reason"] = f"(강제trim·말소리축소) {d.get('reason', '')}"
                trimmed_count += 1
            else:
                # 말소리 윈도우 없으면 cut
                d["decision"] = "cut"
                d.pop("keep_windows", None)
                d["reason"] = f"(강제trim·cut) {d.get('reason', '')}"
                trimmed_count += 1

    final = calc_total()
    _log(f"강제 trim 완료: {trimmed_count}개 결정 변경, {initial/60:.1f}분 → {final/60:.1f}분")

    return decisions


# ---------------------------------------------------------------------------
# 활동 클러스터 균등 분배
# 같은 라벨(셋업/요리/불멍 등)이 시간순 인접한 씬 그룹에서
# 한쪽만 길게 keep / 나머지 통째 cut된 패턴을 발견하면 균등 분배로 보정
# ---------------------------------------------------------------------------

# 분배 대상 라벨 (활동이 여러 영상에 걸쳐 진행될 수 있는 것)
_DISTRIBUTABLE_LABELS = {
    "setting_up",      # 텐트/타프 설치
    "cooking",         # 요리
    "fire_tending",    # 불멍/모닥불
    "showing_gear",    # 장비 소개
    "walking",         # 이동/하이킹
    "scenery",         # 풍경
    "eating",          # 식사
}


def _distribute_activity_clusters(
    decisions: list[dict],
    scenes: list[dict],
    all_windows: list[dict],
) -> list[dict]:
    """같은 라벨이 시간순 인접한 씬 그룹에서 흐름 유지 위해 균등 partial 적용.

    예: setting_up 씬 5개 (S5~S9 인접) →
        LLM이 S6만 길게 keep, 나머지 cut 했다면 → 흐름 끊김.
        이런 패턴 발견 시 모든 씬을 각 1윈도우 partial로 균등 분배.

    조건:
      - 시간순 인접한 같은 라벨 씬이 3개 이상
      - LLM 결정에 keep/partial과 cut이 섞여 있음 (한쪽만 처리한 패턴)
    """
    if not scenes:
        return decisions

    # 시간순 정렬 (이미 정렬되어 있겠지만 보장)
    sorted_scenes = sorted(scenes, key=lambda s: s.get("start", 0))

    # 인접 같은 라벨 클러스터 추출
    clusters: list[list[dict]] = []
    current: list[dict] = []
    current_label: str | None = None

    for s in sorted_scenes:
        action = s.get("action", "")
        if action in _DISTRIBUTABLE_LABELS and action == current_label:
            current.append(s)
        else:
            if len(current) >= 3:
                clusters.append(current)
            current = [s] if action in _DISTRIBUTABLE_LABELS else []
            current_label = action
    if len(current) >= 3:
        clusters.append(current)

    if not clusters:
        return decisions

    decision_by_scene = {d["scene"]: d for d in decisions}
    distributed_groups = 0

    for cluster in clusters:
        # 클러스터의 결정 파악
        keep_or_partial = []
        cut_scenes = []
        for s in cluster:
            d = decision_by_scene.get(s["id"], {"decision": "keep"})
            if d.get("decision") == "cut":
                cut_scenes.append(s)
            else:
                keep_or_partial.append(s)

        # 혼재 패턴(일부만 keep, 일부만 cut)일 때만 분배
        if not keep_or_partial or not cut_scenes:
            continue

        label = cluster[0].get("action", "")
        # cut됐던 씬을 1윈도우 partial로 복원 (모션 높은 윈도우)
        # keep_or_partial은 partial로 축소 (이미 partial이면 그대로 두되 너무 길면 1윈도우)
        for s in cluster:
            d = decision_by_scene.get(s["id"])
            wids = s.get("window_ids", [])
            if not wids:
                continue

            best_wid = max(
                wids,
                key=lambda w: all_windows[w].get("motion", 0.0) if 0 <= w < len(all_windows) else 0.0,
            )

            if d is None:
                d = {"scene": s["id"]}
                decisions.append(d)
                decision_by_scene[s["id"]] = d

            if d.get("decision") == "cut":
                # cut → 1윈도우 partial 복원
                d["decision"] = "partial"
                d["keep_windows"] = [best_wid]
                d["reason"] = f"(클러스터분배·{label}) 흐름 유지 위해 1윈도우 복원"
            elif d.get("decision") == "keep" and len(wids) > 1:
                # keep → 1윈도우 partial로 축소
                d["decision"] = "partial"
                d["keep_windows"] = [best_wid]
                d["reason"] = f"(클러스터분배·{label}) 균등 분배 위해 1윈도우 축소"
            elif d.get("decision") == "partial":
                kw = d.get("keep_windows", [])
                if len(kw) > 1:
                    d["keep_windows"] = kw[:1]
                    d["reason"] = f"(클러스터분배·{label}) 균등 분배 위해 1윈도우 축소"

        distributed_groups += 1

    if distributed_groups:
        _log(f"활동 클러스터 분배: {distributed_groups}개 그룹을 균등 1윈도우 partial로 보정")

    return decisions


# ---------------------------------------------------------------------------
# PARTIAL / KEEP / CUT 적용
# ---------------------------------------------------------------------------

def _apply_decisions(
    decisions: list[dict],
    scenes: list[dict],
    all_windows: list[dict],
) -> list[dict]:
    """편집 결정을 적용하여 KEEP 세그먼트 생성

    - keep: 장면 전체 KEEP (해당 장면의 모든 윈도우)
    - partial: keep_windows에 지정된 윈도우만 KEEP
    - cut: 무시 (세그먼트에 추가하지 않음)
    """
    # scene id -> scene 매핑
    scene_map = {s["id"]: s for s in scenes}

    keep_segments = []
    seg_id = 0

    for d in sorted(decisions, key=lambda x: x.get("scene", 0)):
        scene_id = d.get("scene", 0)
        decision = d.get("decision", "cut")
        scene = scene_map.get(scene_id)

        if scene is None:
            _log(f"경고: 장면 S{scene_id:02d} 매핑 실패")
            continue

        if decision == "cut":
            continue

        window_ids = scene["window_ids"]
        hint = d.get("hint", "")
        reason = d.get("reason", "")

        if decision == "keep":
            # 장면 전체 KEEP
            for wid in window_ids:
                if 0 <= wid < len(all_windows):
                    w = all_windows[wid]
                    keep_segments.append(_make_segment(
                        seg_id, w, wid, hint, reason, scene,
                    ))
                    seg_id += 1

        elif decision == "partial":
            # 지정된 윈도우만 KEEP
            keep_window_ids = set(d.get("keep_windows", []))
            if not keep_window_ids:
                _log(f"경고: S{scene_id:02d} PARTIAL이지만 keep_windows 비어있음")
                continue

            # 장면에 속하지 않는 윈도우 ID 경고 + 보정
            valid_wids = keep_window_ids & set(window_ids)
            invalid_wids = keep_window_ids - set(window_ids)
            if invalid_wids:
                _log(f"경고: S{scene_id:02d} PARTIAL keep_windows에 장면 외 ID {sorted(invalid_wids)} → 무시")
            if not valid_wids:
                _log(f"경고: S{scene_id:02d} PARTIAL 유효한 윈도우 없음 → 장면 전체 KEEP으로 대체")
                valid_wids = set(window_ids)

            for wid in window_ids:
                if wid in valid_wids and 0 <= wid < len(all_windows):
                    w = all_windows[wid]
                    keep_segments.append(_make_segment(
                        seg_id, w, wid, hint, reason, scene,
                    ))
                    seg_id += 1

    return keep_segments


def _clean_reason(reason: str) -> str:
    """디버깅용 접두사를 제거하여 SRT에 적합한 reason 반환

    (자동CUT·driving), (자동축소·셋업120초→60초) 등의 시스템 태그를 제거하고
    LLM이 생성한 원래 편집 사유만 남긴다.
    """
    cleaned = re.sub(r"\(자동[A-Za-z0-9가-힣·→%]+\)\s*", "", reason)
    return cleaned.strip()


def _make_segment(
    seg_id: int,
    w: dict,
    wid: int,
    hint: str,
    reason: str,
    scene: dict,
) -> dict:
    """개별 KEEP 세그먼트 데이터 생성"""
    return {
        "id": seg_id,
        "globalStart": w["globalStart"],
        "globalEnd": w["globalEnd"],
        "label": w["label"],
        "score": w.get("s1_score", 0),
        "source": w.get("source", ""),
        "has_speech": window_has_speech(w),
        "desc": w.get("desc", ""),
        "reason": _clean_reason(reason),
        "hint": hint,
        "scene_id": scene["id"],
        "scene_action": scene["action"],
        "window_id": wid,
    }


# ---------------------------------------------------------------------------
# 재편집 헬퍼
# ---------------------------------------------------------------------------

def _build_keep_summary(
    decisions: list[dict],
    scenes: list[dict],
    target_minutes: int = 0,
) -> str:
    """현재 KEEP/PARTIAL 장면 요약 텍스트 생성 (재편집 프롬프트용)

    장면별 길이와 통계를 포함하여 LLM이 구체적으로 판단할 수 있게 함.
    """
    scene_map = {s["id"]: s for s in scenes}
    entries = []  # (scene_id, decision, dur_min, has_speech, line)

    for d in sorted(decisions, key=lambda x: x.get("scene", 0)):
        decision = d.get("decision", "cut")
        if decision == "cut":
            continue
        scene = scene_map.get(d.get("scene", -1))
        if not scene:
            continue

        sid = scene["id"]
        dur = scene["duration"]
        action = scene["action"]
        has_speech = scene["has_speech"]

        speech_part = ""
        if has_speech:
            pct = int(scene["speech_ratio"] * 100)
            speech_part = f" ★말소리{pct}%"

        descs = scene.get("descs", [])
        desc_text = "→".join(descs[:2]) if descs else ""

        if decision == "keep":
            dur_min = dur / 60
            dur_str = f"{dur_min:.1f}분"
            line = f"[S{sid:02d}] KEEP ({dur_str}) {action}{speech_part} | {desc_text}"
        else:  # partial
            n_w = len(d.get("keep_windows", []))
            dur_min = n_w * 10 / 60
            dur_str = f"{dur_min:.1f}분"
            line = f"[S{sid:02d}] PARTIAL ({dur_str}, {n_w}윈도우) {action}{speech_part} | {desc_text}"

        entries.append((sid, decision, dur_min, has_speech, line))

    total_keep_min = sum(e[2] for e in entries)
    keep_count = len(entries)
    speech_count = sum(1 for e in entries if e[3])
    no_speech_count = keep_count - speech_count

    # 통계 헤더
    header_lines = [
        f"총 {keep_count}개 장면, 합계 {total_keep_min:.1f}분 "
        f"(말소리 {speech_count}개, 비말소리 {no_speech_count}개)",
    ]
    if target_minutes > 0:
        need_cut = total_keep_min - target_minutes
        if need_cut > 0:
            header_lines.append(
                f"→ 목표 {target_minutes}분까지 최소 {need_cut:.0f}분({int(need_cut / total_keep_min * 100)}%)을 "
                f"추가로 CUT해야 합니다."
            )

    header = "\n".join(header_lines)
    scene_lines = "\n".join(e[4] for e in entries)
    return f"{header}\n\n{scene_lines}"


def _merge_reedit_decisions(
    original: list[dict],
    new_cuts: list[dict],
) -> list[dict]:
    """재편집 결과를 원본 decisions에 병합 (새 CUT/PARTIAL이 기존 KEEP을 덮어씀)"""
    new_map = {d["scene"]: d for d in new_cuts}
    merged = []
    for d in original:
        sid = d.get("scene", -1)
        if sid in new_map:
            merged.append(new_map[sid])
        else:
            merged.append(d)
    return merged


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------

def run_narrative_editing(
    scenes: list[dict],
    all_windows: list[dict],
    total_duration: float,
    progress_callback=None,
    editing_comment: str = "",
    target_minutes: int = 0,
) -> list[dict]:
    """장면 기반 내러티브 편집 -- 2-Pass LLM (CUT-only 출력)

    Pass 1: 전체 스토리보드 검토 → 콘텐츠 분석 + 편집 방향 수립
    Pass 2: 버릴 장면(CUT/PARTIAL)만 출력, 언급하지 않은 장면은 자동 KEEP
    """
    if not scenes:
        return []

    _log(f"내러티브 편집 시작: {len(scenes)}개 장면, {len(all_windows)}개 윈도우")
    if editing_comment:
        editing_comment = editing_comment[:500]
        _log(f"편집 코멘트: {editing_comment}")

    # 모델 사전 확인
    while not _check_model_available(EDITING_MODEL):
        _pause_for_llm_error(
            f"모델 '{EDITING_MODEL}'을 찾을 수 없습니다. ollama pull {EDITING_MODEL} 실행 필요",
            stage="editing",
            percent=81,
        )

    # 다른 모델 해제
    if progress_callback:
        progress_callback("editing", 81, "다른 모델 해제 중...")
    _unload_other_models()

    # 모델 워밍업
    if progress_callback:
        progress_callback("editing", 82, "LLM 모델 로딩 중...")
    while not _warmup_model():
        _pause_for_llm_error(
            "LLM 워밍업 실패 -- 모델이 응답하지 않습니다",
            stage="editing",
            percent=82,
        )
        _unload_other_models()

    # 스토리보드 생성
    if progress_callback:
        progress_callback("editing", 83, "스토리보드 생성 중...")
    storyboard = generate_narrative_storyboard(scenes, total_duration)
    total_min = int(total_duration / 60)
    _log(f"스토리보드: {len(scenes)}개 장면, {len(storyboard)}자, 원본 {total_min}분")

    # -----------------------------------------------------------------------
    # Pass 1: 기획 — 콘텐츠 분석 + 편집 방향 수립
    # -----------------------------------------------------------------------
    if progress_callback:
        progress_callback("editing", 84, "영상 전체 검토 중 (Pass 1: 기획)...")

    # 사용자 코멘트 섹션
    comment_section = ""
    if editing_comment:
        comment_section = f"\n\n## 편집자 요청 (반드시 반영하세요)\n\n{editing_comment}\n"

    planning_prompt = (
        PLANNING_PROMPT_TEMPLATE
        .replace("__STORYBOARD__", storyboard)
        .replace("__TOTAL_DURATION__", f"{total_min}분")
        .replace("=== 스토리보드 ===", f"{comment_section}=== 스토리보드 ===")
    )

    planning_result = _run_planning_pass(
        planning_prompt, stage="editing", percent=84,
    )

    planning_text = planning_result["planning_text"]
    _log(f"기획 완료 — 편집 방향 {len(planning_text)}자")

    # -----------------------------------------------------------------------
    # Pass 2: 편집 — CUT/PARTIAL만 출력, 나머지 자동 KEEP
    # -----------------------------------------------------------------------
    if progress_callback:
        progress_callback("editing", 87, "LLM 편집 판단 중...")

    if target_minutes > 0:
        keep_ratio = target_minutes / max(total_min, 1)
        duration_line = f"**완성본은 반드시 약 {target_minutes}분을 목표로 하세요.** (원본 {total_min}분, 약 {int(keep_ratio * 100)}%만 유지)"
        _log(f"목표 시간 설정: {target_minutes}분 (유지율 {keep_ratio:.0%})")
        if keep_ratio < 0.5:
            keep_bias = f"목표가 원본의 {int(keep_ratio * 100)}%이므로 과감하게 CUT하세요. 말소리 없는 장면은 적극적으로 CUT하고, 긴 장면은 PARTIAL로 핵심만 남기세요."
        else:
            keep_bias = "정말 불필요한 장면만 선별적으로 CUT하세요. 대부분의 장면은 남겨야 합니다."
    else:
        keep_min_low = int(total_min * 0.5)
        keep_min_high = int(total_min * 0.7)
        duration_line = f"**완성본은 원본의 50~70% 분량(약 {keep_min_low}~{keep_min_high}분)을 목표로 하세요.**"
        keep_bias = "정말 불필요한 장면만 선별적으로 CUT하세요. 대부분의 장면은 남겨야 합니다."

    editing_prompt = (
        EDITING_PROMPT_TEMPLATE
        .replace("__STORYBOARD__", storyboard)
        .replace("__PLANNING_RESULT__", planning_text)
        .replace("__TOTAL_DURATION__", f"{total_min}분")
        .replace("__DURATION_GUIDE__", duration_line)
        .replace("__KEEP_BIAS__", keep_bias)
        .replace("=== 스토리보드 ===", f"{comment_section}=== 스토리보드 ===")
    )

    decisions = _run_llm_with_retry(
        editing_prompt, len(scenes), stage="editing", percent=87,
    )

    # 누락 장면 처리 (누락=자동KEEP)
    decisions = _fill_missing_scenes(decisions, scenes)

    # 긴 장면 자동 축소 (코드 레벨 안전장치)
    decisions = _cap_long_scenes(decisions, scenes, all_windows)

    # 말소리 보호 안전망 (PARTIAL에서 누락된 ★ 윈도우 복원)
    decisions = _protect_speech_in_partial(decisions, scenes, all_windows)

    # 모든 영상 파일에 최소 1씬 보장 (흐름 끊김 방지)
    decisions = _ensure_all_files_present(decisions, scenes, all_windows)
    decisions = _distribute_activity_clusters(decisions, scenes, all_windows)

    # 결정 통계 로그
    keep_count = sum(1 for d in decisions if d.get("decision") == "keep")
    partial_count = sum(1 for d in decisions if d.get("decision") == "partial")
    cut_count = sum(1 for d in decisions if d.get("decision") == "cut")
    _log(f"편집 결정: KEEP {keep_count}, PARTIAL {partial_count}, CUT {cut_count}")

    # 결정 적용 -> 세그먼트 생성
    if progress_callback:
        progress_callback("editing", 92, "편집 결과 적용 중...")
    keep_segments = _apply_decisions(decisions, scenes, all_windows)

    total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
    nonspeech_min = _calc_nonspeech_keep_min(decisions, scenes, all_windows)
    _log(f"편집 완료: {len(keep_segments)}개 세그먼트, 전체 {total_keep / 60:.1f}분 (비말소리 {nonspeech_min:.1f}분)")

    # -------------------------------------------------------------------
    # 목표 시간 초과 시 재편집 (최대 3회)
    # 비교 기준: 비말소리 keep 시간
    # -------------------------------------------------------------------
    MAX_REEDIT = 5
    TOLERANCE = 1.07  # 7% 여유 (30분 목표 → 32분까지 허용)

    for reedit_round in range(MAX_REEDIT):
        current_min = total_keep / 60  # 전체 keep 시간 비교
        if target_minutes <= 0 or current_min <= target_minutes * TOLERANCE:
            break

        over_min = int(current_min - target_minutes)
        _log(f"목표 초과: {current_min:.1f}분 > {target_minutes}분 (재편집 {reedit_round + 1}/{MAX_REEDIT})")

        if progress_callback:
            progress_callback(
                "editing", 93 + reedit_round,
                f"목표 초과 ({current_min:.0f}분>{target_minutes}분), 재편집 {reedit_round + 1}차...",
            )

        keep_summary = _build_keep_summary(decisions, scenes, target_minutes)
        reedit_prompt = REEDIT_PROMPT_OLLAMA.format(
            current_min=int(current_min),
            target_minutes=target_minutes,
            total_min=total_min,
            over_min=over_min,
            keep_summary=keep_summary,
        )

        new_cuts = _run_llm_with_retry(
            reedit_prompt, len(scenes), stage="editing", percent=93 + reedit_round,
        )
        if not new_cuts:
            _log("재편집: 추가 CUT 없음, 중단")
            break

        _log(f"재편집 {reedit_round + 1}차: {len(new_cuts)}개 추가 CUT/PARTIAL")

        prev_total_min = total_keep / 60
        decisions = _merge_reedit_decisions(decisions, new_cuts)
        decisions = _fill_missing_scenes(decisions, scenes)
        decisions = _cap_long_scenes(decisions, scenes, all_windows)
        decisions = _protect_speech_in_partial(decisions, scenes, all_windows)
        decisions = _ensure_all_files_present(decisions, scenes, all_windows)
        decisions = _distribute_activity_clusters(decisions, scenes, all_windows)

        keep_count = sum(1 for d in decisions if d.get("decision") == "keep")
        partial_count = sum(1 for d in decisions if d.get("decision") == "partial")
        cut_count = sum(1 for d in decisions if d.get("decision") == "cut")
        _log(f"재편집 후: KEEP {keep_count}, PARTIAL {partial_count}, CUT {cut_count}")

        keep_segments = _apply_decisions(decisions, scenes, all_windows)
        total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
        nonspeech_min = _calc_nonspeech_keep_min(decisions, scenes, all_windows)
        cur_total_min = total_keep / 60
        _log(f"재편집 후: {len(keep_segments)}개 세그먼트, 전체 {cur_total_min:.1f}분 (비말소리 {nonspeech_min:.1f}분)")

        # 수렴 정체 감지: 1분 이상 줄지 않으면 중단
        if prev_total_min - cur_total_min < 1.0:
            _log(f"재편집 정체 감지 ({prev_total_min:.1f}→{cur_total_min:.1f}분), 중단")
            break

    # 마지막 안전망: LLM 재편집 후에도 목표 초과면 강제 trim
    if target_minutes > 0:
        current_min = total_keep / 60
        if current_min > target_minutes * 1.07:
            decisions = _force_trim_to_target(
                decisions, scenes, all_windows, target_minutes, tolerance=1.05
            )
            keep_segments = _apply_decisions(decisions, scenes, all_windows)
            total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
            _log(f"최종(강제trim 후): {len(keep_segments)}개 세그먼트, 전체 {total_keep / 60:.1f}분")

    return keep_segments


# ===========================================================================
# Scored 편집 — 순수 알고리즘 (LLM 없음)
# ===========================================================================

def run_scored_editing(
    scenes: list[dict],
    all_windows: list[dict],
    total_duration: float,
    progress_callback=None,
    editing_comment: str = "",
    target_minutes: int = 0,
) -> list[dict]:
    """점수 기반 편집 — scene_scorer + budget_selector (LLM 미사용)

    run_narrative_editing()과 동일한 keep_segments 형식을 반환한다.
    향후 LLM을 "리뷰어"로 활용할 수 있는 구조를 남겨둔다.
    """
    from scene_scorer import score_all_scenes
    from budget_selector import select_scenes

    if not scenes:
        return []

    _log(f"Scored 편집 시작: {len(scenes)}개 장면, {len(all_windows)}개 윈도우")
    if editing_comment:
        _log(f"편집 코멘트: {editing_comment} (scored 모드에서는 참고용)")

    # --- 1단계: 장면 점수 산출 (83~87%) ---
    if progress_callback:
        progress_callback("scoring", 83, "장면 점수 산출 중...")

    score_results = score_all_scenes(scenes, all_windows, total_duration)

    if progress_callback:
        scores = [r["score"] for r in score_results]
        avg = sum(scores) / len(scores) if scores else 0
        progress_callback(
            "scoring", 87,
            f"점수 산출 완료: {len(score_results)}개 장면, 평균 {avg:.1f}점",
        )

    # --- 2단계: 예산 기반 장면 선택 (87~90%) ---
    if progress_callback:
        progress_callback("selection", 87, "장면 선택 중...")

    keep_segments = select_scenes(
        scenes, all_windows, target_minutes, total_duration,
    )

    total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
    _log(f"Scored 편집 완료: {len(keep_segments)}개 세그먼트, {total_keep / 60:.1f}분")

    if progress_callback:
        progress_callback(
            "selection", 90,
            f"장면 선택 완료: {len(keep_segments)}개 세그먼트, {total_keep / 60:.1f}분",
        )

    return keep_segments


# ===========================================================================
# 하이브리드 편집 — Scoring + Budget Selection + Claude LLM 보정 + Hard Trim
# ===========================================================================

def run_hybrid_editing(
    scenes: list[dict],
    all_windows: list[dict],
    total_duration: float,
    progress_callback=None,
    editing_comment: str = "",
    target_minutes: int = 0,
) -> list[dict]:
    """1차 편집: Claude에 전체 장면을 보여주고 쓸모없는 장면만 제거

    scoring/budget/hard_trim 없이, Claude가 맥락을 보고 직접 판단.
    """
    if not scenes:
        return []

    _log(f"1차 편집 시작: {len(scenes)}개 장면, {len(all_windows)}개 윈도우")
    if editing_comment:
        _log(f"편집 코멘트: {editing_comment}")

    if progress_callback:
        progress_callback("editing", 83, "Claude 편집 중...")

    # 전체 장면을 Claude에 전달 — target_minutes가 있으면 가이드 + 재편집 루프 동작
    keep_segments = run_narrative_editing_claude(
        scenes, all_windows, total_duration,
        progress_callback=progress_callback,
        editing_comment=editing_comment,
        target_minutes=target_minutes,
    )

    total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
    _log(f"1차 편집 완료: {len(keep_segments)}개 세그먼트, {total_keep / 60:.1f}분")

    if progress_callback:
        progress_callback("editing", 95, f"편집 완료: {total_keep / 60:.1f}분")

    return keep_segments


# ===========================================================================
# Claude 편집 — 1-Pass (기획+편집 통합)
# ===========================================================================

CLAUDE_EDITING_PROMPT = """당신은 캠핑 브이로그 편집자입니다.
시청자가 끝까지 보는 **지루하지 않은 영상**으로 편집하세요. 컷이 길어지면 시청자가 빠져나갑니다.

## 레퍼런스 스타일

__EDITING_STYLE__

## 스토리보드 포맷

각 줄: [S번호] 시간 (길이) 행동 ★말소리% M:모션 W:윈도우범위 | 설명 💬"대사"
- ★: 말소리 비율 (없으면 말소리 없음)
- M: 모션 레벨 (저/중/고)
- W: 해당 장면의 윈도우 번호 범위 (윈도우 1개 = 10초)
- "--- 클립 #N ---": 다른 영상 파일의 경계

복잡한 판단이 필요한 장면은 Read 도구로 다음 파일에서 상세 설명/대사를 확인:
__DETAIL_FILES__

## 작업 — 모든 씬을 분류

원본 __TOTAL_DURATION__.

__DURATION_GUIDE__

모든 씬에 대해 다음 셋 중 하나로 결정하세요:

- **keep**: 씬 전체 유지. 단 **씬 길이가 윈도우 1개(10초) 이하**일 때만 사용.
- **partial**: 핵심 윈도우만 골라 유지 (나머지 자동 삭제). **윈도우 2개 이상 씬의 기본값**.
- **cut**: 씬 전체 삭제.

## 결정 규칙

### 1. 컷 길이 목표 (사용자 실제 편집 통계)
- **평균 컷 9초 ± 3초**, 절반은 5초 이하
- partial keep_windows는 보통 1~2개만 (한 윈도우=10초)
- **인접 윈도우 연속 keep 금지** — `[0, 1]`은 한 컷이 20초가 됨. `[0, 3]`처럼 점프

### 2. 말소리(★) 씬도 partial 가능
- 정보 가치 있는 발화(장비 설명/요리 해설/감상)는 **유지**
- 그러나 **말 사이 여백, 무의미 발화, 변화 없는 화면**은 partial로 cut 가능
- 30초+ 긴 말소리 씬도 핵심 발화 윈도우만 keep_windows로 지정
- 즉, 길이가 길고 비말 윈도우가 섞여 있으면 **partial로 줄이세요**

### 3. 비말소리 씬 (★ 없음)
- 윈도우 1개 → keep
- 윈도우 2개 이상 → **거의 모두 partial** — keep_windows 1~2개만
- 기본 패턴 (한 컷 = 1윈도우, 평균 9초 목표):
  - 2~3윈도우 씬 → `keep_windows: [0]` (10초 컷)
  - 4~6윈도우 씬 → `keep_windows: [0, 3]` 또는 `[2]` 만
  - 7+윈도우 씬 → `keep_windows: [0, 4]` 또는 핵심 1개

### 4. CUT 대상 (적극)
- 차량 운전(driving), 주차장/도로 단순 이동 (말소리 없음)
- 흔들림/NG, 의미 없는 촬영
- M:저이면서 변화 없는 정체 장면
- 같은 활동/장소가 이미 다른 씬에서 다뤄진 반복
- **활용도 낮은 영상은 통째로 cut OK** — 모든 영상을 살릴 필요 없음 (사용자 패턴: 30%는 통째 미사용)

### 5. 비슷한 씬이 여러 개 (시간순 분리됨)
- 같은 라벨이 **시간 흐름상 떨어져** 반복되면 **모션 높은 것** 1~2개만 keep, 나머지 cut
- 예: 아침 요리 + 저녁 요리는 시간대 다름 → 둘 다 keep 가능
- **한 영상에서 여러 짧은 컷 OK** (사용자도 한 영상에서 1~9개 컷 사용)

### 5-2. ⭐ 활동 클러스터는 균등 분배 (매우 중요)
같은 라벨이 **시간순 인접해 여러 씬에 연속**해 있으면 (예: 셋업이 영상 5개에 걸쳐 진행),
**모든 씬에서 짧게 1윈도우씩** 가져오세요. 한쪽만 길게 keep + 나머지 통째 cut은 금지.

❌ 나쁜 예: S5 setting_up keep(전체) + S6, S7, S8 cut → 흐름 끊김, 시청자 지루
✅ 좋은 예: S5, S6, S7, S8 각 partial([윈도우 1개씩]) → 짧게짧게 자연스러운 흐름

특히 **텐트/타프 셋업, 요리 과정, 불멍 시퀀스**는 시간 흐름이 있는 활동이라 분배 필수.

### 6. 흐름 보장 (느슨한 보호)
- 인트로(도착) → 셋업 → 활동 → 여유 → 마무리(철수) 큰 흐름은 살아있어야 함
- 단, 각 클립별 1씬 강제 keep은 아님 — 활용도가 정말 낮으면 클립 통째 cut OK
- 셋업/요리/불멍/풍경은 핵심 콘텐츠 — 짧게라도 1~2개 살리세요

## 출력 형식

먼저 1~2문장 판단 근거. 그 다음 `=== JSON ===` 줄 다음에 JSON만 출력:

```
편집 판단 근거...

=== JSON ===
{"reasoning": "한 줄 요약", "decisions": [
  {"scene": 1, "decision": "keep", "reason": "..."},
  {"scene": 2, "decision": "partial", "keep_windows": [5, 8], "reason": "..."},
  {"scene": 3, "decision": "cut", "reason": "..."}
]}
```

**모든 씬에 대해 결정을 출력하세요.** 누락된 씬은 자동으로 keep 처리되지만, 실수 방지를 위해 명시 권장.

PARTIAL의 keep_windows는 반드시 해당 씬의 W: 범위 안의 윈도우 번호여야 합니다.

=== 요약 스토리보드 ===

__STORYBOARD__
"""


CUT_ONLY_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer"},
                    "decision": {"type": "string"},
                    "keep_windows": {"type": "array", "items": {"type": "integer"}},
                    "hint": {"type": "string"},
                    "reason": {"type": "string"}
                },
                "required": ["scene", "decision", "reason"]
            }
        }
    },
    "required": ["reasoning", "decisions"]
}


# ---------------------------------------------------------------------------
# 목표 시간 초과 시 재편집 프롬프트
# ---------------------------------------------------------------------------

REEDIT_PROMPT_OLLAMA = """이전 편집 결과가 목표 시간을 초과했습니다.

현재 결과: {current_min}분 (목표: {target_minutes}분, 원본: {total_min}분)
**{over_min}분을 추가로 줄여야 합니다.**

## 현재 KEEP 장면 목록 (줄여야 할 대상)

{keep_summary}

## 요청

위 KEEP 장면 중 추가로 CUT하거나 PARTIAL할 장면을 선택하세요.

### 우선 CUT 대상
- 반복적이거나 비핵심적인 장면
- 짧은 비말소리 장면
- 이미 비슷한 장면이 다른 곳에서 유지되는 경우

### 보호 대상 (가급적 남기세요)
- ★ 말소리가 높은 장면 (대사/해설 포함)
- 내러티브 전환점 (도착, 셋업 시작, 식사, 마무리)
- 유일한 활동 장면 (해당 활동이 이 장면에서만 나옴)

내러티브 흐름이 끊기지 않도록 주의하세요.

## 출력

먼저 편집 판단 근거를 간단히 서술하세요.
그 후 === JSON === 구분자 아래에 **추가로 제거할 장면만** JSON 배열로 출력하세요.

[
  {{"scene": 5, "decision": "cut", "reason": "..."}},
  {{"scene": 8, "decision": "partial", "keep_windows": [30,31], "reason": "..."}},
  ...
]
"""

REEDIT_PROMPT_CLAUDE = """이전 편집 결과의 전체 길이가 목표를 크게 초과했습니다.
이번 라운드에서 **반드시 충분히 많이 줄여야** 합니다.

현재 전체 길이: **{current_min}분** (목표: **{target_minutes}분**, 초과: **{over_min}분**)
원본: {total_min}분

## 절대 규칙

1. **이번 응답으로 전체 길이가 {target_minutes}분 ± 2분이 되도록** 매우 적극적으로 추가 cut/partial을 적용하세요
2. **말소리(★) 장면도 길면 partial** — 말 사이 여백, 무의미 발화, 변화 없는 화면 cut
3. **활용도 낮은 영상은 통째로 cut OK** — 모든 영상을 보존할 필요 없음

## 적극적 감축 전략 — 모두 적용

- **반복 활동**: 같은 라벨/내용이 2개 이상이면 모션 가장 좋은 1개만 남기고 나머지 모두 cut
- **긴 partial을 더 짧게**: 현재 partial keep_windows가 2개+면 1개로 줄임
- **말소리 씬 partial**: 30초+ 말소리 씬도 핵심 발화 윈도우 1~2개만 keep
- **인접 비슷한 씬**: 시간 흐름상 가까이 있는 같은 활동은 1개만 keep
- **B-roll/풍경**: 핵심 1~2개만 남기고 cut
- **저모션/정체 장면**: 모두 cut
- 평균 컷 길이 9초 ± 3초 목표 (사용자 실제 편집 통계)

## 현재 KEEP 장면 목록 (이 중에서 골라서 줄이세요)

{keep_summary}

## 보호 대상 (cut 금지)

- ★ 말소리 중 핵심 발화 (단, 길면 partial로 사이 여백은 자르세요)
- 내러티브 전환점 (도착, 셋업 시작, 식사 시작, 마무리) 각 1개
- 유일한 활동 (셋업/요리/불멍/풍경 각 1~2개)

## 출력 형식

먼저 1~2문장 판단 근거. 그 다음 `=== JSON ===` 줄 다음에 JSON만 출력:

```
이번 라운드 판단: ...

=== JSON ===
{{"reasoning": "...", "decisions": [
  {{"scene": 5, "decision": "cut", "reason": "S04와 동일한 풍경 반복"}},
  {{"scene": 12, "decision": "partial", "keep_windows": [50], "reason": "긴 셋업을 1윈도우로 축소"}}
]}}
```

**충분히 많은 항목을 출력하세요** — 한 두 개만 줄여서는 {over_min}분을 못 줄입니다.
"""


def _split_storyboard_to_files(storyboard: str, scenes: list[dict], num_parts: int = 4) -> list[tuple[str, str]]:
    """상세 스토리보드를 장면 기준으로 분할하여 임시 파일로 저장.

    Returns: [(path, scene_range_label), ...] — 예: [("/tmp/xxx.txt", "S01~S33"), ...]
    """
    import math
    import re as _re
    import tempfile

    lines = storyboard.split("\n")

    # 장면 헤더 위치 + 번호 찾기 ([S01], [S02], ...)
    scene_starts = []  # (line_idx, scene_id)
    for i, line in enumerate(lines):
        m = _re.match(r"\[S(\d+)\]", line)
        if m:
            scene_starts.append((i, int(m.group(1))))

    if not scene_starts:
        chunk_size = math.ceil(len(lines) / num_parts)
        chunks = [("\n".join(lines[i:i + chunk_size]), "") for i in range(0, len(lines), chunk_size)]
    else:
        scenes_per_part = math.ceil(len(scene_starts) / num_parts)
        chunks = []
        for p in range(num_parts):
            start_idx = p * scenes_per_part
            end_idx = min((p + 1) * scenes_per_part, len(scene_starts))
            if start_idx >= len(scene_starts):
                break
            line_start = scene_starts[start_idx][0]
            if end_idx < len(scene_starts):
                line_end = scene_starts[end_idx][0]
            else:
                line_end = len(lines)
            if p == 0:
                line_start = 0
            chunk_text = "\n".join(lines[line_start:line_end]).strip()
            if chunk_text:
                first_sid = scene_starts[start_idx][1]
                last_sid = scene_starts[end_idx - 1][1]
                label = f"S{first_sid:02d}~S{last_sid:02d}"
                chunks.append((chunk_text, label))

    # 임시 파일로 저장
    results = []
    for i, (chunk, label) in enumerate(chunks):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=f"_detail_part{i+1}.txt",
            prefix="storyboard_", delete=False, encoding="utf-8"
        )
        f.write(chunk)
        f.close()
        results.append((f.name, label))
        _log(f"상세 스토리보드 파트{i+1} ({label}): {f.name} ({len(chunk)}자)")

    return results


def run_narrative_editing_claude(
    scenes: list[dict],
    all_windows: list[dict],
    total_duration: float,
    progress_callback=None,
    editing_comment: str = "",
    target_minutes: int = 0,
) -> list[dict]:
    """Claude 기반 내러티브 편집 — 요약 프롬프트 + 상세 파일 참조"""
    from claude_client import check_claude_available, call_claude_text

    if not scenes:
        return []

    _log(f"Claude 편집 시작: {len(scenes)}개 장면, {len(all_windows)}개 윈도우")
    if editing_comment:
        editing_comment = editing_comment[:500]
        _log(f"편집 코멘트: {editing_comment}")

    # Claude 가용성 확인
    if not check_claude_available():
        raise RuntimeError("Claude CLI를 찾을 수 없습니다. claude 명령어가 설치되어 있는지 확인하세요.")

    # 스토리보드 생성
    if progress_callback:
        progress_callback("editing", 83, "스토리보드 생성 중...")

    compact = generate_compact_storyboard(scenes, total_duration)
    detailed = generate_narrative_storyboard(scenes, total_duration)

    total_min = int(total_duration / 60)
    keep_min_low = int(total_min * 0.5)
    keep_min_high = int(total_min * 0.7)
    _log(f"요약 스토리보드: {len(compact)}자, 상세: {len(detailed)}자, 원본 {total_min}분")

    # 상세 스토리보드를 임시 파일로 분할 저장
    detail_parts = _split_storyboard_to_files(detailed, scenes)

    try:
        # 파일 경로 목록 텍스트 (장면 범위 포함)
        detail_files_text = "\n".join(
            f"- 파트{i+1} ({label}): {path}" for i, (path, label) in enumerate(detail_parts)
        )

        # 분량 가이드: target_minutes가 있으면 그걸 사용, 없으면 기본 50~70%
        if target_minutes > 0:
            default_guide = (
                f"## 목표 시간 — 절대 규칙\n\n"
                f"**완성본 전체 길이가 약 {target_minutes}분이 되도록** 편집하세요. "
                f"(말소리/비말소리 모두 포함한 합. ± 2분 이내)\n\n"
                f"이 목표를 맞추는 방법:\n"
                f"- 모든 씬을 PARTIAL 검토 (keep_windows 1~2개로 짧게)\n"
                f"- **말소리(★) 씬도 길면 partial** — 말 사이 여백, 무의미 발화, 변화 없는 화면 cut\n"
                f"- 같은 라벨 반복 씬은 **모션 가장 높은 1개만** 남기고 나머지 CUT\n"
                f"- 셋업/요리/불멍/풍경도 **각 활동 1~2회만** (반복 금지)\n"
                f"- **활용도 낮은 영상은 통째로 cut OK** (사용자 패턴: 영상 30%는 통째 미사용)\n"
                f"- 평균 컷 길이 **9초 ± 3초** 목표 (사용자 실제 편집 통계)\n"
            )
            _log(f"목표 시간 설정: 전체 합 {target_minutes}분")
        else:
            default_guide = (
                f"**완성본 전체 길이는 원본의 50~70%(약 {keep_min_low}~{keep_min_high}분)를 목표로 하세요.**\n"
                f"평균 컷 길이는 9초 ± 3초로 짧게 자주 끊어 주세요."
            )
        comment_section = ""
        if editing_comment:
            comment_section = f"\n\n## 편집자 요청 (이 요청이 최우선입니다 — 위의 기본 가이드라인보다 우선 적용하세요)\n\n{editing_comment}\n"

        # 편집 스타일 가이드 (조조캠핑/서주랜드 분석 기반)
        style_path = os.path.join(os.path.dirname(__file__), "editing_style.md")
        try:
            with open(style_path, "r", encoding="utf-8") as sf:
                editing_style = sf.read()
        except OSError:
            editing_style = ""

        prompt = (
            CLAUDE_EDITING_PROMPT
            .replace("__STORYBOARD__", compact)
            .replace("__DETAIL_FILES__", detail_files_text)
            .replace("__TOTAL_DURATION__", f"{total_min}분")
            .replace("__DURATION_GUIDE__", default_guide)
            .replace("__EDITING_STYLE__", editing_style)
            .replace("=== 요약 스토리보드 ===", f"{comment_section}=== 요약 스토리보드 ===")
        )

        # Claude 편집 호출
        if progress_callback:
            progress_callback("editing", 85, "Claude 편집 판단 중...")

        _log(f"Claude 편집 호출 시작... (프롬프트 {len(prompt)}자, {len(scenes)}개 장면)")
        _log(f"=== Claude 프롬프트 ===\n{prompt}\n=== 프롬프트 끝 ===")
        response = call_claude_text(prompt, model="claude-opus-4-7", timeout=1800)
        _log(f"=== Claude 응답 ({len(response)}자) ===\n{response}\n=== 응답 끝 ===")
    finally:
        # 임시 파일 정리
        for path, _ in detail_parts:
            try:
                os.unlink(path)
            except OSError:
                pass

    if not response:
        raise RuntimeError("Claude 응답 없음 — 타임아웃이거나 CLI 오류입니다. 다시 시도해주세요.")

    # 응답 파싱
    decisions = _parse_claude_editing_response(response)
    if decisions is None:
        # 기존 파서로 폴백
        _log("Claude 구조화 파싱 실패, 기존 파서 시도...")
        decisions = _parse_editing_output(response)
        if decisions is None:
            raise RuntimeError("Claude 응답 파싱 실패 — 응답 형식이 올바르지 않습니다.")

    _log(f"Claude 편집 파싱 완료: {len(decisions)}개 판단")

    # 후처리 파이프라인 (기존 코드 100% 재사용)
    if progress_callback:
        progress_callback("editing", 90, "편집 결과 적용 중...")

    decisions = _fill_missing_scenes(decisions, scenes)
    decisions = _cap_long_scenes(decisions, scenes, all_windows)
    decisions = _protect_speech_in_partial(decisions, scenes, all_windows)
    decisions = _ensure_all_files_present(decisions, scenes, all_windows)
    decisions = _distribute_activity_clusters(decisions, scenes, all_windows)

    # 결정 통계 로그
    keep_count = sum(1 for d in decisions if d.get("decision") == "keep")
    partial_count = sum(1 for d in decisions if d.get("decision") == "partial")
    cut_count = sum(1 for d in decisions if d.get("decision") == "cut")
    _log(f"편집 결정: KEEP {keep_count}, PARTIAL {partial_count}, CUT {cut_count}")

    # 결정 적용 -> 세그먼트 생성
    keep_segments = _apply_decisions(decisions, scenes, all_windows)
    total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
    nonspeech_min = _calc_nonspeech_keep_min(decisions, scenes, all_windows)
    _log(f"Claude 편집 완료: {len(keep_segments)}개 세그먼트, 전체 {total_keep / 60:.1f}분 (비말소리 {nonspeech_min:.1f}분)")

    # -------------------------------------------------------------------
    # 목표 시간 초과 시 재편집 (최대 3회)
    # 비교 기준: 비말소리 keep 시간 (말소리는 보존이 우선이라 제외)
    # -------------------------------------------------------------------
    MAX_REEDIT = 5
    TOLERANCE = 1.07  # 7% 여유 (30분 목표 → 32분까지 허용)

    for reedit_round in range(MAX_REEDIT):
        current_min = total_keep / 60  # 전체 keep 시간 비교
        if target_minutes <= 0 or current_min <= target_minutes * TOLERANCE:
            break

        over_min = int(current_min - target_minutes)
        _log(f"비말소리 시간 초과: {current_min:.1f}분 > {target_minutes}분 (Claude 재편집 {reedit_round + 1}/{MAX_REEDIT})")

        if progress_callback:
            progress_callback(
                "editing", 93 + reedit_round,
                f"비말소리 초과 ({current_min:.0f}분>{target_minutes}분), Claude 재편집 {reedit_round + 1}차...",
            )

        keep_summary = _build_keep_summary(decisions, scenes, target_minutes)
        reedit_prompt = REEDIT_PROMPT_CLAUDE.format(
            current_min=int(current_min),
            target_minutes=target_minutes,
            total_min=total_min,
            over_min=over_min,
            keep_summary=keep_summary,
        )

        _log(f"Claude 재편집 호출... (프롬프트 {len(reedit_prompt)}자)")
        reedit_response = call_claude_text(reedit_prompt, model="claude-opus-4-7", timeout=1800)

        if not reedit_response:
            _log("Claude 재편집: 응답 없음, 중단")
            break

        _log(f"=== Claude 재편집 응답 ({len(reedit_response)}자) ===\n{reedit_response}\n=== 응답 끝 ===")

        new_cuts = _parse_claude_editing_response(reedit_response)
        if new_cuts is None:
            new_cuts = _parse_editing_output(reedit_response)
        if not new_cuts:
            _log("Claude 재편집: 추가 CUT 없음, 중단")
            break

        _log(f"Claude 재편집 {reedit_round + 1}차: {len(new_cuts)}개 추가 CUT/PARTIAL")

        prev_total_min = total_keep / 60
        decisions = _merge_reedit_decisions(decisions, new_cuts)
        decisions = _fill_missing_scenes(decisions, scenes)
        decisions = _cap_long_scenes(decisions, scenes, all_windows)
        decisions = _protect_speech_in_partial(decisions, scenes, all_windows)
        decisions = _ensure_all_files_present(decisions, scenes, all_windows)
        decisions = _distribute_activity_clusters(decisions, scenes, all_windows)

        keep_count = sum(1 for d in decisions if d.get("decision") == "keep")
        partial_count = sum(1 for d in decisions if d.get("decision") == "partial")
        cut_count = sum(1 for d in decisions if d.get("decision") == "cut")
        _log(f"재편집 후: KEEP {keep_count}, PARTIAL {partial_count}, CUT {cut_count}")

        keep_segments = _apply_decisions(decisions, scenes, all_windows)
        total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
        nonspeech_min = _calc_nonspeech_keep_min(decisions, scenes, all_windows)
        cur_total_min = total_keep / 60
        _log(f"재편집 후: {len(keep_segments)}개 세그먼트, 전체 {cur_total_min:.1f}분 (비말소리 {nonspeech_min:.1f}분)")

        # 수렴 정체 감지: 1분 이상 줄지 않으면 중단 (LLM이 더 못 줄임)
        if prev_total_min - cur_total_min < 1.0:
            _log(f"재편집 정체 감지 ({prev_total_min:.1f}→{cur_total_min:.1f}분), 중단")
            break

    # -------------------------------------------------------------------
    # 마지막 안전망: LLM 재편집 후에도 목표 초과면 강제 trim
    # -------------------------------------------------------------------
    if target_minutes > 0:
        current_min = total_keep / 60
        if current_min > target_minutes * 1.07:
            decisions = _force_trim_to_target(
                decisions, scenes, all_windows, target_minutes, tolerance=1.05
            )
            keep_segments = _apply_decisions(decisions, scenes, all_windows)
            total_keep = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
            _log(f"최종(강제trim 후): {len(keep_segments)}개 세그먼트, 전체 {total_keep / 60:.1f}분")

    return keep_segments


def _parse_claude_editing_response(response: str) -> list[dict] | None:
    """Claude 편집 응답 파싱 — JSON 객체에서 decisions 추출"""
    text = response.strip()

    # 1차: JSON 객체 파싱 ({"reasoning": ..., "decisions": [...]})
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "decisions" in obj:
            decisions = obj["decisions"]
            if isinstance(decisions, list):
                reasoning = obj.get("reasoning", "")
                if reasoning:
                    _log(f"Claude 편집 근거: {reasoning[:200]}")
                return decisions
    except json.JSONDecodeError:
        pass

    # 2차: ```json ... ``` 블록
    json_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\s*\n```", text)
    if json_match:
        try:
            obj = json.loads(json_match.group(1))
            if isinstance(obj, dict) and "decisions" in obj:
                return obj["decisions"]
            if isinstance(obj, list):
                return obj
        except json.JSONDecodeError:
            pass

    # 3차: 텍스트에서 { ... } 추출
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end])
            if isinstance(obj, dict) and "decisions" in obj:
                return obj["decisions"]
        except json.JSONDecodeError:
            pass

    return None
