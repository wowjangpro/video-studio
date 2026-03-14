"""Stage 2: 비전 태깅 — 슬라이딩 윈도우 장면 분류

인접 윈도우 맥락(이전/현재/다음)으로 행동 태깅 정확도를 높임.
비전 모델은 행동/구도/설명만 출력하고, 편집 판단은 storyboard.py의 LLM이 수행.
"""

import os
import subprocess
import base64
import json
import sys
import tempfile

import httpx

VISION_MODEL = "qwen2.5vl:7b"
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

VALID_ACTIONS = {
    "cooking", "eating", "fire_tending", "setting_up", "showing_gear",
    "walking", "driving", "resting", "scenery", "dark", "talking",
}

_PROMPT_BODY = """

행동 (하나만 선택):
- cooking: 음식을 준비하거나 요리하고 있다
- eating: 먹거나 마시고 있다
- fire_tending: 불을 피우거나 장작을 넣거나 화로를 관리하고 있다
- setting_up: 텐트/타프/장비를 설치하거나 정리하고 있다
- showing_gear: 장비를 카메라에 보여주거나 소개하고 있다
- walking: 걷거나 등산하거나 이동하고 있다
- driving: 차를 타고 이동 중이다
- resting: 앉아서 쉬거나 특별한 행동 없이 가만히 있다
- talking: 카메라를 향해 말하거나 설명하고 있다 (장비/음식이 아닌 일반 토크)
- scenery: 사람 없이 자연 풍경만 보인다
- dark: 화면이 매우 어둡다

구도:
- closeup: 피사체가 화면 대부분을 차지 (클로즈업)
- wide: 넓은 전경이나 캠프 전체가 보임

화면 품질 (usable):
- yes: 정상적으로 촬영된 화면, 편집에 사용 가능
- no: 사용 불가 (심한 흔들림, 초점 나감, 바닥/하늘만 찍힘, 렌즈 가림, 의도 없는 촬영)
- marginal: 품질이 좋진 않지만 내용은 알아볼 수 있음

장면 설명 (구체적으로):
- 주인공이 무엇을 하고 있는지 구체적으로 설명하세요.
- 어떤 재료/도구/장비를 다루는지 포함하세요.
- 주변 분위기를 간단히 포함하세요 (밤/낮, 실내/실외, 날씨 등).
- 배경에 보이는 무관한 물건은 무시하세요.

JSON만 출력하세요: {"action": "cooking", "shot": "closeup", "desc": "저녁에 화로 옆에서 양파를 칼로 썰고 있다", "usable": "yes", "usable_reason": ""}"""

PROMPT_TAG = "아웃도어 영상의 연속된 프레임들입니다. 전체를 종합하여 아래 항목을 판단하세요." + _PROMPT_BODY


def _build_context_prompt(n_frames: int, center_idx: int) -> str:
    """슬라이딩 윈도우용 맥락 프롬프트 생성"""
    center_num = center_idx + 1

    if n_frames == 3:
        frame_desc = "프레임 1: 이전 구간 / 프레임 2: 현재 구간 / 프레임 3: 다음 구간"
    elif center_idx == 0:
        frame_desc = "프레임 1: 현재 구간 / 프레임 2: 다음 구간"
    else:
        frame_desc = "프레임 1: 이전 구간 / 프레임 2: 현재 구간"

    return (
        "아웃도어 영상의 연속된 10초 구간에서 각각 추출한 프레임입니다.\n"
        f"{frame_desc}\n\n"
        f"현재 구간(프레임 {center_num})에서 주인공이 무엇을 하고 있는지 판단하세요.\n"
        "앞뒤 프레임은 시간 흐름 참고용입니다."
    ) + _PROMPT_BODY


def _log(msg: str):
    print(f"[stage2] {msg}", file=sys.stderr, flush=True)


def extract_representative_frame(video_path: str, time_sec: float) -> str | None:
    """영상에서 특정 시간의 프레임을 base64 JPEG로 추출"""
    cmd = [
        "ffmpeg",
        "-ss", str(time_sec),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=768:-1",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-q:v", "5",
        "-v", "quiet",
        "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        _log(f"프레임 추출 타임아웃: {video_path} @ {time_sec:.1f}s")
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    return base64.b64encode(result.stdout).decode("utf-8")


def _call_ollama(images_b64: list[str], prompt: str) -> dict:
    """Ollama API 호출 (다중 이미지 지원)"""
    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": VISION_MODEL,
                "prompt": prompt,
                "images": images_b64,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 200},
            },
            timeout=120.0,
        )
        text = response.json().get("response", "")

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        _log(f"Ollama 호출 실패: {e}")

    return {}


def tag_window_context(
    video_path: str,
    prev_window: tuple[float, float] | None,
    curr_window: tuple[float, float],
    next_window: tuple[float, float] | None,
) -> tuple[str, str, str, str, str]:
    """슬라이딩 윈도우 비전 태깅 — 인접 윈도우 맥락으로 정확도 향상

    3개 인접 윈도우(이전/현재/다음)에서 각 1프레임(50% 지점)을 추출하여
    비전 모델에 30초 시간 흐름 맥락을 제공합니다.
    """
    frames_b64 = []
    center_idx = 0

    if prev_window:
        mid = prev_window[0] + (prev_window[1] - prev_window[0]) * 0.5
        frame = extract_representative_frame(video_path, mid)
        if frame:
            frames_b64.append(frame)

    center_idx = len(frames_b64)
    mid = curr_window[0] + (curr_window[1] - curr_window[0]) * 0.5
    frame = extract_representative_frame(video_path, mid)
    if frame:
        frames_b64.append(frame)

    if next_window:
        mid = next_window[0] + (next_window[1] - next_window[0]) * 0.5
        frame = extract_representative_frame(video_path, mid)
        if frame:
            frames_b64.append(frame)

    if not frames_b64:
        return "no_frame", "", "", "no", "프레임 추출 실패"

    if center_idx >= len(frames_b64):
        center_idx = max(0, len(frames_b64) - 1)

    if len(frames_b64) >= 2:
        prompt = _build_context_prompt(len(frames_b64), center_idx)
    else:
        prompt = PROMPT_TAG

    result = _call_ollama(frames_b64, prompt)

    label = result.get("action", "unknown")
    shot = result.get("shot", "")
    desc = result.get("desc", "")
    usable = result.get("usable", "yes")
    usable_reason = result.get("usable_reason", "")

    if label not in VALID_ACTIONS:
        label = "unknown"
    if shot not in ("closeup", "wide"):
        shot = ""
    if usable not in ("yes", "no", "marginal"):
        usable = "yes"

    ctx = f"{len(frames_b64)}f/ctx" if len(frames_b64) >= 2 else f"{len(frames_b64)}f"
    _log(f"  tag: action={label} shot={shot} usable={usable} desc={desc} ({ctx})")

    return label, shot, desc, usable, usable_reason


# ---------------------------------------------------------------------------
# Claude 배치 비전 태깅
# ---------------------------------------------------------------------------

def extract_frame_to_file(video_path: str, time_sec: float, output_path: str) -> bool:
    """영상에서 특정 시간의 프레임을 JPEG 파일로 저장"""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=768:-1",
        "-q:v", "3",
        "-v", "quiet",
        output_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
        return os.path.exists(output_path)
    except Exception:
        return False


def _parse_claude_tag(tag: dict) -> tuple[str, str, str, str, str]:
    """Claude 비전 응답을 (action, shot, desc, usable, usable_reason)으로 변환"""
    label = tag.get("action", "unknown")
    shot = tag.get("shot", "")
    desc = tag.get("desc", "")
    usable = tag.get("usable", "yes")
    usable_reason = tag.get("usable_reason", "")

    if label not in VALID_ACTIONS:
        label = "unknown"
    if shot not in ("closeup", "wide"):
        shot = ""
    if usable not in ("yes", "no", "marginal"):
        usable = "yes"

    return label, shot, desc, usable, usable_reason


def tag_windows_batch_claude(
    video_path: str,
    windows: list[dict],
    batch_size: int = 10,
    progress_callback=None,
) -> dict[int, tuple[str, str, str, str, str]]:
    """Claude 비전으로 배치 태깅 — 여러 프레임을 한 번에 분석

    Returns: {window_index: (action, shot, desc, usable, usable_reason)}
    """
    from claude_client import call_claude_vision

    results = {}
    tmp_dir = tempfile.mkdtemp(prefix="ai-movie-cut-frames-")

    try:
        # 프레임 추출
        frame_paths = {}
        for i, w in enumerate(windows):
            mid = w["start"] + (w["end"] - w["start"]) * 0.5
            path = os.path.join(tmp_dir, f"w{i:04d}.jpg")
            if extract_frame_to_file(video_path, mid, path):
                frame_paths[i] = path

        if not frame_paths:
            _log("프레임 추출 실패: 모든 윈도우")
            return results

        # 배치 처리
        indices = sorted(frame_paths.keys())
        total_batches = (len(indices) + batch_size - 1) // batch_size

        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            batch_indices = indices[start:start + batch_size]
            batch_paths = [frame_paths[i] for i in batch_indices]

            # 프롬프트 구성
            window_info = "\n".join(
                f"- 이미지 {j+1} (윈도우 {idx}, {windows[idx]['start']:.1f}~{windows[idx]['end']:.1f}초): {frame_paths[idx]}"
                for j, idx in enumerate(batch_indices)
            )

            prompt = (
                f"캠핑/아웃도어 브이로그 영상의 프레임 {len(batch_indices)}장입니다.\n"
                f"각 프레임에 대해 아래 항목을 분석해주세요.\n\n"
                f"{window_info}\n\n"
                f"{_PROMPT_BODY}\n\n"
                f"각 이미지에 대해 JSON 배열로 출력하세요:\n"
                f'[{{"window": 0, "action": "...", "shot": "...", "desc": "...", "usable": "...", "usable_reason": "..."}}]\n'
                f"window 값은 위 목록의 윈도우 번호를 그대로 사용하세요."
            )

            _log(f"Claude 배치 {batch_idx+1}/{total_batches}: 윈도우 {len(batch_indices)}개")

            if progress_callback:
                pct = int((batch_idx / total_batches) * 100)
                progress_callback(f"Claude 비전 분석 중... ({batch_idx+1}/{total_batches})", pct)

            response = call_claude_vision(prompt, batch_paths, model="sonnet", timeout=180)

            if not response:
                _log(f"Claude 배치 {batch_idx+1} 응답 없음")
                for idx in batch_indices:
                    results[idx] = ("unknown", "", "", "yes", "claude 응답 없음")
                continue

            # JSON 배열 파싱
            tags = _parse_batch_response(response, batch_indices)
            for idx, tag in tags.items():
                results[idx] = _parse_claude_tag(tag)
                _log(f"  tag[{idx}]: action={results[idx][0]} desc={results[idx][2][:30]}")

            # 파싱 실패한 윈도우는 기본값
            for idx in batch_indices:
                if idx not in results:
                    results[idx] = ("unknown", "", "", "yes", "파싱 실패")

    finally:
        # 임시 파일 정리
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


def _parse_batch_response(response: str, batch_indices: list[int]) -> dict[int, dict]:
    """Claude 배치 응답에서 JSON 배열을 파싱"""
    # JSON 배열 추출 시도
    text = response.strip()

    # 1차: 전체가 JSON 배열인 경우
    if text.startswith("["):
        try:
            items = json.loads(text)
            return _map_items_to_indices(items, batch_indices)
        except json.JSONDecodeError:
            pass

    # 2차: ```json ... ``` 블록 추출
    import re
    json_match = re.search(r"```(?:json)?\s*\n(\[[\s\S]*?\])\s*\n```", text)
    if json_match:
        try:
            items = json.loads(json_match.group(1))
            return _map_items_to_indices(items, batch_indices)
        except json.JSONDecodeError:
            pass

    # 3차: 텍스트에서 [ ... ] 추출
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            items = json.loads(text[start:end])
            return _map_items_to_indices(items, batch_indices)
        except json.JSONDecodeError:
            pass

    _log(f"배치 응답 파싱 실패: {text[:200]}")
    return {}


def _map_items_to_indices(items: list, batch_indices: list[int]) -> dict[int, dict]:
    """파싱된 JSON 아이템을 윈도우 인덱스에 매핑"""
    result = {}
    if not isinstance(items, list):
        return result

    for item in items:
        if not isinstance(item, dict):
            continue
        # window 필드가 있으면 직접 매핑
        wid = item.get("window")
        if wid is not None and wid in batch_indices:
            result[wid] = item
            continue

    # window 매핑 실패 시 순서대로 매핑
    if not result and len(items) == len(batch_indices):
        for idx, item in zip(batch_indices, items):
            if isinstance(item, dict):
                result[idx] = item

    return result
