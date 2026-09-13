"""스토리 우선 편집 — 1단계: 스토리 설계 (프로토타입)

기존 파이프라인은 "이 씬을 남길까"를 148번 묻는다. 그 결과는 행동 목록이지
이야기가 아니다. 이 모듈은 순서를 뒤집는다.

  1. 촬영자의 시놉시스를 1급 입력으로 받는다. 기계는 "무슨 일이 있었나"를 알 수
     없고(비전 설명은 그럴듯한 행동을 쓰지 뜻밖의 행동은 못 쓴다 — 어댑터를
     "수동 에어펌프"로, 입으로 부는 장면을 "풍경"으로 적었다), 잘하는 건
     "그게 어디 있나"를 1000여 개 윈도우에서 찾는 일이다.
  2. 비트(beat)마다 근거 윈도우를 반드시 댄다. 근거 없는 비트는 만들지 않는다.
  3. 시놉시스와 설명이 어긋나는 비트는 프레임을 다시 뽑아 비전에 *질문형*으로
     묻는다(표적 재관찰). 전체 재태깅이 아니라 비트당 몇 장이다.

출력은 스토리 설계 JSON + 사람이 읽을 마크다운. 2단계(캐스팅: 비트 → 초 단위
in/out)와 3단계(조립·예산)는 이 설계가 검토된 뒤에 붙인다.

실행:
  shared-venv/bin/python3 story_planner.py --folder /path/to/source \\
      --synopsis /path/to/source/autocut/synopsis.md --target-minutes 20 --name 20260503_story
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from claude_client import call_claude_text, call_claude_vision
from scene_detector import LABEL_KO


def _log(msg: str):
    print(f"[story] {msg}", file=sys.stderr, flush=True)


def _fmt(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


# ---------------------------------------------------------------------------
# 입력 인덱스 — 윈도우(근거 단위) + 씬(구조 단위)
# ---------------------------------------------------------------------------

def build_window_index(windows: list[dict]) -> str:
    """윈도우 1개 = 1줄. 비트의 근거는 이 ID로 댄다."""
    lines = []
    for i, w in enumerate(windows):
        label = LABEL_KO.get(w.get("label", ""), w.get("label", "?"))
        shot = {"wide": "와이드", "closeup": "클로즈업"}.get(w.get("shot", ""), "")
        flag = " ✗" if w.get("usable") == "no" else ""
        line = f"W{i} {_fmt(w['globalStart'])} [{label}{'/' + shot if shot else ''}]{flag} {w.get('desc', '').strip()}"
        if w.get("transcript"):
            line += f' 💬"{w["transcript"].strip()}"'
        lines.append(line)
    return "\n".join(lines)


def build_scene_index(scenes: list[dict]) -> str:
    """씬 1개 = 1줄. 같은 행동이 이어지는 구간의 경계를 보여준다(설명은 생략)."""
    lines = []
    prev_file = -1
    for s in scenes:
        fi = s.get("file_index", -1)
        if fi >= 0 and fi != prev_file:
            lines.append(f"--- 클립 #{fi + 1} ---")
            prev_file = fi
        wids = s.get("window_ids", [])
        wr = f"W{wids[0]}~W{wids[-1]}" if wids else "-"
        sp = f" ★{int(s.get('speech_ratio', 0) * 100)}%" if s.get("has_speech") else ""
        lines.append(
            f"[S{s['id']:02d}] {_fmt(s['start'])}-{_fmt(s['end'])} "
            f"{LABEL_KO.get(s['action'], s['action'])} {wr}{sp}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1단계 프롬프트
# ---------------------------------------------------------------------------

STORY_PROMPT = """당신은 캠핑 브이로그의 스토리 편집자입니다. 지금은 컷을 고르는 단계가 아니라
**이 영상이 어떤 이야기인지 설계하는 단계**입니다.

## 입력이 무엇인지 정확히 이해하세요

### 1) 촬영자 시놉시스 — 사실입니다
그날 실제로 일어난 일입니다. 아래 설명과 어긋나면 **시놉시스가 맞습니다.**

__SYNOPSIS__

### 2) 윈도우 설명 — 비전 모델이 쓴 것이라 "그럴듯한 오류"가 있습니다
10초 단위 화면을 AI가 본 설명입니다. 일어날 법한 행동은 잘 쓰지만 **뜻밖의 행동은
못 씁니다.** 예를 들어 손바닥만 한 어댑터를 밸브에 대고 입으로 부는 장면을
"수동 에어펌프로 공기 주입"이라고 적고, 사람이 천 아래로 들어가 불어서 텐트가
혼자 솟아오르는 장면을 "풍경"이라고 적습니다. 설명은 **단서**이지 사실이 아닙니다.
💬는 실제 발화(STT)이며 설명보다 신뢰도가 높습니다.

### 3) 당신의 역할
시놉시스의 각 사건이 **화면 어디에 있는지** 찾고, 그것들을 시청자가 보고 싶은
순서와 리듬으로 배열하는 것입니다. 시놉시스에 없는 사건을 지어내지 마세요.
반대로 시놉시스에 있는데 화면에서 못 찾으면 **못 찾았다고 표시**하세요(missing).
그 비트는 만들지 않습니다.

## 설계 원칙

- **훅(첫 3~8초)**: 시간순일 필요 없습니다. "왜?"가 생기는 장면을 앞으로 당기세요.
- **비트 역할**: hook / setup / complication(사건·문제) / attempt(시도) / payoff(해결·보상)
  / breather(숨 고르기·풍경·정서) / montage(짧은 컷 여러 개) / talk(시청자 멘트).
- **근거**: 비트마다 evidence_windows(윈도우 ID 목록)를 반드시 댑니다.
  - direct: 설명·발화가 사건을 그대로 보여줌
  - indirect: 결과나 정황만 보임(예: 사람이 사라지고 텐트가 혼자 부푼다). 자막 한 줄이면 살아남.
  - missing: 화면에 없음. must_have여도 만들지 않음.
- **충돌 표시**: 시놉시스와 설명이 어긋나는 비트는 conflict에 무엇이 어긋나는지 쓰고,
  verify_windows(재관찰할 윈도우 ≤3개)와 verify_question(그 프레임을 보고 답할 수 있는
  구체적 질문)을 적으세요. 예: "이 사람이 밸브에 입을 대고 있는가? 손에 든 것은
  펌프인가 작은 어댑터인가?"
- **순서**: 막 안에서는 시간순으로 배열하세요. 훅처럼 일부러 순서를 깨는 비트만
  "nonlinear": true 로 표시하면 그 자리를 지킵니다(나머지는 시간순으로 정렬됩니다).
- **montage 비트는 후속 단계에서 윈도우마다 2~4초 컷으로 쪼개집니다.** 그러니
  evidence_windows 에 시각적으로 서로 다른 3~7개 윈도우를 고르세요(같은 앵글 반복 X).
- **화각을 섞으세요.** 윈도우 라벨 뒤의 와이드/클로즈업이 화각입니다. 같은 행동을 보여주는
  윈도우가 여럿이면 와이드와 클로즈업이 번갈아 오게 고르세요. 같은 화각·같은 자리의
  윈도우가 3개 넘게 연달아 오는 비트는 지루합니다.
- **길이**: 촬영자의 실제 편집 스타일은 **평균 9초, 중앙값 6초, 5초 미만 컷이 39%**입니다.
  target_seconds는 역할에 맞게 — hook 3~8, montage 컷당 2~4, complication/attempt 6~15,
  payoff 10~25, talk는 문장이 끝나는 길이. 10초 윈도우 격자에 맞추지 마세요.
- **자막(caption_hint)**: 화면에 얹을 문구 그대로 씁니다. "(못 찍음)" 같은 편집자
  메모는 conflict 에 쓰고 자막에는 넣지 마세요.
- **버리기**: 이야기에 기여하지 않는 활동은 통째로 버립니다. "촬영한 영상을 다
  쓴다"는 목표가 아닙니다. 무엇을 왜 버렸는지 dropped에 적으세요.
- **총 길이**: 완성본 약 __TARGET_MIN__분. 비트 target_seconds의 합이 이 근처가 되게.

## 출력 — JSON만, 다른 텍스트 없이

```json
{
  "logline": "한 줄. 이 영상은 무엇에 관한 이야기인가",
  "hook": {"window_ids": [..], "seconds": 5, "why": "왜 이걸로 여는가"},
  "acts": [
    {
      "title": "막 제목",
      "beats": [
        {
          "id": "A1-1",
          "role": "complication",
          "intent": "이 비트가 시청자에게 주는 것 (한 문장)",
          "synopsis_ref": "시놉시스 몇 번 항목인지",
          "evidence_windows": [53, 54],
          "evidence_grade": "direct",
          "conflict": null,
          "verify_windows": [],
          "verify_question": null,
          "must_have": true,
          "nonlinear": false,
          "target_seconds": 8,
          "caption_hint": "시청자에게 보일 자막 문구만 (편집자 메모 X)"
        }
      ]
    }
  ],
  "threads": [{"name": "되짚는 실 (예: 펌프)", "beat_ids": ["A1-1", "A1-4"]}],
  "dropped": [{"what": "무엇을", "why": "왜 버리나"}]
}
```

## 씬 인덱스 (같은 행동이 이어지는 구간 — 구조 참고용)

__SCENE_INDEX__

## 윈도우 인덱스 (근거 단위 — 비트는 반드시 이 ID를 인용)

__WINDOW_INDEX__
"""


RELOOK_PROMPT = """이 프레임들은 캠핑 영상의 같은 순간을 몇 초 간격으로 뽑은 것입니다.
AI가 쓴 설명과 촬영자의 진술이 어긋나서 직접 확인하려는 것입니다.

- AI 설명: __DESC__
- 촬영자 진술: __CLAIM__
- 확인할 질문: __QUESTION__

첫 줄은 반드시 다음 셋 중 하나로 시작하세요.
판정: 확인   (촬영자 진술이 프레임과 부합)
판정: 부정   (프레임이 촬영자 진술과 명백히 다름)
판정: 불확실 (프레임만으로는 알 수 없음)

그다음 2~3문장으로 **실제로 보이는 것**을 구체적으로 쓰세요 — 손에 든 물건의 크기와
형태, 사람의 자세와 위치, 화면에 사람이 있는지 없는지. 추측은 추측이라고 쓰세요.
"""


# ---------------------------------------------------------------------------
# 응답 파싱·검증
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """응답에서 첫 번째 JSON 객체를 꺼낸다(코드펜스·앞뒤 산문 허용).

    중괄호를 세는 방식은 문자열 안의 '{' '}'(자막·의도 문구)에 깨진다.
    JSONDecoder.raw_decode 는 문자열을 이해하므로 '{' 위치마다 시도한다.
    """
    text = re.sub(r"```(?:json)?", "", text)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "acts" in obj:
            return obj
    return None


def validate_plan(plan: dict, windows: list[dict]) -> dict:
    """존재하지 않는 윈도우 ID 제거, 근거 없는 비트는 missing으로 강등."""
    n = len(windows)
    fixed = 0

    def _ids(v) -> list[int]:
        out = []
        for w in (v or []):
            try:
                i = int(w)
            except (TypeError, ValueError):
                continue
            if 0 <= i < n:
                out.append(i)
        return out

    def _num(v, default=0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    plan["acts"] = [a for a in (plan.get("acts") or []) if isinstance(a, dict)]
    for act in plan["acts"]:
        act["beats"] = [b for b in (act.get("beats") or []) if isinstance(b, dict)]
        for b in act["beats"]:
            raw = b.get("evidence_windows") or []
            ev = _ids(raw)
            if len(ev) != len(raw):
                fixed += 1
            b["evidence_windows"] = ev
            b["verify_windows"] = _ids(b.get("verify_windows"))[:3]
            b["target_seconds"] = _num(b.get("target_seconds"), 6.0)
            b["must_have"] = bool(b.get("must_have"))
            b["nonlinear"] = bool(b.get("nonlinear"))
            if not ev and b.get("evidence_grade") != "missing":
                b["evidence_grade"] = "missing"
                fixed += 1
    hook = plan.get("hook") if isinstance(plan.get("hook"), dict) else {}
    hook["window_ids"] = _ids(hook.get("window_ids"))
    hook["seconds"] = _num(hook.get("seconds"), 5.0)
    plan["hook"] = hook
    plan["threads"] = plan.get("threads") or []
    plan["dropped"] = plan.get("dropped") or []
    if fixed:
        _log(f"검증: 비트 {fixed}개에서 잘못된 윈도우 참조/근거 누락 정정")
    return plan


# ---------------------------------------------------------------------------
# 표적 재관찰 — 어긋나는 비트만 프레임을 뽑아 비전에 질문
# ---------------------------------------------------------------------------

def _file_offsets(files: list[dict]) -> list[tuple[float, float, str]]:
    off = 0.0
    out = []
    for f in files:
        out.append((off, off + f["duration"], f["name"]))
        off += f["duration"]
    return out


def _extract_frames(folder: str, offsets, windows, wids: list[int], out_dir: str) -> list[str]:
    """윈도우당 2장(앞·뒤). 캐시 키는 파일명+파일 내 시각이라 윈도우 번호가 바뀌어도 안전."""
    from stage2 import extract_frame_to_file  # ffmpeg 호출·타임아웃을 한 곳에서 관리

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for wid in wids:
        w = windows[wid]
        dur = w["globalEnd"] - w["globalStart"]
        for dt in (0.2 * dur, 0.7 * dur):          # 꼬리 윈도우(1~10초)도 범위 안에서
            t = w["globalStart"] + dt
            hit = next(((a, n) for a, b, n in offsets if a <= t < b), None)
            if not hit:
                continue
            a, name = hit
            local = t - a
            dst = os.path.join(out_dir, f"{os.path.splitext(name)[0]}_{local:.1f}s.jpg")
            if not os.path.exists(dst):
                extract_frame_to_file(os.path.join(folder, name), local, dst)
            if os.path.exists(dst):
                paths.append(dst)
    return paths


def relook(plan: dict, windows: list[dict], files: list[dict], folder: str,
           out_dir: str, max_beats: int = 6) -> dict:
    offsets = _file_offsets(files)
    todo = []
    for act in plan.get("acts", []):
        for b in act.get("beats", []):
            if b.get("verify_windows") and b.get("verify_question"):
                todo.append(b)
    if not todo:
        _log("재관찰: 대상 비트 없음")
        return plan
    todo = todo[:max_beats]
    _log(f"재관찰: 비트 {len(todo)}개")

    for b in todo:
        wids = b["verify_windows"]
        frames = _extract_frames(folder, offsets, windows, wids, out_dir)
        if not frames:
            b["relook"] = {"verdict": "불확실", "answer": "(프레임 추출 실패)"}
            continue
        desc = " / ".join(windows[w].get("desc", "")[:120] for w in wids)
        prompt = (RELOOK_PROMPT
                  .replace("__DESC__", desc)
                  .replace("__CLAIM__", b.get("conflict") or b.get("intent", ""))
                  .replace("__QUESTION__", b["verify_question"]))
        _log(f"  {b.get('id')}: W{wids} 프레임 {len(frames)}장 → 비전 질의")
        ans = call_claude_vision(prompt, frames, timeout=240)
        m = re.search(r"판정\s*[:：]\s*(확인|부정|불확실)", ans or "")
        verdict = m.group(1) if m else "불확실"
        b["relook"] = {"verdict": verdict, "answer": (ans or "").strip()[:600], "frames": frames}
        if verdict == "확인" and b.get("evidence_grade") == "indirect":
            b["evidence_grade"] = "direct"
        if verdict == "부정":
            # 화면은 존재한다. 틀린 건 시놉시스(또는 설명)의 해석이므로 비트를
            # 버리지 않고 "진술과 다름"으로 표시해 사람이 의도·자막을 고치게 한다.
            b["claim_status"] = "refuted"
        _log(f"  {b.get('id')}: 판정={verdict}")
    return plan


# ---------------------------------------------------------------------------
# 사람이 읽을 아웃라인
# ---------------------------------------------------------------------------

def render_markdown(plan: dict, windows: list[dict], target_minutes: int) -> str:
    def span(wids):
        if not wids:
            return "—"
        a = min(windows[w]["globalStart"] for w in wids)
        b = max(windows[w]["globalEnd"] for w in wids)
        return f"{_fmt(a)}~{_fmt(b)}"

    grade_mark = {"direct": "●", "indirect": "◐", "missing": "○"}
    refuted_mark = "⛔"  # 화면은 있으나 재관찰 결과 진술과 다름 — 의도·자막 재검토 필요
    out = [f"# 스토리 설계 — {plan.get('logline', '')}", ""]
    hook = plan.get("hook") or {}
    out += ["## 훅", f"- {span(hook.get('window_ids', []))} W{hook.get('window_ids', [])} · {hook.get('seconds', '?')}초",
            f"- {hook.get('why', '')}", ""]

    total = 0.0
    kept = 0
    for act in plan.get("acts", []):
        out.append(f"## {act.get('title', '')}")
        for b in act.get("beats", []):
            g = b.get("evidence_grade", "?")
            mark = refuted_mark if b.get("claim_status") == "refuted" else grade_mark.get(g, "?")
            secs = b.get("target_seconds", 0) or 0
            if g != "missing":
                total += secs
                kept += 1
            line = (f"- {mark} **{b.get('id')}** [{b.get('role')}] {span(b.get('evidence_windows', []))} "
                    f"W{b.get('evidence_windows', [])} · {secs:.0f}초"
                    f"{' · 필수' if b.get('must_have') else ''} — {b.get('intent', '')}")
            out.append(line)
            if b.get("conflict"):
                out.append(f"    - ⚠ 충돌: {b['conflict']}")
            if b.get("relook"):
                r = b["relook"]
                out.append(f"    - 🔍 재관찰 판정 **{r.get('verdict')}**: {r.get('answer', '').splitlines()[-1][:200] if r.get('answer') else ''}")
            if b.get("caption_hint"):
                out.append(f"    - 자막: {b['caption_hint']}")
        out.append("")

    if plan.get("threads"):
        out.append("## 되짚는 실")
        for t in plan["threads"]:
            out.append(f"- {t.get('name')}: {', '.join(t.get('beat_ids', []))}")
        out.append("")
    if plan.get("dropped"):
        out.append("## 버린 것")
        for d in plan["dropped"]:
            out.append(f"- {d.get('what')} — {d.get('why')}")
        out.append("")
    out.append(f"---\n비트 {kept}개 · 목표 합 {total / 60:.1f}분 (목표 {target_minutes}분) · "
               f"● 직접 근거 ◐ 간접 근거 ○ 화면에 없음 ⛔ 화면은 있으나 진술과 다름(재검토)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def plan_story_from_data(
    windows: list[dict],
    scenes: list[dict],
    files: list[dict],
    folder: str,
    synopsis: str,
    target_minutes: int,
    relook_dir: str | None = None,
    do_relook: bool = True,
    progress_callback=None,
) -> dict:
    """1단계 스토리 설계 — 메모리 데이터로 호출 (analyze.py 경로)"""
    scenes = [s for s in scenes if not s.get("is_ng")]
    prompt = (STORY_PROMPT
              .replace("__SYNOPSIS__", synopsis.strip())
              .replace("__TARGET_MIN__", str(target_minutes))
              .replace("__SCENE_INDEX__", build_scene_index(scenes))
              .replace("__WINDOW_INDEX__", build_window_index(windows)))
    _log(f"스토리 설계 호출: 윈도우 {len(windows)}개, 씬 {len(scenes)}개, 프롬프트 {len(prompt):,}자")

    plan = None
    for attempt in range(2):
        raw = call_claude_text(prompt, timeout=1800)
        if not raw:
            raise RuntimeError("스토리 설계: Claude 응답 없음 — 타임아웃이거나 CLI 오류")
        plan = _extract_json(raw)
        if plan is not None:
            break
        # 원문은 남긴다 — 30분짜리 호출 결과를 파싱 실패로 잃지 않게
        keep_dir = os.path.dirname(relook_dir) if relook_dir else folder
        raw_name = f"story_raw_failed_{attempt + 1}.txt"
        try:
            with open(os.path.join(keep_dir, raw_name), "w", encoding="utf-8") as f:
                f.write(raw)
        except OSError:
            pass
        _log(f"스토리 설계: JSON 파싱 실패 (시도 {attempt + 1}/2) — 원문을 {raw_name} 에 저장")
        if progress_callback and attempt == 0:
            progress_callback("editing", 82, "스토리 설계 응답 형식 오류 — 다시 요청 중 (Claude)...")
    if plan is None:
        raise RuntimeError("스토리 설계: 응답을 JSON으로 읽을 수 없음 (story_raw_failed_*.txt 확인)")
    plan = validate_plan(plan, windows)
    n_beats = sum(len(a.get("beats", [])) for a in plan.get("acts", []))
    _log(f"설계 완료: 막 {len(plan.get('acts', []))}개, 비트 {n_beats}개")

    if do_relook and relook_dir:
        if progress_callback:
            progress_callback("editing", 88, "설명과 시놉시스가 어긋난 장면 재관찰 중...")
        plan = relook(plan, windows, files, folder, relook_dir)
    return plan


def plan_story(folder: str, synopsis: str, target_minutes: int, name: str,
               do_relook: bool = True) -> tuple[dict, str]:
    """CLI 경로 — autocut/analysis.json 을 읽어 설계하고 파일로 저장"""
    autocut = os.path.join(folder, "autocut")
    with open(os.path.join(autocut, "analysis.json"), encoding="utf-8") as f:
        data = json.load(f)
    windows = data["windows"]
    plan = plan_story_from_data(
        windows, data["scenes"], data["files"], folder, synopsis, target_minutes,
        relook_dir=os.path.join(autocut, "relook"), do_relook=do_relook,
    )
    md = render_markdown(plan, windows, target_minutes)
    with open(os.path.join(autocut, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    with open(os.path.join(autocut, f"{name}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    _log(f"저장: {name}.json / {name}.md")
    return plan, md


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="스토리 우선 편집 — 1단계 스토리 설계")
    ap.add_argument("--folder", required=True, help="소스 폴더 (autocut/analysis.json 필요)")
    ap.add_argument("--synopsis", required=True, help="촬영자 시놉시스 파일")
    ap.add_argument("--target-minutes", type=int, default=20)
    ap.add_argument("--name", default="story")
    ap.add_argument("--no-relook", action="store_true", help="표적 재관찰 생략")
    args = ap.parse_args()
    with open(args.synopsis, encoding="utf-8") as f:
        syn = f.read()
    _, md = plan_story(args.folder, syn, args.target_minutes, args.name, do_relook=not args.no_relook)
    print(md)
