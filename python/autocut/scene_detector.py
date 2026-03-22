"""장면 기반 구조화 — 교차검증 + 장면 그룹핑 + NG 필터 + 내러티브 스토리보드

10초 윈도우 단위 데이터를 자연스러운 장면(Scene) 단위로 재구성하고,
코드 기반으로 NG를 판정하여 LLM 입력을 최적화한다.
"""

import sys
from collections import Counter

LABEL_KO = {
    "cooking": "요리",
    "eating": "식사",
    "fire_tending": "불멍",
    "setting_up": "설치",
    "showing_gear": "장비소개",
    "walking": "이동",
    "driving": "운전",
    "resting": "휴식",
    "scenery": "풍경",
    "dark": "어두움",
    "talking": "토크",
    "unknown": "미분류",
    "no_frame": "프레임없음",
    "start": "시작",
}


# 아웃도어 이동 키워드 — walking 장면이 "콘텐츠 이동"인지 "단순 이동"인지 판별
# 이 키워드가 장면 설명에 포함되면 KEEP/PARTIAL 보호 대상
TRAIL_KEYWORDS = (
    # 산악/등산
    "산길", "숲길", "계곡", "능선", "등산", "트레일", "오르막", "내리막",
    "하이킹", "트래킹", "릿지", "정상", "봉우리", "고개",
    "돌길", "산을", "숲을", "숲속", "산속", "등산로",
    # 해안/섬
    "해변", "바닷가", "해안", "갯벌", "섬", "선착장", "포구", "해안길",
    # 걷기길/둘레길
    "둘레길", "올레길", "해파랑", "종주",
    # 백패킹/캠핑 이동
    "백패킹", "야영", "캠핑장", "야영장", "텐트", "타프",
    # 영어
    "trail", "hike", "hiking", "mountain", "forest", "ridge", "summit", "peak",
    "beach", "island", "coast", "backpacking", "camping",
)


def window_has_speech(w: dict) -> bool:
    """윈도우에 말소리가 있는지 판별 (VAD + Stage 2 교차 확인)"""
    return w.get("has_speech", False) or w.get("source", "") in ("vad", "vad+vision")


def _log(msg: str):
    print(f"[scene_detector] {msg}", file=sys.stderr, flush=True)


def _format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}시간 {m}분 {s}초"
    if m > 0:
        return f"{m}분 {s}초"
    return f"{s}초"


def _motion_level(motion: float) -> str:
    if motion < 0.01:
        return "정적"
    if motion < 0.03:
        return "저"
    if motion < 0.08:
        return "중"
    return "고"


# ---------------------------------------------------------------------------
# 1. 교차검증
# ---------------------------------------------------------------------------

def cross_validate_window(w: dict) -> dict:
    """Stage 1 + VAD + Stage 2 교차검증으로 오류 보정

    - 규칙 1: VAD=음성 + 비전=resting/scenery/dark → talking으로 보정
    - 규칙 2: 비전=usable:no + Stage1=높은점수(>=3) → marginal로 완화
    - 규칙 3: 비전=unknown + VAD=음성 → talking으로 보정
    """
    has_speech = window_has_speech(w)

    # 규칙 1: 음성이 있는데 비전이 비활동으로 판정한 경우
    if has_speech and w.get("label") in ("resting", "scenery", "dark"):
        original_label = w["label"]
        w["label"] = "talking"
        w["desc"] = f"(음성감지) {w.get('desc', '')}"
        w["_cv_rule1"] = original_label

    # 규칙 2: 비전이 NG인데 Stage 1 점수가 높은 경우
    if w.get("usable") == "no" and w.get("s1_score", 0) >= 3:
        w["usable"] = "marginal"
        w["usable_reason"] = f"(S1교차검증) {w.get('usable_reason', '')}"
        w["_cv_rule2"] = True

    # 규칙 3: 비전이 미분류인데 음성이 있는 경우
    if has_speech and w.get("label") == "unknown":
        w["label"] = "talking"
        w["_cv_rule3"] = True

    return w


def cross_validate_all(windows: list[dict]) -> dict:
    """모든 윈도우에 교차검증 적용, 보정 통계 반환"""
    stats = {"rule1": 0, "rule2": 0, "rule3": 0}
    for w in windows:
        cross_validate_window(w)
        if "_cv_rule1" in w:
            stats["rule1"] += 1
        if w.get("_cv_rule2"):
            stats["rule2"] += 1
        if w.get("_cv_rule3"):
            stats["rule3"] += 1

    total = stats["rule1"] + stats["rule2"] + stats["rule3"]
    _log(
        f"교차검증: {total}개 보정 "
        f"(resting→talking {stats['rule1']}, "
        f"usable:no→marginal {stats['rule2']}, "
        f"unknown→talking {stats['rule3']})"
    )
    return stats


# ---------------------------------------------------------------------------
# 2. 장면 그룹핑
# ---------------------------------------------------------------------------

def _should_merge(w_cur: dict, w_next: dict) -> bool:
    """두 인접 윈도우가 같은 장면에 속하는지 판단"""
    # 같은 action 라벨
    if w_cur.get("label") == w_next.get("label"):
        return True

    # 둘 다 말소리가 있으면 같은 장면 (활동이 달라도 토크 연속)
    cur_speech = window_has_speech(w_cur)
    next_speech = window_has_speech(w_next)
    if cur_speech and next_speech:
        return True

    return False


def group_windows_to_scenes(windows: list[dict]) -> list[dict]:
    """연속된 관련 윈도우를 장면(Scene)으로 그룹핑

    그룹핑 기준:
    1. 같은 action 라벨 연속 → 같은 장면
    2. 둘 다 has_speech=True → 같은 장면 (활동이 달라도)
    3. 1개의 다른 라벨 윈도우가 끼어있어도 앞뒤가 같으면 bridge → 같은 장면
    """
    if not windows:
        return []

    # 1단계: 기본 그룹핑 (인접 병합)
    groups: list[list[int]] = [[0]]  # 윈도우 인덱스 그룹
    for i in range(1, len(windows)):
        if _should_merge(windows[groups[-1][-1]], windows[i]):
            groups[-1].append(i)
        else:
            groups.append([i])

    # 2단계: bridge tolerance — 1개 다른 라벨이 끼어있으면 앞뒤와 합침
    merged_groups: list[list[int]] = []
    i = 0
    while i < len(groups):
        cur = groups[i]

        # 현재 그룹이 1개짜리이고, 앞뒤 그룹이 같은 라벨이면 bridge
        if (
            i > 0
            and i + 1 < len(groups)
            and len(cur) == 1
            and merged_groups
        ):
            prev_label = windows[merged_groups[-1][-1]].get("label")
            next_label = windows[groups[i + 1][0]].get("label")
            if prev_label == next_label:
                # bridge: 현재 1개짜리를 이전 그룹에 합치고, 다음 그룹도 합침
                merged_groups[-1].extend(cur)
                merged_groups[-1].extend(groups[i + 1])
                i += 2
                continue

        if merged_groups and _should_merge(windows[merged_groups[-1][-1]], windows[cur[0]]):
            merged_groups[-1].extend(cur)
        else:
            merged_groups.append(list(cur))
        i += 1

    # 3단계: 장면 데이터 구조 생성
    scenes = []
    for scene_idx, win_indices in enumerate(merged_groups):
        scene_windows = [windows[idx] for idx in win_indices]

        # 주요 활동: 가장 많은 라벨
        label_counts = Counter(w.get("label", "unknown") for w in scene_windows)
        action = label_counts.most_common(1)[0][0]

        # 말소리 비율
        speech_count = sum(1 for w in scene_windows if window_has_speech(w))
        speech_ratio = speech_count / len(scene_windows) if scene_windows else 0.0

        # 모션 평균
        motions = [w.get("motion", 0) for w in scene_windows]
        avg_motion = sum(motions) / len(motions) if motions else 0.0

        # 설명 수집 (중복 제거, 순서 유지)
        descs = []
        seen_descs = set()
        for w in scene_windows:
            d = w.get("desc", "")
            if d and d not in seen_descs and d != "(설명 없음)" and d != "(음성 감지)":
                # 교차검증 접두사 제거하여 깨끗한 설명 추출
                clean_desc = d.replace("(음성감지) ", "")
                if clean_desc and clean_desc not in seen_descs:
                    descs.append(clean_desc)
                    seen_descs.add(clean_desc)

        # 대사 수집 (중복 제거, 40자 제한, 최대 5개)
        transcripts = []
        seen_texts = set()
        for w in scene_windows:
            t = w.get("transcript", "")
            if t:
                for part in t.split(" / "):
                    part = part.strip()
                    truncated = part[:40]
                    if truncated and truncated not in seen_texts:
                        transcripts.append(truncated)
                        seen_texts.add(truncated)

        start = scene_windows[0].get("globalStart", 0.0)
        end = scene_windows[-1].get("globalEnd", 0.0)

        # 파일 인덱스: 첫 윈도우의 fileIndex (장면이 걸치면 첫 파일 기준)
        file_index = scene_windows[0].get("fileIndex", -1)

        scenes.append({
            "id": scene_idx + 1,
            "start": start,
            "end": end,
            "duration": end - start,
            "action": action,
            "has_speech": speech_count > 0,
            "speech_ratio": round(speech_ratio, 2),
            "window_ids": list(win_indices),
            "descs": descs,
            "transcripts": transcripts[:5],
            "avg_motion": avg_motion,
            "motion_level": _motion_level(avg_motion),
            "is_ng": False,
            "file_index": file_index,
        })

    _log(f"장면 그룹핑: {len(windows)}개 윈도우 → {len(scenes)}개 장면")
    return scenes


# ---------------------------------------------------------------------------
# 3. NG 필터
# ---------------------------------------------------------------------------

def _scene_is_ng(scene: dict, windows: list[dict]) -> bool:
    """장면이 NG인지 판정"""
    scene_windows = [windows[idx] for idx in scene["window_ids"]]

    # NG 보호: 말소리 있으면 NG 아님
    if scene["has_speech"]:
        return False

    # NG 보호: usable=yes가 50% 이상이면 NG 아님
    yes_count = sum(1 for w in scene_windows if w.get("usable") == "yes")
    if yes_count / max(len(scene_windows), 1) >= 0.5:
        return False

    # NG 조건 1: 모든 윈도우 usable=no
    all_unusable = all(w.get("usable") == "no" for w in scene_windows)
    if all_unusable:
        return True

    # NG 조건 2: 완전 암전 (모든 윈도우 brightness < 0.03)
    all_dark = all(w.get("brightness", 0.5) < 0.03 for w in scene_windows)
    if all_dark:
        return True

    # NG 조건 3: 방치된 카메라 (말소리 없음 + 모션 없음 + 30초 이상)
    if (
        scene["duration"] >= 30
        and not scene["has_speech"]
        and scene["avg_motion"] < 0.005
    ):
        # 추가 확인: s1_filtered 소스도 체크
        all_static = all(
            w.get("motion", 0) < 0.01 and w.get("source") != "vad+vision"
            for w in scene_windows
        )
        if all_static:
            return True

    return False


def filter_ng_scenes(scenes: list[dict], windows: list[dict]) -> list[dict]:
    """코드 기반 NG 판정 — LLM 호출 불필요

    NG 조건:
    - 모든 윈도우 usable=no
    - 완전 암전 (brightness < 0.03)
    - 말소리 없음 + 모션 없음 + 30초 이상 (방치된 카메라)

    NG 보호:
    - has_speech 윈도우가 하나라도 있으면 NG 아님
    - usable=yes가 50% 이상이면 NG 아님
    """
    ng_count = 0
    ng_duration = 0.0
    for scene in scenes:
        if _scene_is_ng(scene, windows):
            scene["is_ng"] = True
            ng_count += 1
            ng_duration += scene["duration"]

    usable_scenes = [s for s in scenes if not s["is_ng"]]

    _log(
        f"NG 필터: {ng_count}개 NG 장면 제거 "
        f"({_format_duration(ng_duration)}), "
        f"{len(usable_scenes)}개 장면 유지"
    )
    return usable_scenes


# ---------------------------------------------------------------------------
# 4. 내러티브 스토리보드 생성
# ---------------------------------------------------------------------------

def generate_narrative_storyboard(
    scenes: list[dict],
    total_duration: float,
) -> str:
    """장면 단위 내러티브 스토리보드 텍스트 생성

    포맷:
    [S01] 00:00-02:30 (2분30초) 설치 | ★말소리 45% | 모션: 중
      텐트 자리 잡기 → 텐트 펼치기 → 펙 박기
      [윈도우: 0~14]
    """
    # 장면 커버리지 통계
    scene_coverage = sum(s["duration"] for s in scenes)

    lines = [
        "=== 아웃도어 브이로그 스토리보드 ===",
        f"전체: {_format_duration(total_duration)}, "
        f"장면 커버리지: {_format_duration(scene_coverage)}, "
        f"{len(scenes)}개 장면",
        "",
    ]

    for scene in scenes:
        sid = scene["id"]
        t_start = _format_time(scene["start"])
        t_end = _format_time(scene["end"])
        dur_str = _format_duration(scene["duration"])
        action_ko = LABEL_KO.get(scene["action"], scene["action"])

        # 말소리 표시
        speech_part = ""
        if scene["has_speech"]:
            pct = int(scene["speech_ratio"] * 100)
            speech_part = f" | ★말소리 {pct}%"

        motion_part = f" | 모션: {scene['motion_level']}"

        header = f"[S{sid:02d}] {t_start}-{t_end} ({dur_str}) {action_ko}{speech_part}{motion_part}"
        lines.append(header)

        # 활동 진행 (→ 연결)
        if scene["descs"]:
            desc_text = " → ".join(scene["descs"][:5])
            if len(scene["descs"]) > 5:
                desc_text += " …"
            lines.append(f"  {desc_text}")
        else:
            lines.append(f"  (설명 없음)")

        # 대사 (있을 때만)
        scene_transcripts = scene.get("transcripts", [])
        if scene_transcripts:
            quoted = " / ".join(f'"{t}"' for t in scene_transcripts[:3])
            lines.append(f"  대사: {quoted}")

        # 윈도우 범위
        wids = scene["window_ids"]
        if len(wids) == 1:
            lines.append(f"  [윈도우: {wids[0]}]")
        else:
            lines.append(f"  [윈도우: {wids[0]}~{wids[-1]}]")

        lines.append("")

    storyboard = "\n".join(lines)
    _log(
        f"스토리보드 생성: {len(scenes)}개 장면, "
        f"{len(storyboard)}자"
    )
    return storyboard


def generate_compact_storyboard(
    scenes: list[dict],
    total_duration: float,
) -> str:
    """장면 단위 요약 스토리보드 — 장면당 1줄

    포맷:
    [S01] 00:00-02:30 (2m30s) 설치 ★45% M:중 W:0~14 | 텐트 자리→펼치기→펙 박기 💬"이거 맛있어"
    """
    scene_coverage = sum(s["duration"] for s in scenes)

    # 파일 수 파악
    file_indices = set(s.get("file_index", -1) for s in scenes)
    file_count = len([f for f in file_indices if f >= 0]) or 1

    lines = [
        f"=== 아웃도어 브이로그 요약 스토리보드 ({len(scenes)}개 장면, "
        f"{file_count}개 클립, "
        f"전체 {_format_duration(total_duration)}, "
        f"커버리지 {_format_duration(scene_coverage)}) ===",
        "",
    ]

    prev_file_index = -1

    for scene in scenes:
        # 파일 경계 구분자 — 클립이 바뀔 때 표시
        fi = scene.get("file_index", -1)
        if fi >= 0 and fi != prev_file_index:
            if prev_file_index >= 0:
                lines.append("")
            lines.append(f"--- 클립 #{fi + 1} ---")
            prev_file_index = fi

        sid = scene["id"]
        t_start = _format_time(scene["start"])
        t_end = _format_time(scene["end"])
        dur_sec = scene["duration"]
        dur_m = int(dur_sec // 60)
        dur_s = int(dur_sec % 60)
        dur_str = f"{dur_m}m{dur_s:02d}s" if dur_m > 0 else f"{dur_s}s"
        action_ko = LABEL_KO.get(scene["action"], scene["action"])

        # 말소리
        speech_part = ""
        if scene["has_speech"]:
            pct = int(scene["speech_ratio"] * 100)
            speech_part = f" ★{pct}%"

        # 모션
        motion_part = f" M:{scene['motion_level']}"

        # 윈도우 범위
        wids = scene["window_ids"]
        if len(wids) == 1:
            w_part = f" W:{wids[0]}"
        else:
            w_part = f" W:{wids[0]}~{wids[-1]}"

        # 설명 (최대 3개)
        descs = scene.get("descs", [])
        desc_text = "→".join(descs[:3]) if descs else "(설명 없음)"

        # 대사 (1개, 30자)
        transcripts = scene.get("transcripts", [])
        talk_part = ""
        if transcripts:
            t = transcripts[0][:30]
            talk_part = f' 💬"{t}"'

        line = (
            f"[S{sid:02d}] {t_start}-{t_end} ({dur_str}) "
            f"{action_ko}{speech_part}{motion_part}{w_part} | {desc_text}{talk_part}"
        )
        lines.append(line)

    compact = "\n".join(lines)
    _log(f"요약 스토리보드 생성: {len(scenes)}개 장면, {len(compact)}자")
    return compact


# ---------------------------------------------------------------------------
# 5. 품질 로그
# ---------------------------------------------------------------------------

def log_quality_summary(
    windows: list[dict],
    scenes: list[dict],
    cv_stats: dict,
):
    """각 단계의 품질 지표를 stderr로 출력"""
    total = len(windows)
    if total == 0:
        return

    # Stage 1 통계
    s1_filtered = sum(1 for w in windows if w.get("source") == "s1_filtered")
    avg_score = sum(w.get("s1_score", 0) for w in windows) / total
    dark_count = sum(1 for w in windows if w.get("brightness", 0.5) < 0.03)
    _log(f"[quality] Stage 1: {total}개 윈도우, 평균 점수 {avg_score:.1f}, 암전 {dark_count}개, S1필터 {s1_filtered}개")

    # VAD 통계
    speech_windows = sum(1 for w in windows if window_has_speech(w))
    speech_duration = sum(
        w.get("globalEnd", 0) - w.get("globalStart", 0)
        for w in windows
        if window_has_speech(w)
    )
    _log(
        f"[quality] VAD: 음성 윈도우 {speech_windows}개 "
        f"(전체의 {speech_windows * 100 // max(total, 1)}%), "
        f"총 {speech_duration / 60:.1f}분"
    )

    # Stage 2 통계
    vision_count = sum(1 for w in windows if w.get("source") in ("vision", "vad+vision"))
    unknown_count = sum(1 for w in windows if w.get("label") == "unknown")
    unusable_count = sum(1 for w in windows if w.get("usable") == "no")
    _log(
        f"[quality] Stage 2: 비전 태깅 {vision_count}개, "
        f"unknown {unknown_count}개, usable:no {unusable_count}개"
    )

    # 교차검증 통계
    _log(
        f"[quality] 교차검증: "
        f"resting→talking {cv_stats.get('rule1', 0)}개, "
        f"usable:no→marginal {cv_stats.get('rule2', 0)}개, "
        f"unknown→talking {cv_stats.get('rule3', 0)}개"
    )

    # 장면 통계
    ng_count = sum(1 for s in scenes if s.get("is_ng"))
    usable_count = len(scenes) - ng_count
    _log(
        f"[quality] 장면: 총 {len(scenes)}개, "
        f"NG {ng_count}개 제거, {usable_count}개 유지"
    )
