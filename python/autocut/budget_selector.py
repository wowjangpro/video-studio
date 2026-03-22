"""예산 기반 장면 선택 + 중복 제거

scored_scenes(scene + score/breakdown/flags)를 받아서
예산(target_minutes)에 맞춰 장면을 선택한다.
하이브리드 모드에서는 LLM 입력용 후보 리스트를 생성하고,
scored 단독 모드에서는 keep_segments를 직접 생성한다.
"""

import sys

from scene_detector import window_has_speech

# 중복 제거 제외 action (단계별로 다른 의미를 가짐)
# cooking: 준비/조리/완성 각각 다른 단계
# talking: 대화 내용이 다를 수 있음 — Claude가 판단
DEDUP_EXEMPT_ACTIONS = {"cooking", "talking"}

# 긴 장면 PARTIAL 임계값 (초)
LONG_SCENE_THRESHOLD = 60
LONG_SPEECH_THRESHOLD = 120


def _log(msg: str):
    print(f"[budget_selector] {msg}", file=sys.stderr, flush=True)


def _format_duration(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m > 0:
        return f"{m}분 {s}초"
    return f"{s}초"


# ---------------------------------------------------------------------------
# 1. 중복 제거 (dedup)
# ---------------------------------------------------------------------------

def _dedup_scenes(scenes: list[dict]) -> list[dict]:
    """같은 action + 5분 이내 장면 중 score 최고만 남기고 나머지 제거

    cooking은 예외 — 준비/조리/완성이 각각 다른 단계이므로 dedup 제외.
    반환: dedup 후 유지할 장면 리스트 (원본 리스트는 변경하지 않음)
    """
    action_groups: dict[str, list[dict]] = {}
    for scene in scenes:
        action = scene.get("action", "unknown")
        if action in DEDUP_EXEMPT_ACTIONS:
            continue
        action_groups.setdefault(action, []).append(scene)

    dedup_cut_ids: set[int] = set()

    for action, group in action_groups.items():
        sorted_group = sorted(group, key=lambda s: s["start"])

        clusters: list[list[dict]] = []
        current_cluster: list[dict] = []

        for scene in sorted_group:
            if not current_cluster:
                current_cluster.append(scene)
            elif scene["start"] - current_cluster[0]["start"] <= 300:
                current_cluster.append(scene)
            else:
                clusters.append(current_cluster)
                current_cluster = [scene]
        if current_cluster:
            clusters.append(current_cluster)

        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            best = max(cluster, key=lambda s: s.get("score", 0))
            for scene in cluster:
                if scene["id"] != best["id"]:
                    dedup_cut_ids.add(scene["id"])

    if dedup_cut_ids:
        _log(f"중복 제거: {len(dedup_cut_ids)}개 장면 제거 (같은 action + 5분 이내)")

    return [s for s in scenes if s["id"] not in dedup_cut_ids]


# ---------------------------------------------------------------------------
# 2. (제거됨) action 비율 cap — Claude가 편집 판단하므로 미적용
# ---------------------------------------------------------------------------

    return [s for s in selected if s["id"] not in remove_ids]


# ---------------------------------------------------------------------------
# 3. hard trim (최종 예산 안전망)
# ---------------------------------------------------------------------------

def hard_trim(keep_segments: list[dict], budget_seconds: float) -> list[dict]:
    """최종 안전장치: 총 duration이 예산을 초과하면 score 낮은 것부터 제거

    keep_segments (globalStart/globalEnd 기반)에서 작동.
    """
    if budget_seconds <= 0:
        return keep_segments

    total = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
    if total <= budget_seconds:
        return keep_segments

    # scene_id 기준으로 그룹핑하여 장면 단위로 제거
    scene_groups: dict[int, list[dict]] = {}
    for seg in keep_segments:
        sid = seg.get("scene_id", seg.get("id", 0))
        scene_groups.setdefault(sid, []).append(seg)

    # 각 장면의 총 시간과 최소 score
    scene_info = []
    for sid, segs in scene_groups.items():
        dur = sum(s["globalEnd"] - s["globalStart"] for s in segs)
        min_score = min(s.get("score", 0) for s in segs)
        scene_info.append({"sid": sid, "duration": dur, "score": min_score})

    # score 낮은 장면부터 제거
    scene_info.sort(key=lambda x: x["score"])
    excess = total - budget_seconds
    removed_sids: set[int] = set()
    removed_dur = 0.0

    for info in scene_info:
        if removed_dur >= excess:
            break
        removed_sids.add(info["sid"])
        removed_dur += info["duration"]

    if removed_sids:
        _log(f"hard trim: {len(removed_sids)}개 장면 제거 ({_format_duration(removed_dur)} 초과)")

    return [
        s for s in keep_segments
        if s.get("scene_id", s.get("id", 0)) not in removed_sids
    ]


# ---------------------------------------------------------------------------
# 4. 긴 장면 PARTIAL 처리
# ---------------------------------------------------------------------------

def _select_keep_windows(
    scene: dict,
    all_windows: list[dict],
    max_windows: int = 6,
) -> list[int]:
    """긴 장면에서 핵심 윈도우만 선택

    - 말소리 윈도우 우선
    - 비말소리: 시작/끝 + 균등 샘플링
    """
    wids = scene["window_ids"]

    speech_wids = [
        wid for wid in wids
        if 0 <= wid < len(all_windows) and window_has_speech(all_windows[wid])
    ]

    if speech_wids:
        non_speech = [wid for wid in wids if wid not in set(speech_wids)]
        remaining = max(0, max_windows - len(speech_wids))
        if remaining > 0 and non_speech:
            step = max(1, len(non_speech) // remaining)
            sampled = non_speech[::step][:remaining]
            selected = sorted(set(speech_wids) | set(sampled))
        else:
            selected = sorted(speech_wids[:max_windows])
    else:
        if len(wids) <= max_windows:
            selected = list(wids)
        else:
            middle = wids[1:-1]
            inner_count = max(0, max_windows - 2)
            if inner_count > 0 and middle:
                step = max(1, len(middle) // inner_count)
                inner = middle[::step][:inner_count]
            else:
                inner = []
            selected = sorted(set([wids[0], wids[-1]]) | set(inner))

    return selected


# ---------------------------------------------------------------------------
# 5. 메인 선택 함수
# ---------------------------------------------------------------------------

def select_scenes(
    scored_scenes: list[dict],
    all_windows: list[dict],
    target_minutes: int,
    total_duration: float,
    budget_ratio: float = 1.0,
) -> list[dict]:
    """예산 기반 장면 선택 + 중복 제거 → keep_segments 반환

    Parameters
    ----------
    scored_scenes : scene dict + "score", "breakdown", "flags" 필드
    all_windows : 전체 윈도우 리스트
    target_minutes : 목표 분량 (0이면 자동 40~60%)
    total_duration : 원본 영상 총 길이 (초)
    budget_ratio : 예산 배수 (1.0=정확, 1.2=120% 여유 — 하이브리드 모드용)

    Returns
    -------
    keep_segments : merger.merge_adjacent_segments 입력과 호환되는 리스트
    """
    if not scored_scenes:
        return []

    total_min = total_duration / 60
    _log(f"장면 선택 시작: {len(scored_scenes)}개 장면, 원본 {total_min:.1f}분")

    # --- 목표 시간 결정 ---
    if target_minutes > 0:
        budget_seconds = target_minutes * 60.0 * budget_ratio
    else:
        if total_duration <= 30 * 60:
            ratio = 0.60
        elif total_duration <= 60 * 60:
            ratio = 0.50
        else:
            ratio = 0.40
        budget_seconds = total_duration * ratio * budget_ratio
        _log(f"자동 예산: {_format_duration(budget_seconds)} (비율 {ratio:.0%} × {budget_ratio:.0%})")

    # --- 1단계: 필수 포함/제외 분류 ---
    forced_keep: list[dict] = []
    forced_cut_ids: set[int] = set()
    candidates: list[dict] = []

    scene_count = len(scored_scenes)
    for i, scene in enumerate(scored_scenes):
        flags = set(scene.get("flags", []))
        has_speech = scene.get("has_speech", False)

        # 필수 제외: driving + 비말소리
        if "driving_cut" in flags and not has_speech:
            forced_cut_ids.add(scene["id"])
            continue

        # 필수 포함: 영상 시작/끝 장면만
        if i == 0 or i == scene_count - 1:
            forced_keep.append(scene)
            continue

        # 나머지 (speech 포함)는 모두 candidates
        candidates.append(scene)

    _log(
        f"분류: 필수KEEP {len(forced_keep)}개, "
        f"필수CUT {len(forced_cut_ids)}개, "
        f"후보 {len(candidates)}개"
    )

    # --- 2단계: 중복 제거 (candidates — speech 포함) ---
    candidates = _dedup_scenes(candidates)

    # --- 3단계: 예산 배분 ---
    forced_keep_seconds = sum(s["duration"] for s in forced_keep)
    remaining_budget = max(0.0, budget_seconds - forced_keep_seconds)
    _log(
        f"예산: 전체 {_format_duration(budget_seconds)}, "
        f"필수KEEP {_format_duration(forced_keep_seconds)}, "
        f"잔여 {_format_duration(remaining_budget)}"
    )

    candidates_sorted = sorted(candidates, key=lambda s: s.get("score", 0), reverse=True)
    budget_keep: list[dict] = []
    used_seconds = 0.0

    for scene in candidates_sorted:
        if used_seconds >= remaining_budget:
            break
        budget_keep.append(scene)
        used_seconds += scene["duration"]

    _log(
        f"예산 배분: {len(budget_keep)}개 선택 "
        f"({_format_duration(used_seconds)}), "
        f"{len(candidates) - len(budget_keep)}개 제거"
    )

    # --- 4단계: 합산 (ratio cap 미적용 — Claude가 편집 판단) ---
    all_keep = forced_keep + budget_keep

    # --- 5단계: keep_segments 생성 ---
    all_keep_sorted = sorted(all_keep, key=lambda s: s["start"])

    keep_segments = []
    seg_id = 0

    for scene in all_keep_sorted:
        wids = scene["window_ids"]
        duration = scene["duration"]
        has_speech = scene.get("has_speech", False)

        # 긴 비말소리 장면 PARTIAL: 60초+
        if duration >= LONG_SCENE_THRESHOLD and not has_speech:
            selected_wids = _select_keep_windows(scene, all_windows, max_windows=6)
            for wid in selected_wids:
                if 0 <= wid < len(all_windows):
                    w = all_windows[wid]
                    keep_segments.append({
                        "id": seg_id,
                        "globalStart": w["globalStart"],
                        "globalEnd": w["globalEnd"],
                        "label": scene.get("action", "unknown"),
                        "score": scene.get("score", 0),
                        "reason": "",
                        "decision": "partial",
                        "keep_windows": selected_wids,
                        "scene_id": scene["id"],
                        "scene_action": scene.get("action", "unknown"),
                        "window_id": wid,
                    })
                    seg_id += 1
        # 긴 말소리 장면 PARTIAL: 120초+
        elif has_speech and duration >= LONG_SPEECH_THRESHOLD:
            selected_wids = _select_keep_windows(scene, all_windows, max_windows=12)
            for wid in selected_wids:
                if 0 <= wid < len(all_windows):
                    w = all_windows[wid]
                    keep_segments.append({
                        "id": seg_id,
                        "globalStart": w["globalStart"],
                        "globalEnd": w["globalEnd"],
                        "label": scene.get("action", "unknown"),
                        "score": scene.get("score", 0),
                        "reason": "",
                        "decision": "partial",
                        "keep_windows": selected_wids,
                        "scene_id": scene["id"],
                        "scene_action": scene.get("action", "unknown"),
                        "window_id": wid,
                    })
                    seg_id += 1
        else:
            # 전체 KEEP
            for wid in wids:
                if 0 <= wid < len(all_windows):
                    w = all_windows[wid]
                    keep_segments.append({
                        "id": seg_id,
                        "globalStart": w["globalStart"],
                        "globalEnd": w["globalEnd"],
                        "label": scene.get("action", "unknown"),
                        "score": scene.get("score", 0),
                        "reason": "",
                        "decision": "keep",
                        "keep_windows": list(wids),
                        "scene_id": scene["id"],
                        "scene_action": scene.get("action", "unknown"),
                        "window_id": wid,
                    })
                    seg_id += 1

    total_keep_duration = sum(s["globalEnd"] - s["globalStart"] for s in keep_segments)
    _log(
        f"장면 선택 완료: {len(keep_segments)}개 세그먼트, "
        f"{_format_duration(total_keep_duration)} "
        f"(목표 {_format_duration(budget_seconds)})"
    )

    return keep_segments
