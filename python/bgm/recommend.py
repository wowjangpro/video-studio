#!/usr/bin/env python3
"""영상 구간에 어울리는 곡을 로컬 음악 라이브러리에서 추천한다

사용법 (bgm-venv):
    recommend.py <root> <video_path> <start> <end> [preference] [--top 10]

1. 구간 프레임을 Claude 가 보고 음악 조건을 정한다
   (검색 문장 · 에너지 · BPM 범위 · 보컬 허용 · 어울리는 장르/분위기).
2. 검색 문장의 CLAP 임베딩과 곡의 10초 구간 임베딩을 비교해, 곡마다 구간 길이만큼 가장 잘 맞는
   부분(시작 지점)을 찾는다.
3. 보컬 · BPM · 에너지 · 장르/분위기 · 곡 길이로 점수를 고치고, 한 아티스트는 최대 2곡까지 상위 N곡을 낸다.

출력 (JSON Lines):
    {"type": "progress", ...}
    {"type": "recommended", "criteria": {...}, "tracks": [...]}
    {"type": "error", "message": "..."}
"""
import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys

import numpy as np

from music_library import audio_base, audio_file, connect, emit, read_manifest

GENRES = ["Alternative & Punk", "Ambient", "Children's", "Cinematic", "Classical", "Country & Folk",
          "Dance & Electronic", "Hip-Hop & Rap", "Holiday", "Jazz & Blues", "Pop", "R&B & Soul",
          "Reggae", "Rock"]
MOODS = ["Angry", "Bright", "Calm", "Dark", "Dramatic", "Funky", "Happy", "Inspirational", "Romantic", "Sad"]
ENERGY_TARGET = {"low": 0.35, "medium": 0.6, "high": 0.85}
CLAUDE_MODEL = "claude-fable-5-1"
MAX_PER_ARTIST = 2
# 이 영상에 이미 넣은 곡 — 한 영상에서 같은 곡이 반복되지 않게 내린다 (빼지는 않는다)
USED_PENALTY = 0.05
VOCAL_PENALTY = 0.06


def progress(percent: int, message: str) -> None:
    emit({"type": "progress", "stage": "recommending", "percent": percent, "message": message})


# ── 1. 장면 → 음악 조건 ──

def scene_criteria(frame_paths: list[str], duration: float, preference: str) -> dict:
    from analyze_claude import _build_env

    pref = f'\n사용자 요청: "{preference.strip()}" — 반드시 반영하세요.\n' if preference.strip() else ""
    images = "\n".join(f"- {p}" for p in frame_paths)
    prompt = (
        f"다음 영상 프레임들을 Read 도구로 읽어서 분석하세요.\n\n{images}\n\n"
        f"이 영상 구간({duration:.0f}초)에 배경음악을 깔려고 합니다. 음악 라이브러리에서 곡을 찾을 조건을 정하세요.\n"
        f"{pref}\n"
        "아래 JSON 형식으로만 응답하세요:\n"
        "{\n"
        '  "scene_description": "장면의 분위기·장소·행동·시간대 (한국어 2~3문장)",\n'
        '  "music_queries": ["음악 검색 문장 (영어, 장르·악기·분위기·템포를 담은 한 문장)", "다른 관점의 문장", "또 다른 문장"],\n'
        '  "energy": "low | medium | high",\n'
        '  "bpm_min": 60, "bpm_max": 120,\n'
        '  "vocals_ok": false,\n'
        f'  "genres": ["다음 중 어울리는 것 1~3개: {", ".join(GENRES)}"],\n'
        f'  "moods": ["다음 중 어울리는 것 1~3개: {", ".join(MOODS)}"]\n'
        "}\n"
        "영상 배경음악은 대사·내레이션과 겹치므로 특별한 이유가 없으면 vocals_ok 는 false 로 두세요."
    )
    cmd = ["claude", "-p", "--model", CLAUDE_MODEL, "--output-format", "json", "--no-session-persistence"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=240, env=_build_env())
    if r.returncode != 0:
        raise RuntimeError(f"Claude 분석 실패: {r.stderr.strip()[:300]}")
    text = json.loads(r.stdout).get("result", "")
    start, end = text.find("{"), text.rfind("}") + 1
    if start < 0 or end <= start:
        raise RuntimeError("Claude 응답에서 조건(JSON)을 찾지 못했습니다")
    c = json.loads(text[start:end])
    queries = [q for q in (c.get("music_queries") or []) if isinstance(q, str) and q.strip()]
    if not queries:
        raise RuntimeError("Claude 가 음악 검색 문장을 만들지 않았습니다")
    return {
        "scene_description": str(c.get("scene_description") or ""),
        "music_queries": queries[:5],
        "energy": c.get("energy") if c.get("energy") in ENERGY_TARGET else "medium",
        "bpm_min": float(c.get("bpm_min") or 0) or None,
        "bpm_max": float(c.get("bpm_max") or 0) or None,
        "vocals_ok": bool(c.get("vocals_ok")),
        "genres": [g for g in (c.get("genres") or []) if g in GENRES],
        "moods": [m for m in (c.get("moods") or []) if m in MOODS],
    }


# ── 2·3. 검색 + 점수 ──

def bpm_penalty(bpm: float | None, lo: float | None, hi: float | None) -> float:
    if not bpm or not lo or not hi:
        return 0.0
    # 박자 추정은 절반·두 배로 나오기 쉬워서 셋 중 범위에 가장 가까운 값으로 본다
    dist = min(max(0.0, lo - b, b - hi) for b in (bpm, bpm / 2, bpm * 2))
    return min(0.05, dist / 400)


def best_window(sims: np.ndarray, k: int) -> tuple[int, float]:
    """연속 k 구간 평균 유사도가 가장 큰 시작 구간과 그 값."""
    if len(sims) <= k:
        return 0, float(sims.mean())
    c = np.convolve(sims, np.ones(k) / k, mode="valid")
    i = int(np.argmax(c))
    return i, float(c[i])


def rank(conn, root: str, query_emb: np.ndarray, criteria: dict, duration: float, top: int,
         used: set[str] | None = None) -> list[dict]:
    rows = conn.execute(
        """SELECT t.id, t.title, t.artist, t.genre, t.mood, t.duration_sec, t.license_type, t.attribution_text,
                  t.audio_path, a.bpm, a.has_vocals, a.features_json, e.dim, e.segment_sec, e.n_segments, e.segments
           FROM tracks t JOIN analysis a ON a.track_id = t.id JOIN embeddings e ON e.track_id = t.id
           WHERE t.audio_path IS NOT NULL"""
    ).fetchall()
    target_energy = ENERGY_TARGET[criteria["energy"]]
    base = audio_base(root)
    scored = []
    for r in rows:
        segs = np.frombuffer(r["segments"], dtype=np.float16).astype(np.float32).reshape(r["n_segments"], r["dim"])
        sims = segs @ query_emb
        k = max(1, math.ceil(duration / r["segment_sec"]))
        i, sem = best_window(sims, k)
        feats = json.loads(r["features_json"] or "{}")
        track_len = feats.get("duration") or r["duration_sec"] or 0
        # 마지막 구간은 0으로 채운 10초라 시작+구간 길이가 곡 끝을 넘을 수 있다 → 곡 안으로 당긴다
        offset = max(0.0, min(i * r["segment_sec"], track_len - duration)) if track_len else i * r["segment_sec"]
        energy = feats.get("energy") or []
        win = energy[int(offset):int(offset + duration)] or energy or [target_energy]
        win_energy = float(np.mean(win))

        score = sem
        reasons = []
        if not criteria["vocals_ok"] and r["has_vocals"]:
            # 대사·내레이션과 겹치는 보컬곡은 의미 유사도 차이(보통 0.01~0.05)보다 크게 내린다
            score -= VOCAL_PENALTY
            reasons.append("보컬 있음(감점)")
        bp = bpm_penalty(r["bpm"], criteria["bpm_min"], criteria["bpm_max"])
        score -= bp
        score -= 0.06 * abs(win_energy - target_energy)
        if track_len and track_len < duration:
            score -= 0.05
            reasons.append(f"곡이 구간보다 짧음({track_len:.0f}초)")
        if used and r["id"] in used:
            score -= USED_PENALTY
            reasons.append("이 영상에 이미 넣은 곡(감점)")
        genre_hit = [g for g in criteria["genres"] if r["genre"] and g in r["genre"]]
        mood_hit = [m for m in criteria["moods"] if r["mood"] and m in r["mood"]]
        score += 0.02 * bool(genre_hit) + 0.02 * bool(mood_hit)
        if genre_hit or mood_hit:
            reasons.insert(0, "장르·분위기 일치: " + ", ".join(genre_hit + mood_hit))
        if r["bpm"] and not bp:
            reasons.append(f"템포 {r['bpm']:.0f} BPM")
        reasons.append(f"구간 에너지 {win_energy:.2f} (목표 {target_energy})")

        full = audio_file(base, r["audio_path"])
        scored.append({
            "id": r["id"], "title": r["title"], "artist": r["artist"], "genre": r["genre"], "mood": r["mood"],
            "durationSec": track_len, "offsetSec": offset, "score": round(score, 4), "similarity": round(sem, 4),
            "bpm": r["bpm"], "hasVocals": bool(r["has_vocals"]), "licenseType": r["license_type"],
            "attributionText": r["attribution_text"], "audioPath": full, "reasons": reasons,
        })

    scored.sort(key=lambda t: -t["score"])
    picked, per_artist = [], {}
    for t in scored:
        a = t["artist"] or ""
        if per_artist.get(a, 0) >= MAX_PER_ARTIST:
            continue
        per_artist[a] = per_artist.get(a, 0) + 1
        picked.append(t)
        if len(picked) >= top:
            break
    return picked


def main() -> None:
    # 취소(SIGTERM)돼도 finally 가 돌아 임시 프레임 폴더를 지우도록 예외로 바꾼다
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("video")
    ap.add_argument("start", type=float)
    ap.add_argument("end", type=float)
    ap.add_argument("preference", nargs="?", default="")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--used", default="", help="이 영상에 이미 넣은 곡 ID (쉼표로 구분)")
    ap.add_argument("--criteria-json", help="장면 분석을 건너뛰고 이 조건(JSON)으로 검색 (시험용)")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    if read_manifest(root) is None:
        emit({"type": "error", "message": f"음악 라이브러리 폴더가 아닙니다: {root}"})
        sys.exit(1)
    duration = max(1.0, a.end - a.start)

    tmp_dir = None
    try:
        if a.criteria_json:
            criteria = json.loads(a.criteria_json)
        else:
            from analyze_claude import extract_frames
            progress(5, "영상 프레임을 뽑는 중...")
            frames = extract_frames(a.video, a.start, a.end)
            tmp_dir = os.path.dirname(frames[0]) if frames else None
            progress(30, "Claude 가 장면을 보고 음악 조건을 정하는 중...")
            criteria = scene_criteria(frames, duration, a.preference)

        progress(60, "음악 임베딩 모델을 불러오는 중...")
        from library_analyze import Clap
        clap = Clap()
        q = clap.text(criteria["music_queries"]).mean(axis=0)
        q /= np.linalg.norm(q) or 1.0

        progress(80, "라이브러리에서 곡을 찾는 중...")
        conn = connect(root)
        try:
            used = {x for x in a.used.split(",") if x}
            tracks = rank(conn, root, q, criteria, duration, a.top, used)
        finally:
            conn.close()
        emit({"type": "recommended", "criteria": criteria, "durationSec": duration, "tracks": tracks})
    except Exception as e:  # noqa: BLE001 — 실패 사유를 화면에 그대로 전달
        emit({"type": "error", "message": str(e)})
        sys.exit(1)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
