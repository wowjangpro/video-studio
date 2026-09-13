"""스토리 우선 편집 — 2단계 캐스팅(비트 → 초 단위 컷) + 3단계 조립(순서·예산)

1단계(story_planner)가 만든 스토리 설계를 실제 컷 목록으로 바꾼다.

캐스팅은 코드로 한다. 10초 윈도우 설명만으로는 LLM도 초 단위 위치를 알 수 없고,
그 대신 역할(role)이 길이와 위치를 결정한다.
  - montage  : 윈도우마다 2~4.5초씩, 윈도우 한가운데를 뜬다 (경계 2초 겹침 회피)
  - talk     : STT 문장 경계에 맞춘다. 문장 중간에서 자르지 않는다
  - payoff   : 구간의 끝에 붙인다 (완성·리액션은 끝에 온다)
  - 그 외    : 구간의 앞에서 target_seconds 만큼

조립은 스토리 순서다. 훅이 맨 앞, 막 안에서는 시간순(의도적 비선형 제외).
예산 초과는 비트 단위로 떨어뜨린다 — must_have 아닌 비트를 역할 우선순위가 낮은
것부터 통째로 빼지, 모든 컷을 조금씩 깎지 않는다(그렇게 깎으면 아크가 부서진다).

기존 후처리(merge_adjacent_segments, validate_segments)는 쓰지 않는다. 전자는 같은
씬 안 12초 이내 컷을 합쳐 몽타주를 없애고, 후자는 시간순 정렬로 훅을 뒤로 보낸다.
"""

from __future__ import annotations

import sys

from scene_detector import window_has_speech
from story_planner import plan_story_from_data, render_markdown

ROLE_KO = {
    "hook": "훅", "setup": "설정", "complication": "사건", "attempt": "시도",
    "payoff": "보상", "breather": "숨", "montage": "몽타주", "talk": "멘트",
}
# 예산 초과 시 이 순서로 뺀다 (앞일수록 먼저)
DROP_PRIORITY = ["breather", "montage", "setup", "attempt", "talk", "complication", "payoff"]

MONTAGE_MIN, MONTAGE_MAX = 3.0, 7.5
MONTAGE_TYPICAL = 5.5   # 몽타주 컷 수를 정할 때의 기준 길이
MONTAGE_SWING = 0.3     # 몽타주 안에서 긴 컷/짧은 컷을 번갈아 (촬영자 스타일: 중앙 6초, 5초 미만 39%)
SPAN_MIN = 3.5         # 연속 구간 컷 하나의 최소 길이
MIN_CUT = 2.0          # validate_segments 와 같은 하한
SEAM = 0.8             # 윈도우 경계에서 비켜 서는 여유
TOLERANCE = 1.07     # 목표의 7%까지 허용


def _log(msg: str):
    print(f"[story_edit] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 2단계 — 캐스팅
# ---------------------------------------------------------------------------

def _groups(wids: list[int]) -> list[list[int]]:
    """연속 윈도우 ID를 묶는다: [3,4,5,9,10] → [[3,4,5],[9,10]]"""
    out: list[list[int]] = []
    for w in sorted(set(wids)):
        if out and w == out[-1][-1] + 1:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def _mid_cut(w: dict, length: float) -> tuple[float, float]:
    dur = w["globalEnd"] - w["globalStart"]
    length = min(length, dur - 2 * SEAM)
    s = w["globalStart"] + (dur - length) / 2
    return s, s + length


def _cast_montage(wids: list[int], windows: list[dict], target: float) -> list[tuple[int, float, float]]:
    """target 초를 2~4.5초 컷 여러 개로. 윈도우가 남으면 균등 샘플링으로 줄인다."""
    if not wids:
        return []
    # 컷 수는 "3.5초짜리 몇 개"로 정한다. 2초 최소치로 채우면 죄다 2초 컷이 된다.
    k = max(1, min(len(wids), round(target / MONTAGE_TYPICAL)))
    if k < len(wids):
        step = len(wids) / k
        chosen = [wids[int(i * step)] for i in range(k)]
    else:
        chosen = list(wids)
    mean = max(MONTAGE_MIN, min(MONTAGE_MAX, target / len(chosen)))
    # 같은 길이로 나열하면 기계적으로 보인다. 긴 컷/짧은 컷을 번갈아 리듬을 만든다.
    out = []
    for i, w in enumerate(chosen):
        swing = 1 + MONTAGE_SWING if i % 2 == 0 else 1 - MONTAGE_SWING
        per = max(MONTAGE_MIN, min(MONTAGE_MAX, mean * swing))
        out.append((w, *_mid_cut(windows[w], per)))
    return out


def _cast_talk(wids: list[int], windows: list[dict], target: float,
               speech_segments: list[dict]) -> list[tuple[int, float, float]]:
    """발화는 문장 단위. STT 세그먼트가 걸치면 그 경계로 맞춘다."""
    out = []
    for g in _groups(wids):
        gs = windows[g[0]]["globalStart"]
        ge = windows[g[-1]]["globalEnd"]
        segs = [s for s in speech_segments if s["end"] > gs and s["start"] < ge]
        if segs:
            # 문장 경계 우선. 윈도우 끝에 걸친 문장은 2초까지 넘겨서 끝까지 살린다
            s = max(gs - 1.0, min(x["start"] for x in segs) - 0.4)
            e = min(ge + 2.0, max(x["end"] for x in segs) + 0.4)
            if e - s < MIN_CUT:
                e = s + MIN_CUT
        else:
            s = gs + SEAM
            e = min(ge - SEAM, s + target)
        if e - s >= MIN_CUT:
            out.append((g[0], s, e))
    return out


def _cast_span(wids: list[int], windows: list[dict], target: float,
               anchor_end: bool) -> list[tuple[int, float, float]]:
    """연속 구간마다 하나의 컷. 길이는 구간 길이에 비례해 나눈다."""
    groups = _groups(wids)
    if not groups:
        return []
    lens = [windows[g[-1]]["globalEnd"] - windows[g[0]]["globalStart"] for g in groups]
    # 구간이 많아 하나당 3초가 안 되면 긴 구간부터 남긴다 (시간순은 유지)
    max_groups = max(1, int(target // SPAN_MIN))
    if len(groups) > max_groups:
        keep = sorted(sorted(range(len(groups)), key=lambda i: -lens[i])[:max_groups])
        groups = [groups[i] for i in keep]
        lens = [lens[i] for i in keep]
    total_len = sum(lens) or 1.0
    out = []
    for g, ln in zip(groups, lens):
        alloc = max(SPAN_MIN, target * ln / total_len)
        gs = windows[g[0]]["globalStart"] + SEAM
        ge = windows[g[-1]]["globalEnd"] - SEAM
        alloc = min(alloc, ge - gs)
        if anchor_end:
            e, s = ge, ge - alloc
        else:
            s, e = gs, gs + alloc
        if e - s >= MIN_CUT:
            out.append((g[0], s, e))
    return out


def _cast_hook(wids: list[int], windows: list[dict], seconds: float,
               speech_segments: list[dict]) -> list[tuple[int, float, float]]:
    """훅은 설계가 준 순서대로. 발화 윈도우는 문장 경계, 나머지는 짧은 컷으로 나눈다."""
    talk = [w for w in wids if window_has_speech(windows[w])]
    visual = [w for w in wids if w not in talk]
    talk_cuts = {w: _cast_talk([w], windows, 4.0, speech_segments) for w in talk}
    used = sum(e - s for c in talk_cuts.values() for _, s, e in c)
    per = max(MONTAGE_MIN, min(MONTAGE_MAX, (seconds - used) / len(visual))) if visual else 0
    out = []
    for w in wids:
        if w in talk_cuts:
            out += talk_cuts[w]
        else:
            out.append((w, *_mid_cut(windows[w], per)))
    return out


def cast_beat(beat: dict, windows: list[dict], speech_segments: list[dict]) -> list[tuple[int, float, float]]:
    """비트 → [(대표 윈도우, 시작초, 끝초), ...]"""
    wids = [w for w in beat.get("evidence_windows", []) if windows[w].get("usable") != "no"]
    target = float(beat.get("target_seconds") or 0) or 6.0
    role = beat.get("role", "setup")
    if not wids or beat.get("evidence_grade") == "missing":
        return []
    if role == "montage":
        return _cast_montage(wids, windows, target)
    if role == "talk":
        return _cast_talk(wids, windows, target, speech_segments)
    return _cast_span(wids, windows, target, anchor_end=(role == "payoff"))


# ---------------------------------------------------------------------------
# 3단계 — 조립
# ---------------------------------------------------------------------------

def _beat_time(beat: dict, windows: list[dict]) -> float:
    """비트는 첫 컷이 나오는 순간에 시작한다 — 첫 윈도우 기준"""
    wids = beat.get("evidence_windows") or []
    if not wids:
        return 0.0
    return min(windows[w]["globalStart"] for w in wids)


def _order_beats(plan: dict, windows: list[dict]) -> list[tuple[str, dict]]:
    """[(막 제목, 비트)] — 막 순서 유지, 막 안은 시간순. nonlinear 비트는 설계 순서 자리 유지."""
    ordered = []
    for act in plan.get("acts", []):
        beats = act.get("beats", [])
        linear = sorted((b for b in beats if not b.get("nonlinear")), key=lambda b: _beat_time(b, windows))
        it = iter(linear)
        for b in beats:
            ordered.append((act.get("title", ""), b if b.get("nonlinear") else next(it)))
    return ordered


def _apply_budget(entries: list[dict], target_minutes: int,
                  windows_bounds: dict | None = None) -> list[dict]:
    """entries: [{beat, cuts, must_have, role, act}] — 비트 단위로 뺀다."""
    windows_bounds = windows_bounds or {}
    if target_minutes <= 0:
        return entries
    limit = target_minutes * 60 * TOLERANCE

    def total(es):
        return sum(e - s for x in es for _, s, e in x["cuts"])

    cur = total(entries)
    if cur <= limit:
        return entries

    _log(f"예산: {cur / 60:.1f}분 > 목표 {target_minutes}분(+7%) — 비트 단위로 축소")
    # 1) must_have 아닌 비트를 우선순위 낮은 역할부터. 막을 비우지는 않는다.
    for role in DROP_PRIORITY:
        for x in sorted((x for x in entries if x["role"] == role and not x["must_have"] and not x["hook"]),
                        key=lambda x: -sum(e - s for _, s, e in x["cuts"])):
            if cur <= limit:
                break
            same_act = [y for y in entries if y["act"] == x["act"] and y["cuts"]]
            if len(same_act) <= 1:
                continue
            dur = sum(e - s for _, s, e in x["cuts"])
            _log(f"  DROP {x['beat'].get('id')} [{role}] {dur:.0f}초 — {x['beat'].get('intent', '')[:50]}")
            x["cuts"] = []
            cur -= dur
        if cur <= limit:
            break
    # 2) 그래도 넘치면 몽타주 컷을 최소 길이로
    if cur > limit:
        for x in entries:
            if cur <= limit:
                break
            if x["role"] != "montage" or not x["cuts"]:
                continue
            new = []
            for w, s, e in x["cuts"]:
                # 줄이기만 한다. 이미 짧은 컷(꼬리 윈도우)은 그대로 두고, 윈도우 밖으로 나가지 않는다.
                ln = min(e - s, MONTAGE_MIN)
                mid = (s + e) / 2
                ws, we = windows_bounds.get(w, (s, e))
                ns, ne = max(ws, mid - ln / 2), min(we, mid + ln / 2)
                new.append((w, ns, ne))
            cur -= sum(e - s for _, s, e in x["cuts"]) - sum(e - s for _, s, e in new)
            x["cuts"] = new
        _log(f"  몽타주 컷 최소화 후 {cur / 60:.1f}분")
    if cur > limit:
        _log(f"  필수 비트만 남아 {cur / 60:.1f}분에서 중단 (목표 {target_minutes}분) — 아크 보호")
    return entries


def _make_segment(seg_id: int, w: dict, wid: int, s: float, e: float,
                  beat: dict, act: str, scene_of: dict) -> dict:
    role = beat.get("role", "")
    intent = (beat.get("intent") or "").strip()
    if len(intent) > 70:
        intent = intent[:69] + "…"
    reason = f"{ROLE_KO.get(role, role)} · {intent}"
    if beat.get("caption_hint"):
        reason += f" · 자막: {beat['caption_hint']}"
    scene = scene_of.get(wid, {})
    return {
        "id": seg_id,
        "globalStart": round(s, 3),
        "globalEnd": round(e, 3),
        "label": w.get("label", "unknown"),
        "score": w.get("s1_score", 0),
        "source": w.get("source", ""),
        "has_speech": window_has_speech(w),
        "desc": w.get("desc", ""),
        "reason": reason,
        "hint": f"story:{beat.get('id', '')}",   # format_srt_label 은 모르는 키를 무시한다
        "scene_id": scene.get("id"),
        "scene_action": scene.get("action", ""),
        "window_id": wid,
        "beat_id": beat.get("id"),
        "role": role,
        "act": act,
    }


def _clamp_cuts(cuts, windows: list[dict], files: list[dict]):
    """컷을 [0, 전체]와 해당 파일 [offset, offset+duration] 안으로. 멘트 컷은 문장을
    살리려고 윈도우 밖으로 나가는데, 파일 경계를 넘으면 다음 클립의 플래시 컷이 되고
    0 아래로 내려가면 SRT 타임스탬프가 음수가 된다."""
    bounds = [(f["offset"], f["offset"] + f["duration"]) for f in files]
    total = bounds[-1][1] if bounds else float("inf")
    out = []
    for wid, s, e in cuts:
        t = windows[wid]["globalStart"] + 0.01
        fs, fe = next(((a, b) for a, b in bounds if a <= t < b), (0.0, total))
        s2, e2 = max(0.0, fs, s), min(total, fe, e)
        if e2 - s2 >= MIN_CUT:
            out.append((wid, s2, e2))
    return out


def _validate_in_order(segments: list[dict]) -> list[dict]:
    """순서를 지키면서 겹침·2초 미만만 정리 (validate_segments 는 정렬을 해버린다).

    - 바로 앞 컷과 겹치면 앞 컷 끝에서 시작하도록 자른다 (멘트 +2초 확장 ↔ 다음 윈도우 컷)
    - 그 외 이미 낸 컷과 절반 넘게 겹치면 중복으로 버린다
      (훅이 쓴 윈도우를 막 안에서 다시 쓰는 '되짚기'는 절반 이하 겹침이라 살아남는다 — 의도)
    """
    out: list[dict] = []
    for seg in segments:
        if out:
            prev = out[-1]
            if prev["globalStart"] <= seg["globalStart"] < prev["globalEnd"]:
                # 직전 컷 안에서 시작 → 직전 컷 끝부터
                seg["globalStart"] = round(prev["globalEnd"], 3)
            elif seg["globalStart"] < prev["globalStart"] < seg["globalEnd"]:
                # 직전 컷을 통째로 품는 경우 → 직전 컷 앞부분만 남긴다 (같은 푸티지 연속 재생 방지)
                seg["globalEnd"] = round(prev["globalStart"], 3)
        if seg["globalEnd"] - seg["globalStart"] < MIN_CUT:
            continue
        dup = any(min(seg["globalEnd"], o["globalEnd"]) - max(seg["globalStart"], o["globalStart"])
                  > 0.5 * (seg["globalEnd"] - seg["globalStart"]) for o in out)
        if dup:
            continue
        seg["id"] = len(out)
        out.append(seg)
    return out


def assemble(plan: dict, windows: list[dict], scenes: list[dict],
             speech_segments: list[dict], target_minutes: int,
             files: list[dict] | None = None) -> list[dict]:
    scene_of = {}
    for sc in scenes:
        for wid in sc.get("window_ids", []):
            scene_of[wid] = sc

    entries = []
    hook = plan.get("hook") or {}
    if hook.get("window_ids"):
        hb = {"id": "HOOK", "role": "hook", "intent": hook.get("why", ""),
              "evidence_windows": hook["window_ids"], "target_seconds": hook.get("seconds", 5),
              "must_have": True}
        cuts = _cast_hook([w for w in hook["window_ids"] if windows[w].get("usable") != "no"],
                          windows, float(hook.get("seconds") or 5), speech_segments)
        cuts = _clamp_cuts(cuts, windows, files or [])
        entries.append({"beat": hb, "cuts": cuts, "must_have": True, "role": "hook", "act": "훅", "hook": True})

    for act_title, b in _order_beats(plan, windows):
        cuts = _clamp_cuts(cast_beat(b, windows, speech_segments), windows, files or [])
        entries.append({"beat": b, "cuts": cuts, "must_have": bool(b.get("must_have")),
                        "role": b.get("role", "setup"), "act": act_title, "hook": False})

    entries = _apply_budget(entries, target_minutes,
                            {i: (w["globalStart"], w["globalEnd"]) for i, w in enumerate(windows)})

    segments = []
    for x in entries:
        for wid, s, e in x["cuts"]:
            segments.append(_make_segment(len(segments), windows[wid], wid, s, e, x["beat"], x["act"], scene_of))
    segments = _validate_in_order(segments)

    total = sum(s["globalEnd"] - s["globalStart"] for s in segments)
    lens = sorted(s["globalEnd"] - s["globalStart"] for s in segments)
    if lens:
        _log(f"조립: 컷 {len(segments)}개, {total / 60:.1f}분, 평균 {total / len(lens):.1f}초, "
             f"중앙 {lens[len(lens) // 2]:.1f}초, 5초 미만 {sum(1 for l in lens if l < 5) * 100 // len(lens)}%")
    return segments


# ---------------------------------------------------------------------------
# 진입점 — analyze.py 가 호출
# ---------------------------------------------------------------------------

def run_story_editing(
    scenes: list[dict],
    windows: list[dict],
    total_duration: float,
    files: list[dict],
    speech_segments: list[dict],
    folder: str,
    synopsis: str,
    target_minutes: int,
    progress_callback=None,
    relook_dir: str | None = None,
) -> tuple[list[dict], dict, str]:
    """스토리 설계 → 캐스팅 → 조립. (세그먼트, 설계 JSON, 설계 마크다운) 반환"""
    if not synopsis or not synopsis.strip():
        raise RuntimeError(
            "스토리 편집에는 시놉시스가 필요합니다 — 편집 코멘트 칸에 그날 무슨 일이 "
            "있었는지 순서대로 적어주세요 (3~10줄)."
        )
    target = target_minutes if target_minutes > 0 else 20  # 0(자동)은 20분으로 — 설계·조립·표기 모두 같은 값
    if progress_callback:
        progress_callback("editing", 81, "스토리 설계 중 (Claude)...")
    plan = plan_story_from_data(
        windows, scenes, files, folder, synopsis, target,
        relook_dir=relook_dir, progress_callback=progress_callback,
    )
    if progress_callback:
        progress_callback("editing", 92, "비트를 컷으로 캐스팅 중...")
    segments = assemble(plan, windows, scenes, speech_segments or [], target, files=files)
    md = render_markdown(plan, windows, target)
    if progress_callback:
        progress_callback("editing", 95, f"스토리 편집 완료: {len(segments)}컷")
    return segments, plan, md
