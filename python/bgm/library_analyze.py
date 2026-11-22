#!/usr/bin/env python3
"""음악 라이브러리의 음원을 분석해 카탈로그(analysis · embeddings)에 기록한다

사용법 (bgm-venv):
    library_analyze.py <root> [--limit N] [--workers 4] [--force]

곡마다 기록하는 것
- analysis.bpm / musical_key / has_vocals
- analysis.features_json:
    duration      실제 길이(초)
    loudness_db   평균 음량 (RMS dBFS)
    energy        1초 단위 에너지 곡선 (0~1, 곡 안에서 정규화)
    intro_sec     에너지가 처음으로 곡 최대의 50% 에 닿는 시점
    climax_sec    2초 평활 에너지가 가장 큰 시점
    ending        "fade" (마지막 5초가 곡 평균보다 크게 작아지며 끝남) | "cut"
    brightness    스펙트럼 중심 평균 (Hz) — 클수록 밝은 소리
    onset_rate    초당 소리 시작 수 — 리듬 밀도
    vocal_prob    가사 보컬 점수 — AST(AudioSet) 의 노래 라벨(Singing·Male/Female singing·Rapping…)
                  확률의 곡 안 4구간 최댓값. 0.05 이상이면 has_vocals=1
    voice         목소리 관련 라벨별 4구간 최댓값 {라벨: 확률} (합창·허밍·성가·말소리 포함, 기준 재조정용)
    tags          AST 가 곡 전체에서 높게 본 AudioSet 라벨 상위 8개 [[라벨, 확률], …] (악기·장르·분위기)
    key_strength  조성 판정 확신도 (0~1)
- embeddings: CLAP(laion/larger_clap_general) 10초 구간별 임베딩과 평균

보컬 판정에 CLAP 문장 비교를 쓰지 않는 이유: 팝 보컬/연주판으로 기준을 맞춰도 클래식 현악·합주를
노래로 착각했다(클래식 61곡 중 58곡 보컬 판정, 2026-11-17). AST 는 연주곡 0.00~0.02,
보컬곡 0.05~0.27 로 갈렸다.

진행 상황은 stdout JSON Lines ({"type": "progress", ...}) 로 낸다.
언제 멈춰도 다시 실행하면 분석하지 않은 곡부터 이어 한다 (--force 는 전부 다시).
"""
import argparse
import json
import multiprocessing as mp
import os
import sqlite3
import sys
import time

import numpy as np

from music_library import audio_base, audio_file, connect, emit, now_iso, read_manifest

ANALYZER_VERSION = "4"  # 2: CLAP 모델 교체, 3: 보컬 판정을 AST 로, 4: 가사 보컬과 허밍·합창 구분
# laion/larger_clap_music 은 체크포인트가 망가져 어떤 문장이든 같은 임베딩을 낸다 (2026-11-17 확인:
# 서로 다른 문장끼리 코사인 0.999, logit scale 1.0) → 음악·음성·일반 소리를 함께 학습한 general 을 쓴다.
CLAP_MODEL = "laion/larger_clap_general"
CLAP_SR = 48000
SEGMENT_SEC = 10.0
FEATURE_SR = 22050

# 보컬 판정·소리 태그 (AudioSet 527 라벨 분류기)
AST_MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
AST_SR = 16000
AST_CLIPS = 4
# 가사가 있는 노래 — 대사·내레이션과 부딪히므로 추천에서 감점한다
LYRIC_LABELS = ("Singing", "Male singing", "Female singing", "Child singing", "Synthetic singing",
                "Rapping", "Yodeling")
# 가사 없는 목소리 질감 — 기록만 한다 (클래식·뉴에이지에 흔하다)
TEXTURE_LABELS = ("Choir", "Chant", "Humming", "A capella", "Vocal music", "Mantra")
# 기준을 나중에 바꿀 수 있게 목소리 관련 라벨은 전부 구간 최댓값을 저장한다
VOICE_LABELS = LYRIC_LABELS + TEXTURE_LABELS + ("Speech", "Whistling")
VOCAL_THRESHOLD = 0.05
TAG_SKIP = {"Music", "Musical instrument"}

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler 조성 윤곽
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


# ── CPU 작업 (워커 프로세스) ──

def estimate_key(chroma_mean: np.ndarray) -> tuple[str, float]:
    best, best_r = "", -2.0
    for i in range(12):
        for profile, mode in ((MAJOR_PROFILE, "major"), (MINOR_PROFILE, "minor")):
            r = np.corrcoef(chroma_mean, np.roll(profile, i))[0, 1]
            if r > best_r:
                best, best_r = f"{KEY_NAMES[i]} {mode}", r
    return best, float(max(0.0, best_r))


def chroma_mean(y: np.ndarray, sr: int) -> np.ndarray:
    """12음 크로마 평균 — numpy STFT 로 직접 계산한다.

    이 환경(numba 0.64 + numpy 2.4)에서는 librosa 의 numba 기반 함수(beat_track · onset_detect ·
    chroma_cqt · chroma_stft)가 프로세스를 세그멘테이션 오류로 죽인다 (2026-11-17 확인). 그래서 쓰지 않는다.
    """
    from scipy.signal import stft

    f, _, z = stft(y, fs=sr, nperseg=4096, noverlap=2048)
    mag = np.abs(z)
    band = (f >= 55) & (f <= 2000)
    pcs = (np.round(12 * np.log2(f[band] / 440.0) + 69).astype(int)) % 12
    energy = mag[band].mean(axis=1)
    out = np.zeros(12)
    np.add.at(out, pcs, energy)
    return out / (out.sum() or 1.0)


def analyze_signal(y48: np.ndarray) -> dict:
    import librosa
    from scipy.signal import find_peaks

    y = librosa.resample(y48, orig_sr=CLAP_SR, target_sr=FEATURE_SR)
    duration = len(y) / FEATURE_SR
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    frames_per_sec = FEATURE_SR / hop

    # 1초 단위 에너지 (곡 안에서 0~1 정규화)
    n_sec = max(1, int(np.ceil(duration)))
    per_sec = np.array([rms[int(i * frames_per_sec):int((i + 1) * frames_per_sec)].mean()
                        if int(i * frames_per_sec) < len(rms) else 0.0 for i in range(n_sec)])
    peak = per_sec.max() or 1.0
    energy = per_sec / peak
    smooth = np.convolve(energy, np.ones(2) / 2, mode="same")

    intro = next((i for i, e in enumerate(energy) if e >= 0.5), 0)
    climax = int(np.argmax(smooth))
    tail = energy[-5:] if len(energy) >= 5 else energy
    ending = "fade" if tail.mean() < 0.35 * energy.mean() and tail[-1] < tail[0] else "cut"

    # 템포·리듬 밀도: onset 세기 곡선 (numba 를 쓰지 않는 함수만)
    env = librosa.onset.onset_strength(y=y, sr=FEATURE_SR, hop_length=hop)
    bpm = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=env, sr=FEATURE_SR, hop_length=hop))[0])
    onsets, _ = find_peaks(env, height=env.mean() + 0.5 * env.std(), distance=3)
    key, key_strength = estimate_key(chroma_mean(y, FEATURE_SR))
    centroid = librosa.feature.spectral_centroid(y=y, sr=FEATURE_SR, hop_length=hop)[0]

    loud = 20 * np.log10(max(float(np.sqrt(np.mean(y ** 2))), 1e-9))
    return {
        "bpm": round(bpm, 1),
        "key": key,
        "features": {
            "duration": round(duration, 2),
            "loudness_db": round(loud, 1),
            "energy": [round(float(e), 2) for e in energy],
            "intro_sec": int(intro),
            "climax_sec": climax,
            "ending": ending,
            "brightness": round(float(centroid.mean()), 0),
            "onset_rate": round(len(onsets) / max(duration, 1e-6), 2),
            "key_strength": round(key_strength, 2),
        },
    }


def segments_of(y48: np.ndarray) -> np.ndarray:
    """10초 구간으로 자른다 (마지막 남는 부분이 3초 미만이면 버림, 그 외에는 0으로 채움)."""
    seg = int(SEGMENT_SEC * CLAP_SR)
    out = []
    for start in range(0, len(y48), seg):
        piece = y48[start:start + seg]
        if len(piece) < 3 * CLAP_SR and out:
            break
        if len(piece) < seg:
            piece = np.pad(piece, (0, seg - len(piece)))
        out.append(piece)
    return np.stack(out).astype(np.float16)  # 프로세스 사이로 보내는 양을 줄인다 (모델에 넣을 때 float32 로)


def ast_clips(y48: np.ndarray) -> np.ndarray:
    """곡 전체에 고르게 퍼진 10초 구간 4개 (16kHz)."""
    import librosa

    y = librosa.resample(y48, orig_sr=CLAP_SR, target_sr=AST_SR)
    seg = 10 * AST_SR
    if len(y) < seg:
        y = np.pad(y, (0, seg - len(y)))
    starts = np.linspace(0, len(y) - seg, AST_CLIPS).astype(int)
    return np.stack([y[s:s + seg] for s in starts]).astype(np.float16)


def work(job: tuple[str, str]):
    track_id, path = job
    try:
        import librosa
        y48, _ = librosa.load(path, sr=CLAP_SR, mono=True)
        if len(y48) < CLAP_SR:
            return track_id, None, None, None, "1초보다 짧음"
        return track_id, analyze_signal(y48), segments_of(y48), ast_clips(y48), None
    except Exception as e:  # noqa: BLE001
        return track_id, None, None, None, f"{type(e).__name__}: {e}"


# ── CLAP (주 프로세스, GPU) ──

class Clap:
    def __init__(self):
        import torch
        from transformers import ClapModel, ClapProcessor

        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.processor = ClapProcessor.from_pretrained(CLAP_MODEL)
        self.model = ClapModel.from_pretrained(CLAP_MODEL).to(self.device).eval()

    def text(self, texts: list[str]) -> np.ndarray:
        inp = self.processor(text=texts, return_tensors="pt", padding=True).to(self.device)
        with self.torch.no_grad():
            e = self.model.get_text_features(**inp)
        e = self.torch.nn.functional.normalize(e, dim=-1)
        return e.cpu().numpy()

    def release(self) -> None:
        if self.device == "mps":
            self.torch.mps.empty_cache()

    def audio(self, segments: np.ndarray, batch: int = 16) -> np.ndarray:
        outs = []
        for i in range(0, len(segments), batch):
            inp = self.processor(audio=[x.astype(np.float32) for x in segments[i:i + batch]], sampling_rate=CLAP_SR, return_tensors="pt")
            inp = {k: v.to(self.device) for k, v in inp.items()}
            with self.torch.no_grad():
                e = self.model.get_audio_features(**inp)
            outs.append(self.torch.nn.functional.normalize(e, dim=-1).cpu().numpy())
        return np.concatenate(outs)



class AudioTagger:
    """AST(AudioSet) 로 보컬 여부와 소리 태그를 낸다."""

    def __init__(self):
        import torch
        from transformers import ASTFeatureExtractor, ASTForAudioClassification

        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.fe = ASTFeatureExtractor.from_pretrained(AST_MODEL)
        self.model = ASTForAudioClassification.from_pretrained(AST_MODEL).to(self.device).eval()
        self.labels = self.model.config.id2label
        self.lyric_ids = [i for i, l in self.labels.items() if l in LYRIC_LABELS]
        self.voice_ids = [i for i, l in self.labels.items() if l in VOICE_LABELS]

    def __call__(self, clips: np.ndarray) -> tuple[float, dict, list]:
        inp = self.fe([c.astype(np.float32) for c in clips], sampling_rate=AST_SR, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            p = self.torch.sigmoid(self.model(**inp).logits).cpu().numpy()
        vocal = float(p[:, self.lyric_ids].max())
        peak = p.max(axis=0)
        voice = {self.labels[i]: round(float(peak[i]), 3) for i in self.voice_ids}
        mean = p.mean(axis=0)
        tags = [[self.labels[int(i)], round(float(mean[i]), 3)] for i in np.argsort(-mean)
                if self.labels[int(i)] not in TAG_SKIP][:8]
        return vocal, voice, tags


def bounded_imap(pool, fn, jobs, max_inflight: int):
    """pool.imap_unordered 대신 쓴다 — 결과를 기다리는 작업을 max_inflight 개로 묶는다.

    imap_unordered 는 작업을 한꺼번에 넘겨서, 곡을 읽는 워커(CPU)가 GPU 처리보다 빠르면 곡당 수십 MB 의
    결과가 메인 프로세스에 끝없이 쌓였다 → 메모리·스왑이 차서 macOS 가 분석기를 7분마다 강제 종료했다
    (2026-11-18). 앞의 작업을 꺼낸 만큼만 새 작업을 넣는다.
    """
    from collections import deque

    it = iter(jobs)
    pending: deque = deque()
    for job in it:
        pending.append(pool.apply_async(fn, (job,)))
        if len(pending) >= max_inflight:
            break
    while pending:
        yield pending.popleft().get()
        nxt = next(it, None)
        if nxt is not None:
            pending.append(pool.apply_async(fn, (nxt,)))


def save(conn, track_id: str, res: dict, seg_emb: np.ndarray, mean: np.ndarray, vp: float) -> None:
    with conn:
        conn.execute(
            """INSERT INTO analysis (track_id, bpm, musical_key, has_vocals, features_json, analyzer_version, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(track_id) DO UPDATE SET bpm = excluded.bpm, musical_key = excluded.musical_key,
                 has_vocals = excluded.has_vocals, features_json = excluded.features_json,
                 analyzer_version = excluded.analyzer_version, analyzed_at = excluded.analyzed_at""",
            (track_id, res["bpm"], res["key"], int(vp >= VOCAL_THRESHOLD),
             json.dumps(res["features"], ensure_ascii=False), ANALYZER_VERSION, now_iso()))
        conn.execute(
            """INSERT OR REPLACE INTO embeddings (track_id, model, dim, segment_sec, n_segments, mean, segments)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (track_id, CLAP_MODEL, seg_emb.shape[1], SEGMENT_SEC, seg_emb.shape[0],
             mean.astype(np.float32).tobytes(), seg_emb.astype(np.float16).tobytes()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    if read_manifest(root) is None:
        emit({"type": "error", "message": f"음악 라이브러리 폴더가 아닙니다: {root}"})
        sys.exit(1)

    conn = connect(root)
    q = """SELECT t.id, t.audio_path FROM tracks t
           LEFT JOIN analysis a ON a.track_id = t.id
           WHERE t.audio_path IS NOT NULL"""
    if not a.force:
        q += " AND (a.analyzed_at IS NULL OR a.analyzer_version IS NOT ? )"
    rows = conn.execute(q, () if a.force else (ANALYZER_VERSION,)).fetchall()
    jobs = []
    base = audio_base(root)
    for r in rows:
        full = audio_file(base, r["audio_path"])
        if full and os.path.isfile(full):
            jobs.append((r["id"], full))
    if a.limit:
        jobs = jobs[:a.limit]
    total = len(jobs)
    emit({"type": "progress", "stage": "loading", "percent": 0, "message": f"분석 모델을 불러오는 중... (분석할 곡 {total})"})
    if not total:
        emit({"type": "analyzed", "done": 0, "failed": 0})
        return

    clap = Clap()
    tagger = AudioTagger()
    done = failed = 0
    started = time.time()
    ctx = mp.get_context("spawn")
    with ctx.Pool(a.workers) as pool:
        for track_id, res, segs, clips, err in bounded_imap(pool, work, jobs, max_inflight=a.workers * 2):
            if err:
                failed += 1
                emit({"type": "warning", "trackId": track_id, "message": err})
                continue
            seg_emb = clap.audio(segs)
            mean = seg_emb.mean(axis=0)
            mean /= np.linalg.norm(mean) or 1.0
            vp, voice, tags = tagger(clips)
            res["features"]["vocal_prob"] = round(vp, 3)
            res["features"]["voice"] = voice
            res["features"]["tags"] = tags
            for attempt in range(5):
                try:
                    save(conn, track_id, res, seg_emb, mean, vp)
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e) or attempt == 4:
                        raise
                    time.sleep(5)  # 다른 프로세스가 쓰는 중 — 조금 뒤 다시
            done += 1
            if done % 50 == 0:
                clap.release()  # MPS 캐시가 계속 커지지 않게
            if done % 10 == 0 or done == total:
                rate = done / (time.time() - started)
                left = (total - done - failed) / rate if rate else 0
                import resource
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024
                emit({"type": "progress", "stage": "analyzing", "percent": int(done * 100 / total),
                      "message": f"{done}/{total}곡 분석 (실패 {failed}) · 남은 시간 약 {int(left // 60)}분",
                      "maxRssMB": int(rss)})
    conn.close()
    emit({"type": "analyzed", "done": done, "failed": failed})


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # noqa: BLE001 — 무인 실행 중 죽은 이유를 로그에 남긴다
        import traceback
        emit({"type": "error", "message": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})
        raise
