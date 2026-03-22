"""장면 점수 산출 — 코드 기반 편집 판단 보조

각 장면에 0~100 점수를 부여하고, 편집 플래그를 생성한다.
scoring + budget selection 파이프라인의 첫 단계.
"""

import sys
from scene_detector import window_has_speech, TRAIL_KEYWORDS


def _log(msg: str):
    print(f"[scene_scorer] {msg}", file=sys.stderr, flush=True)


def _is_trail_walking(scene: dict) -> bool:
    """장면 설명에서 트레일/등산 여부 판별"""
    desc_text = " ".join(scene.get("descs", [])).lower()
    return any(kw in desc_text for kw in TRAIL_KEYWORDS)


# ---------------------------------------------------------------------------
# 콘텐츠 유형별 기본 가중치
# ---------------------------------------------------------------------------
ACTION_WEIGHTS = {
    "cooking":      25,
    "eating":       25,
    "fire_tending": 20,
    "setting_up":   20,
    "showing_gear": 15,
    "scenery":      20,
    "talking":      25,
    "dark":         10,
    "resting":       5,
    "driving":       3,
    "unknown":       5,
    "no_frame":      0,
    "start":        10,
}


def _speech_score(scene: dict, all_windows: list[dict]) -> float:
    """말소리 가중치 (최대 30점)

    has_speech=True이면 기본 15점 + 대사 밀도 비례 추가 15점.
    """
    if not scene.get("has_speech"):
        return 0.0

    speech_ratio = scene.get("speech_ratio", 0.0)
    return 15.0 + speech_ratio * 15.0


def _content_score(scene: dict) -> float:
    """콘텐츠 유형 가중치 (최대 30점)

    walking은 트레일 여부에 따라 분기.
    """
    action = scene.get("action", "unknown")

    if action == "walking":
        if _is_trail_walking(scene):
            return 15.0
        return 5.0

    weight = ACTION_WEIGHTS.get(action, 5)
    return min(float(weight), 30.0)


def _motion_visual_score(scene: dict, all_windows: list[dict]) -> float:
    """모션/시각 다양성 점수 (최대 20점)

    motion 값 + brightness 변화를 조합.
    """
    wids = scene.get("window_ids", [])
    if not wids:
        return 0.0

    # 모션 점수 (0~10점): avg_motion 기준
    avg_motion = scene.get("avg_motion", 0.0)
    if avg_motion >= 0.08:
        motion_pts = 10.0
    elif avg_motion >= 0.03:
        motion_pts = 7.0
    elif avg_motion >= 0.01:
        motion_pts = 4.0
    else:
        motion_pts = 1.0

    # brightness 다양성 (0~10점): 윈도우 간 brightness 변화 폭
    brightnesses = []
    for wid in wids:
        if 0 <= wid < len(all_windows):
            brightnesses.append(all_windows[wid].get("brightness", 0.5))

    if len(brightnesses) >= 2:
        brightness_range = max(brightnesses) - min(brightnesses)
        visual_pts = min(brightness_range / 0.3, 1.0) * 10.0
    else:
        visual_pts = 4.0  # 윈도우 1개면 중간값

    return min(motion_pts + visual_pts, 20.0)


def _narrative_score(scene: dict, total_duration: float) -> float:
    """내러티브 위치 보너스 (최대 20점)

    - 영상 시작 (첫 10%): +10점 (오프닝)
    - 영상 끝 (마지막 10%): +10점 (엔딩)
    - 시간대 전환점 (시작 후 40~60% 위치): +7점 (중간 피벗)
    - 첫 번째 장면: +9점 (인트로 보호)
    """
    if total_duration <= 0:
        return 0.0

    start = scene.get("start", 0.0)
    end = scene.get("end", 0.0)
    mid = (start + end) / 2.0
    position = mid / total_duration

    pts = 0.0

    # 시작 10%
    if position <= 0.10:
        pts += 10.0

    # 끝 10%
    if position >= 0.90:
        pts += 10.0

    # 중간 전환점 (40~60%)
    if 0.40 <= position <= 0.60:
        pts += 7.0

    # 첫 번째 장면 (ID=1)
    if scene.get("id") == 1:
        pts += 9.0

    return min(pts, 20.0)


# ---------------------------------------------------------------------------
# 플래그 판정
# ---------------------------------------------------------------------------

def _compute_flags(
    scene: dict,
    all_windows: list[dict],
    prev_actions: list[str],
) -> list[str]:
    """편집 관련 플래그를 생성"""
    flags = []
    action = scene.get("action", "unknown")
    duration = scene.get("duration", 0.0)

    # has_speech: 말소리 정보용 (forced_keep 트리거 아님)
    if scene.get("has_speech"):
        flags.append("has_speech")

    # trail_content: 트레일/등산 이동 보호
    if action == "walking" and _is_trail_walking(scene):
        flags.append("trail_content")

    # driving_cut: 운전 장면 → 적극 CUT
    if action == "driving":
        flags.append("driving_cut")

    # repetitive: 같은 action이 이전 장면에서 이미 등장
    if action in prev_actions:
        flags.append("repetitive")

    # long_no_speech: 60초 이상 비말소리 → PARTIAL 후보
    if duration >= 60 and not scene.get("has_speech"):
        flags.append("long_no_speech")

    # long_speech: 120초 이상 말소리 → PARTIAL 후보
    if duration >= 120 and scene.get("has_speech"):
        flags.append("long_speech")

    return flags


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------

def score_scene(
    scene: dict,
    all_windows: list[dict],
    total_duration: float,
    prev_actions: list[str] | None = None,
) -> dict:
    """장면 점수 산출

    입력:
        scene: scene_detector.group_windows_to_scenes 출력의 한 요소
        all_windows: 전체 윈도우 리스트
        total_duration: 전체 영상 길이 (초)
        prev_actions: 이전 장면들의 action 리스트 (반복 판별용)

    출력:
        {"score": float 0~100, "breakdown": dict, "flags": list[str]}
    """
    if prev_actions is None:
        prev_actions = []

    speech = _speech_score(scene, all_windows)
    content = _content_score(scene)
    motion_visual = _motion_visual_score(scene, all_windows)
    narrative = _narrative_score(scene, total_duration)

    # 반복 감점: repetitive이면 콘텐츠 점수 30% 감점
    flags = _compute_flags(scene, all_windows, prev_actions)
    if "repetitive" in flags:
        content *= 0.7

    # driving_cut이면 콘텐츠 점수 추가 감점
    if "driving_cut" in flags and not scene.get("has_speech"):
        content *= 0.3

    raw = speech + content + motion_visual + narrative
    score = max(0.0, min(100.0, raw))

    breakdown = {
        "speech": round(speech, 1),
        "content": round(content, 1),
        "motion_visual": round(motion_visual, 1),
        "narrative": round(narrative, 1),
    }

    return {
        "score": round(score, 1),
        "breakdown": breakdown,
        "flags": flags,
    }


def score_all_scenes(
    scenes: list[dict],
    all_windows: list[dict],
    total_duration: float,
) -> list[dict]:
    """모든 장면에 점수를 매기고 결과를 반환

    각 scene dict에 "score", "breakdown", "flags" 키를 추가한다.
    """
    prev_actions: list[str] = []
    results = []

    for scene in scenes:
        result = score_scene(scene, all_windows, total_duration, prev_actions)
        scene["score"] = result["score"]
        scene["breakdown"] = result["breakdown"]
        scene["flags"] = result["flags"]
        results.append(result)

        prev_actions.append(scene.get("action", "unknown"))

    # 통계 로그
    if results:
        scores = [r["score"] for r in results]
        avg = sum(scores) / len(scores)
        high = sum(1 for s in scores if s >= 60)
        low = sum(1 for s in scores if s < 30)
        _log(
            f"점수 산출 완료: {len(results)}개 장면, "
            f"평균 {avg:.1f}점, "
            f"고점(≥60) {high}개, 저점(<30) {low}개"
        )

    return results
