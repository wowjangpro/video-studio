"""Silero VAD 래퍼 — auto-subtitle에서 검증된 패턴 재활용"""

from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_speech_timestamps, VadOptions

SAMPLING_RATE = 16000
GAP_THRESHOLD = 1.0


def detect_speech_regions(audio_path: str) -> list[dict]:
    """오디오 파일에서 음성 구간 감지"""
    audio = decode_audio(audio_path, sampling_rate=SAMPLING_RATE)

    vad_options = VadOptions(
        threshold=0.4,
        min_speech_duration_ms=500,
        min_silence_duration_ms=300,
        speech_pad_ms=200,
    )
    timestamps = get_speech_timestamps(audio, vad_options, sampling_rate=SAMPLING_RATE)

    regions = []
    for ts in timestamps:
        start_sec = ts["start"] / SAMPLING_RATE
        end_sec = ts["end"] / SAMPLING_RATE
        regions.append({"start": start_sec, "end": end_sec})

    return merge_nearby_regions(regions)


def merge_nearby_regions(regions: list[dict]) -> list[dict]:
    """근접한 음성 구간 병합 (GAP_THRESHOLD 이내)"""
    if not regions:
        return []
    merged = [dict(regions[0])]
    for r in regions[1:]:
        last = merged[-1]
        if r["start"] - last["end"] <= GAP_THRESHOLD:
            last["end"] = r["end"]
        else:
            merged.append(dict(r))
    return merged
