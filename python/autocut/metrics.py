"""Stage 1 메트릭 계산 모듈 (numpy 기반)"""

import numpy as np


def compute_motion_score(frames: list[np.ndarray]) -> float:
    """연속 프레임 간 픽셀 차이의 평균으로 움직임 측정"""
    if len(frames) < 2:
        return 0.0
    diffs = []
    for i in range(1, len(frames)):
        diff = np.abs(frames[i].astype(float) - frames[i - 1].astype(float))
        diffs.append(np.mean(diff) / 255.0)
    return float(np.mean(diffs))


def compute_audio_rms(audio_chunk: np.ndarray) -> float:
    """오디오 청크의 RMS 에너지"""
    if len(audio_chunk) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio_chunk.astype(float) ** 2)) / 32768.0)


def compute_audio_variance(audio_chunk: np.ndarray, window_size: int = 4000) -> float:
    """오디오 에너지의 윈도우별 분산"""
    if len(audio_chunk) < window_size:
        return 0.0
    energies = []
    for i in range(0, len(audio_chunk) - window_size, window_size):
        segment = audio_chunk[i : i + window_size].astype(float)
        energies.append(np.sqrt(np.mean(segment**2)) / 32768.0)
    return float(np.var(energies)) if energies else 0.0


def compute_brightness(frame: np.ndarray) -> float:
    """프레임 밝기 평균 (0~1)"""
    gray = np.mean(frame, axis=2) if frame.ndim == 3 else frame.astype(float)
    return float(np.mean(gray) / 255.0)


def compute_color_saturation(frame: np.ndarray) -> float:
    """프레임 채도 평균 (간소화된 계산)"""
    if frame.ndim != 3 or frame.shape[2] != 3:
        return 0.0
    f = frame.astype(float)
    max_c = np.max(f, axis=2)
    min_c = np.min(f, axis=2)
    diff = max_c - min_c
    mask = max_c > 0
    saturation = np.where(mask, diff / (max_c + 1e-10), 0.0)
    return float(np.mean(saturation))


def compute_closeup_ratio(frame: np.ndarray) -> float:
    """중앙부 vs 가장자리 선명도 비율 — 클로즈업(얕은 피사계 심도/보케) 감지

    중앙이 가장자리보다 훨씬 선명하면 클로즈업일 가능성이 높음.
    비율 > 2.0 이면 클로즈업으로 판단.
    """
    if frame.ndim == 3:
        gray = np.mean(frame, axis=2)
    else:
        gray = frame.astype(float)

    h, w = gray.shape

    # 중앙 30% 영역
    y1, y2 = int(h * 0.35), int(h * 0.65)
    x1, x2 = int(w * 0.35), int(w * 0.65)
    center = gray[y1:y2, x1:x2]

    # 가장자리 영역 (상하좌우 15%)
    top = gray[:int(h * 0.15), :]
    bottom = gray[int(h * 0.85):, :]
    left = gray[:, :int(w * 0.15)]
    right = gray[:, int(w * 0.85):]

    def sharpness(region: np.ndarray) -> float:
        if region.size < 4:
            return 0.0
        dx = np.diff(region, axis=1) if region.shape[1] > 1 else np.zeros(1)
        dy = np.diff(region, axis=0) if region.shape[0] > 1 else np.zeros(1)
        return float(np.var(dx) + np.var(dy))

    center_s = sharpness(center)
    edge_s = np.mean([sharpness(r) for r in [top, bottom, left, right]])

    if edge_s < 1e-6:
        return 1.0
    return center_s / max(edge_s, 1e-6)


def score_window(
    motion_score: float,
    audio_rms: float,
    audio_variance: float,
    brightness: float,
    saturation: float,
    static_seconds: float,
    closeup_ratio: float = 1.0,
) -> int:
    """Stage 1 스코어링"""
    score = 0

    if motion_score > 0.4:
        score += 2
    elif motion_score > 0.15:
        score += 1

    if audio_rms > 0.02:
        score += 1

    if audio_variance > 0.005:
        score += 1

    if saturation > 0.3:
        score += 1

    # 클로즈업 보너스 (중앙이 가장자리보다 2배 이상 선명)
    if closeup_ratio > 2.0:
        score += 1

    if static_seconds > 10:
        score -= 1

    if brightness < 0.02:
        score -= 3

    return score
