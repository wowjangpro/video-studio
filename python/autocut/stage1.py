"""Stage 1: 경량 스캔 — FFmpeg + numpy로 모션/오디오 메트릭 계산"""

import subprocess
import sys
import numpy as np
from PIL import Image
import io

from metrics import (
    compute_motion_score,
    compute_audio_rms,
    compute_audio_variance,
    compute_brightness,
    compute_color_saturation,
    compute_closeup_ratio,
    score_window,
)


def extract_frames_from_video(video_path: str, fps: int = 1) -> list[np.ndarray]:
    """FFmpeg로 360p 프레임 추출 (pipe, JPEG)"""
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vf", f"scale=640:360,fps={fps}",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-q:v", "8",
        "-v", "quiet",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 and result.stderr:
        print(f"[stage1] FFmpeg 프레임 추출 오류: {result.stderr.decode()[:300]}", file=sys.stderr, flush=True)
    data = result.stdout

    frames = []
    # JPEG SOI marker: FF D8, EOI marker: FF D9
    start = 0
    while True:
        soi = data.find(b"\xff\xd8", start)
        if soi == -1:
            break
        eoi = data.find(b"\xff\xd9", soi + 2)
        if eoi == -1:
            break
        jpeg_data = data[soi : eoi + 2]
        try:
            img = Image.open(io.BytesIO(jpeg_data))
            frames.append(np.array(img))
        except Exception:
            pass
        start = eoi + 2

    return frames


def extract_audio_pcm(video_path: str) -> np.ndarray:
    """FFmpeg로 16kHz 모노 PCM 오디오 추출"""
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "-f", "s16le",
        "-v", "quiet",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 and result.stderr:
        print(f"[stage1] FFmpeg 오디오 추출 오류: {result.stderr.decode()[:300]}", file=sys.stderr, flush=True)
    return np.frombuffer(result.stdout, dtype=np.int16)


def analyze_file_stage1(
    video_path: str,
    window_duration: int = 8,
    overlap: int = 2,
) -> list[dict]:
    """파일 하나에 대해 Stage 1 분석 수행"""
    frames = extract_frames_from_video(video_path, fps=1)
    audio = extract_audio_pcm(video_path)

    if not frames:
        return []

    total_seconds = len(frames)
    step = window_duration - overlap
    sample_rate = 16000
    windows = []
    window_id = 0

    t = 0
    while t < total_seconds:
        end = min(t + window_duration, total_seconds)
        frame_slice = frames[t:end]

        audio_start = int(t * sample_rate)
        audio_end = int(min(end * sample_rate, len(audio)))
        audio_slice = audio[audio_start:audio_end] if audio_start < len(audio) else np.array([], dtype=np.int16)

        mid_frame = frame_slice[len(frame_slice) // 2] if frame_slice else None

        motion = compute_motion_score(frame_slice)
        rms = compute_audio_rms(audio_slice)
        variance = compute_audio_variance(audio_slice)
        brightness = compute_brightness(mid_frame) if mid_frame is not None else 0.5
        saturation = compute_color_saturation(mid_frame) if mid_frame is not None else 0.0
        closeup_ratio = compute_closeup_ratio(mid_frame) if mid_frame is not None else 1.0

        # 정적 프레임 연속 시간 계산 (최대 연속 구간)
        static_seconds = 0.0
        if len(frame_slice) >= 2:
            current_static = 0.0
            for i in range(1, len(frame_slice)):
                diff = np.mean(np.abs(frame_slice[i].astype(float) - frame_slice[i - 1].astype(float))) / 255.0
                if diff < 0.02:
                    current_static += 1.0
                    if current_static > static_seconds:
                        static_seconds = current_static
                else:
                    current_static = 0.0

        s1_score = score_window(motion, rms, variance, brightness, saturation, static_seconds, closeup_ratio)

        windows.append({
            "window_id": window_id,
            "start": float(t),
            "end": float(end),
            "motion_score": round(motion, 4),
            "audio_rms": round(rms, 4),
            "audio_variance": round(variance, 6),
            "brightness": round(brightness, 4),
            "saturation": round(saturation, 4),
            "closeup_ratio": round(closeup_ratio, 2),
            "static_seconds": round(static_seconds, 1),
            "score": s1_score,
        })

        # 처리 완료된 프레임 메모리 해제
        next_t = t + step
        for i in range(t, min(next_t, total_seconds)):
            frames[i] = None

        window_id += 1
        t += step

    return windows
