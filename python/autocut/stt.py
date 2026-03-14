"""STT 모듈 — faster-whisper로 음성 구간을 텍스트로 변환

auto-subtitle의 검증된 Whisper 설정을 재활용.
편집 판단 참고용이므로 word_timestamps 불필요, 세그먼트 단위면 충분.
"""

import gc
import re
import sys

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

SAMPLING_RATE = 16000
AUDIO_PAD = 0.5  # 양쪽 패딩 (초)
MAX_GROUP_DURATION = 120.0  # 최대 그룹 길이 (초)
MAX_COMPRESSION_RATIO = 2.4
_noise_re = re.compile(r'^[\d\s\.\,\!\?\-\~]+$')


def _log(msg: str):
    print(f"[stt] {msg}", file=sys.stderr, flush=True)


def _split_long_regions(regions: list[dict]) -> list[dict]:
    """120초 초과 음성 구간을 분할"""
    result = []
    for r in regions:
        duration = r["end"] - r["start"]
        if duration <= MAX_GROUP_DURATION:
            result.append(r)
        else:
            start = r["start"]
            while start < r["end"]:
                end = min(start + MAX_GROUP_DURATION, r["end"])
                result.append({"start": start, "end": end})
                start = end
    return result


def _is_valid_segment(text: str, segment, prev_text: str) -> bool:
    """할루시네이션 필터 (auto-subtitle 검증 패턴)"""
    if not text:
        return False
    if _noise_re.match(text):
        return False
    if text == prev_text:
        return False
    if segment.no_speech_prob > 0.6:
        return False
    if segment.avg_logprob < -1.5:
        return False
    if segment.compression_ratio > MAX_COMPRESSION_RATIO:
        return False
    if not re.search(r'[가-힣]', text):
        return False
    korean_chars = re.findall(r'[가-힣]', text)
    if len(korean_chars) <= 1:
        return False
    return True


def _overlaps_with_vad(seg_start: float, seg_end: float,
                       vad_regions: list[dict], min_overlap: float = 0.3) -> bool:
    """세그먼트가 VAD 음성 구간과 충분히 겹치는지 검증"""
    seg_dur = seg_end - seg_start
    if seg_dur <= 0:
        return False
    overlap = 0.0
    for r in vad_regions:
        o_start = max(seg_start, r["start"])
        o_end = min(seg_end, r["end"])
        if o_end > o_start:
            overlap += o_end - o_start
    return (overlap / seg_dur) >= min_overlap


def transcribe_speech_regions(
    audio_path: str,
    speech_regions: list[dict],
    progress_callback=None,
) -> list[dict]:
    """음성 구간을 텍스트로 변환

    Args:
        audio_path: WAV 파일 경로 (16kHz 모노)
        speech_regions: VAD 결과 [{"start": float, "end": float}, ...]
        progress_callback: (percent, message) 콜백

    Returns:
        [{"start": float, "end": float, "text": str}, ...]
        타임스탬프는 파일 내 로컬 시간 (초)
    """
    if not speech_regions:
        return []

    groups = _split_long_regions(speech_regions)
    total_speech = sum(g["end"] - g["start"] for g in groups)
    _log(f"STT 시작: {len(groups)}개 그룹, 음성 {total_speech:.1f}초")

    # 모델 로드
    _log("Whisper 모델 로딩 (large-v3, int8)...")
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    _log("Whisper 모델 로딩 완료")

    # 오디오 로드
    audio = decode_audio(audio_path, sampling_rate=SAMPLING_RATE)
    total_duration = len(audio) / SAMPLING_RATE

    all_transcripts = []
    prev_text = ""

    for gi, group in enumerate(groups):
        if progress_callback:
            pct = int((gi / max(len(groups), 1)) * 100)
            progress_callback(pct, f"STT ({gi+1}/{len(groups)})")

        # 오디오 슬라이싱 (±패딩)
        pad_start = max(0, group["start"] - AUDIO_PAD)
        pad_end = min(total_duration, group["end"] + AUDIO_PAD)
        start_sample = int(pad_start * SAMPLING_RATE)
        end_sample = int(pad_end * SAMPLING_RATE)
        audio_chunk = audio[start_sample:end_sample]
        chunk_offset = pad_start

        segments, _ = model.transcribe(
            audio_chunk,
            language="ko",
            beam_size=5,
            word_timestamps=False,
            condition_on_previous_text=False,
            hallucination_silence_threshold=2.0,
            initial_prompt=None,
            repetition_penalty=1.2,
            no_repeat_ngram_size=3,
            temperature=0.0,
            no_speech_threshold=0.6,
        )

        for segment in segments:
            text = segment.text.strip()
            if not _is_valid_segment(text, segment, prev_text):
                continue

            seg_start = chunk_offset + segment.start
            seg_end = chunk_offset + segment.end

            if not _overlaps_with_vad(seg_start, seg_end, speech_regions):
                continue

            all_transcripts.append({
                "start": round(seg_start, 3),
                "end": round(seg_end, 3),
                "text": text,
            })
            prev_text = text

    # 모델 해제 (Stage 2 비전 모델 메모리 확보)
    del model
    del audio
    gc.collect()
    _log(f"STT 완료: {len(all_transcripts)}개 세그먼트")

    return all_transcripts


def map_transcripts_to_windows(
    transcripts: list[dict],
    windows: list[dict],
    window_duration: int = 10,
) -> None:
    """Whisper 세그먼트를 10초 윈도우에 매핑 (in-place)

    각 윈도우에 "transcript" 키를 추가.
    겹치는 세그먼트가 여러 개면 " / "로 결합.
    """
    if not transcripts:
        return

    for w in windows:
        w_start = w["start"]
        w_end = w["end"]
        texts = []
        for t in transcripts:
            # 겹침 확인
            if t["end"] > w_start and t["start"] < w_end:
                texts.append(t["text"])
        w["transcript"] = " / ".join(texts) if texts else ""

    mapped = sum(1 for w in windows if w.get("transcript"))
    _log(f"대사 매핑: {mapped}/{len(windows)} 윈도우에 대사 할당")
