"""구간 병합 및 검증

스토리보드 파이프라인에서는 LLM이 KEEP/CUT을 결정하므로
compress_repeated_segments는 더 이상 사용하지 않음.
대신 validate_segments로 기본 검증만 수행.
"""

from collections import Counter

from scene_detector import LABEL_KO

HINT_TAG = {"crop": "크롭", "insert": "인서트"}


def format_srt_label(segment: dict) -> str:
    """세그먼트의 라벨과 힌트를 SRT용 텍스트로 포맷

    예: [요리·크롭] 고기 완성 클로즈업
    """
    raw_label = segment.get("label", "unknown")
    label = LABEL_KO.get(raw_label, raw_label)
    hint = segment.get("hint", "")
    reason = segment.get("reason", "")

    tag = f"[{label}"
    if hint:
        # "crop:48,49" → "crop", "insert" → "insert"
        hint_key = hint.split(":")[0]
        if hint_key in HINT_TAG:
            tag += f"·{HINT_TAG[hint_key]}"
    tag += "]"

    if reason:
        return f"{tag} {reason}"
    return tag


def merge_adjacent_segments(
    segments: list[dict],
    gap_threshold: float = 3.0,
    min_duration: float = 2.0,
) -> list[dict]:
    """인접한 KEEP 세그먼트를 병합하고 최소 길이 필터링

    라벨 선택: 병합된 세그먼트들 중 가장 빈번한 라벨 사용 (다수결).
    동률 시 총 구간 길이가 긴 라벨 우선.
    """
    if not segments:
        return []

    sorted_segs = sorted(segments, key=lambda s: s["globalStart"])

    # 1단계: 인접 세그먼트 그룹화
    groups: list[list[dict]] = [[dict(sorted_segs[0])]]

    for seg in sorted_segs[1:]:
        last_group = groups[-1]
        last_seg = last_group[-1]
        # scene_id가 다르면 병합하지 않음 (장면 경계 보존)
        same_scene = (
            seg.get("scene_id") is None
            or last_seg.get("scene_id") is None
            or seg["scene_id"] == last_seg["scene_id"]
        )
        if same_scene and seg["globalStart"] - last_seg["globalEnd"] <= gap_threshold:
            last_group.append(dict(seg))
        else:
            groups.append([dict(seg)])

    # 2단계: 그룹별 병합 + 다수결 라벨 선택
    merged = []
    for group in groups:
        result = dict(group[0])
        result["globalEnd"] = max(s["globalEnd"] for s in group)

        action_labels = [
            s["label"] for s in group
            if s["label"] not in ("unknown", "")
        ]
        if action_labels:
            label_counts = Counter(action_labels)
            label_durations: dict[str, float] = {}
            for s in group:
                lbl = s["label"]
                if lbl in label_counts:
                    label_durations[lbl] = label_durations.get(lbl, 0) + (s["globalEnd"] - s["globalStart"])
            best_label = max(
                label_counts,
                key=lambda lbl: (label_counts[lbl], label_durations.get(lbl, 0)),
            )
            result["label"] = best_label
            best_score = max(
                (s.get("score", 0) for s in group if s["label"] == best_label and isinstance(s.get("score", 0), (int, float))),
                default=0,
            )
            result["score"] = best_score

        # reason: 첫 번째 비어있지 않은 reason 사용
        for s in group:
            if s.get("reason"):
                result["reason"] = s["reason"]
                break

        # hint: 그룹 내 첫 번째 비어있지 않은 hint 사용 (crop/insert)
        for s in group:
            if s.get("hint"):
                result["hint"] = s["hint"]
                break

        merged.append(result)

    return [s for s in merged if (s["globalEnd"] - s["globalStart"]) >= min_duration]


def validate_segments(segments: list[dict]) -> list[dict]:
    """LLM 편집 결과의 기본 검증

    - 2초 미만 구간 제거
    - 시간순 정렬
    - 겹치는 구간 병합
    """
    if not segments:
        return []

    # 시간순 정렬
    sorted_segs = sorted(segments, key=lambda s: s["globalStart"])

    # 겹침 해소: 이전 구간의 끝이 다음 구간 시작보다 뒤이면 확장
    # scene_id가 다르면 겹쳐도 병합하지 않음
    result = [dict(sorted_segs[0])]
    for seg in sorted_segs[1:]:
        prev = result[-1]
        same_scene = (
            seg.get("scene_id") is None
            or prev.get("scene_id") is None
            or seg["scene_id"] == prev["scene_id"]
        )
        if same_scene and seg["globalStart"] <= prev["globalEnd"]:
            # 같은 장면: 병합
            prev["globalEnd"] = max(prev["globalEnd"], seg["globalEnd"])
            if not prev.get("reason") and seg.get("reason"):
                prev["reason"] = seg["reason"]
            if not prev.get("hint") and seg.get("hint"):
                prev["hint"] = seg["hint"]
        else:
            # 다른 장면인데 겹치면 이전 구간 끝을 잘라서 겹침 해소
            if seg["globalStart"] < prev["globalEnd"]:
                prev["globalEnd"] = seg["globalStart"]
            result.append(dict(seg))

    # 2초 미만 제거
    result = [s for s in result if (s["globalEnd"] - s["globalStart"]) >= 2.0]

    return result
