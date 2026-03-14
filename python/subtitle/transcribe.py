import sys
import json
import re
import argparse
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from faster_whisper.vad import get_speech_timestamps, VadOptions

SAMPLING_RATE = 16000
GAP_THRESHOLD = 1.0  # 1초 이내 간격의 음성 구간을 병합
MIN_GROUP_DURATION = 0.5  # 최소 그룹 길이 (초)
AUDIO_PAD = 0.5  # Whisper에 넘길 때 양쪽에 추가하는 오디오 패딩 (초)
START_OFFSET = -0.3  # 자막 시작 시간 보정 (Whisper가 늦게 잡는 경향 보정)
MAX_GROUP_DURATION = 120.0  # 최대 그룹 길이 (초), 너무 길면 분할

noise_re = re.compile(r'^[\d\s\.\,\!\?\-\~]+$')
MAX_COMPRESSION_RATIO = 2.4  # 이 이상이면 할루시네이션 가능성 높음


def detect_speech_regions(audio):
    """Silero VAD로 음성 구간 감지"""
    vad_options = VadOptions(
        threshold=0.4,
        min_speech_duration_ms=500,
        min_silence_duration_ms=300,
        speech_pad_ms=200,
    )
    timestamps = get_speech_timestamps(audio, vad_options, sampling_rate=SAMPLING_RATE)
    # 샘플 인덱스 → 초 단위로 변환
    regions = []
    for ts in timestamps:
        start_sec = ts["start"] / SAMPLING_RATE
        end_sec = ts["end"] / SAMPLING_RATE
        regions.append({"start": start_sec, "end": end_sec})
    return regions


def merge_nearby_regions(regions):
    """근접한 음성 구간을 병합 (GAP_THRESHOLD 이내 간격)"""
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


def split_long_groups(groups):
    """MAX_GROUP_DURATION 초과 그룹을 분할"""
    result = []
    for g in groups:
        duration = g["end"] - g["start"]
        if duration <= MAX_GROUP_DURATION:
            result.append(g)
        else:
            start = g["start"]
            while start < g["end"]:
                end = min(start + MAX_GROUP_DURATION, g["end"])
                result.append({"start": start, "end": end})
                start = end
    return result


def is_valid_segment(text, segment, prev_text):
    """유효한 자막인지 필터링"""
    if not text:
        return False
    if noise_re.match(text):
        return False
    if text == prev_text:
        return False
    if segment.no_speech_prob > 0.6:
        return False
    if segment.avg_logprob < -1.5:
        return False
    # 압축률 기반 할루시네이션 필터 (반복/무의미 텍스트는 압축률이 높음)
    if segment.compression_ratio > MAX_COMPRESSION_RATIO:
        return False
    # 한글이 하나도 없는 세그먼트 필터링 (영어 할루시네이션 방지)
    if not re.search(r'[가-힣]', text):
        return False
    # 한글 1글자만 있는 짧은 감탄사 필터링
    korean_chars = re.findall(r'[가-힣]', text)
    if len(korean_chars) <= 1:
        return False
    return True


def overlaps_with_vad(seg_start, seg_end, vad_regions, min_overlap=0.3):
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


PAUSE_SPLIT_THRESHOLD = 0.5  # 단어 사이 0.5초 이상 간격이면 분리
MAX_SEGMENT_DURATION = 10.0  # 이 시간 초과 시 최적 지점에서 강제 분리
SENTENCE_END_RE = re.compile(r'[.!?。]$')


def _split_words_to_segment(word_list, chunk_offset):
    """단어 리스트로부터 세그먼트 dict 생성"""
    text = "".join(w.word for w in word_list).strip()
    text = text.replace("...", "").strip()
    if not text:
        return None
    return {
        "start": word_list[0].start + chunk_offset,
        "end": word_list[-1].end + chunk_offset,
        "text": text,
    }


def _force_split_long(words, chunk_offset, max_dur):
    """긴 단어 그룹을 시간 중앙에 가까운 최적 지점에서 강제 분리 (재귀)"""
    seg = _split_words_to_segment(words, chunk_offset)
    if not seg or len(words) < 4:
        return [seg] if seg else []

    duration = seg["end"] - seg["start"]
    if duration <= max_dur:
        return [seg]

    # 시간 중앙에 가깝고 일시정지가 긴 최적 분리 지점 (양쪽 최소 2단어)
    mid_time = words[0].start + duration / 2
    best_idx = -1
    best_score = float('inf')
    for i in range(1, len(words) - 2):
        split_time = (words[i].end + words[i + 1].start) / 2
        gap = words[i + 1].start - words[i].end
        time_diff = abs(split_time - mid_time)
        score = time_diff - gap * 3  # 일시정지가 길수록 유리
        if score < best_score:
            best_score = score
            best_idx = i

    if best_idx < 0:
        return [seg]

    left = words[:best_idx + 1]
    right = words[best_idx + 1:]
    return _force_split_long(left, chunk_offset, max_dur) + _force_split_long(right, chunk_offset, max_dur)


def split_segment_by_sentences(segment, chunk_offset, group):
    """Whisper 세그먼트를 문장 경계에서 분리하여 여러 자막 dict 반환"""
    words = segment.words
    if not words or len(words) < 2:
        start = segment.start + chunk_offset
        end = segment.end + chunk_offset
        if words:
            start = words[0].start + chunk_offset
            end = words[-1].end + chunk_offset
        return [{"start": start, "end": end, "text": segment.text.strip()}]

    # 분리 지점 탐색
    split_points = []
    for i in range(len(words) - 1):
        word_text = words[i].word.strip()
        gap = words[i + 1].start - words[i].end

        # 구두점으로 끝나는 단어 뒤에서 분리
        if SENTENCE_END_RE.search(word_text):
            split_points.append(i)
        # 긴 일시정지에서 분리
        elif gap >= PAUSE_SPLIT_THRESHOLD:
            split_points.append(i)

    if not split_points:
        # 분리 지점 없으면 장문 강제 분리 시도
        return _force_split_long(words, chunk_offset, MAX_SEGMENT_DURATION)

    # 단어 그룹으로 분리
    word_groups = []
    prev_idx = 0
    for sp in split_points:
        group_words = words[prev_idx:sp + 1]
        if len(group_words) >= 2:
            word_groups.append(group_words)
            prev_idx = sp + 1

    # 나머지 단어
    if prev_idx < len(words):
        remaining = words[prev_idx:]
        if len(remaining) >= 2 or not word_groups:
            word_groups.append(remaining)
        elif word_groups and len(remaining) == 1:
            word_groups[-1] = list(word_groups[-1]) + list(remaining)

    # 각 그룹을 세그먼트로 변환 (장문이면 추가 분리)
    sub_segments = []
    for wg in word_groups:
        sub_segments.extend(_force_split_long(wg, chunk_offset, MAX_SEGMENT_DURATION))

    if not sub_segments:
        return [{"start": words[0].start + chunk_offset, "end": words[-1].end + chunk_offset, "text": segment.text.strip()}]
    return sub_segments


def merge_short_segments(segments, min_duration=0.8):
    """짧은 세그먼트를 인접 세그먼트와 병합 (문장 경계 존중)"""
    if not segments:
        return []
    result = [dict(segments[0])]
    for seg in segments[1:]:
        last = result[-1]
        last_dur = last["end"] - last["start"]
        seg_dur = seg["end"] - seg["start"]
        gap = seg["start"] - last["end"]
        # 이전 세그먼트가 문장부호로 끝나면 병합하지 않음
        last_ends_sentence = bool(SENTENCE_END_RE.search(last["text"].strip()))
        # 이전 세그먼트가 짧고, 현재와의 간격이 1초 이내면 병합
        if last_dur < min_duration and gap < 1.0 and not last_ends_sentence:
            last["end"] = seg["end"]
            last["text"] = last["text"] + " " + seg["text"]
        # 현재 세그먼트가 짧고, 이전과의 간격이 1초 이내면 병합
        elif seg_dur < min_duration and gap < 1.0 and not last_ends_sentence:
            last["end"] = seg["end"]
            last["text"] = last["text"] + " " + seg["text"]
        else:
            result.append(dict(seg))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_path", help="오디오 파일 경로")
    parser.add_argument("--model-size", default="medium", help="Whisper 모델 크기")
    parser.add_argument("--device", default="cpu", help="cpu 또는 cuda")
    parser.add_argument("--compute-type", default="int8", help="연산 타입")
    parser.add_argument("--description", default="", help="영상 설명")
    args = parser.parse_args()

    # initial_prompt는 역류 위험이 있으므로 사용하지 않음

    # 모델 로딩
    print(json.dumps({"status": "loading_model", "model": args.model_size}), flush=True)
    model = WhisperModel(
        args.model_size,
        device=args.device,
        compute_type=args.compute_type,
    )

    # 오디오 로딩
    print(json.dumps({"status": "transcribing"}), flush=True)
    audio = decode_audio(args.audio_path, sampling_rate=SAMPLING_RATE)
    total_duration = len(audio) / SAMPLING_RATE

    print(
        json.dumps({"status": "info", "duration": total_duration, "language": "ko"}),
        flush=True,
    )

    # VAD: 음성 구간 감지
    print(json.dumps({"status": "detecting_speech"}), flush=True)
    regions = detect_speech_regions(audio)
    groups = merge_nearby_regions(regions)
    groups = split_long_groups(groups)
    groups = [g for g in groups if g["end"] - g["start"] >= MIN_GROUP_DURATION]

    print(
        json.dumps({
            "status": "speech_detected",
            "groups": len(groups),
            "speech_duration": round(sum(g["end"] - g["start"] for g in groups), 1),
        }),
        flush=True,
    )

    # 각 음성 그룹별 Whisper 실행
    all_segments = []
    segment_id = 0

    for group_idx, group in enumerate(groups):
        print(json.dumps({
            "status": "transcribing_chunk",
            "chunk": group_idx + 1,
            "total_chunks": len(groups),
            "chunk_start": round(group["start"], 3),
            "chunk_end": round(group["end"], 3),
        }), flush=True)

        # 오디오 슬라이싱 (패딩 포함하여 Whisper에 컨텍스트 제공)
        pad_start = max(0, group["start"] - AUDIO_PAD)
        pad_end = min(total_duration, group["end"] + AUDIO_PAD)
        start_sample = int(pad_start * SAMPLING_RATE)
        end_sample = int(pad_end * SAMPLING_RATE)
        audio_chunk = audio[start_sample:end_sample]
        chunk_offset = pad_start  # 패딩된 시작점이 실제 오프셋

        segments, _ = model.transcribe(
            audio_chunk,
            language="ko",
            beam_size=5,
            word_timestamps=True,
            condition_on_previous_text=False,
            hallucination_silence_threshold=2.0,
            initial_prompt=None,
            repetition_penalty=1.2,
            no_repeat_ngram_size=3,
            temperature=0.0,
            no_speech_threshold=0.6,
        )

        prev_text = ""
        for segment in segments:
            text = segment.text.strip()

            if not is_valid_segment(text, segment, prev_text):
                continue

            # 문장 경계에서 세그먼트 분리
            sub_segs = split_segment_by_sentences(segment, chunk_offset, group)

            for sub in sub_segs:
                # 패딩 구간에서 발생한 세그먼트는 제외
                if sub["end"] < group["start"] or sub["start"] > group["end"]:
                    continue

                # VAD 음성 구간과 겹치는지 검증 (유령 세그먼트 제거)
                if not overlaps_with_vad(sub["start"], sub["end"], regions):
                    continue

                # 텍스트 길이 기반 최대 지속시간 제한
                max_dur = max(2.0, len(sub["text"]) * 0.3)
                if sub["end"] - sub["start"] > max_dur:
                    sub["end"] = sub["start"] + max_dur

                seg_data = {
                    "start": round(max(0, sub["start"] + START_OFFSET), 3),
                    "end": round(sub["end"], 3),
                    "text": sub["text"],
                }
                all_segments.append(seg_data)

                # 실시간 출력
                print(json.dumps({
                    "type": "segment",
                    "id": segment_id,
                    "start": seg_data["start"],
                    "end": seg_data["end"],
                    "text": seg_data["text"],
                }, ensure_ascii=False), flush=True)
                segment_id += 1

            prev_text = text

    print(json.dumps({"type": "done"}), flush=True)


if __name__ == "__main__":
    main()
