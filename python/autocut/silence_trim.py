"""편집 결과 segment 안의 긴 침묵 구간을 1초로 줄이는 후처리.

STT segment timing(speech_segments)을 기준으로 음성이 없는 갭을 추출,
1초 이상 갭이면 segment를 분할하여 침묵을 1초로 압축한다.

전제:
- speech_segments: [{"start": float, "end": float, "text": str}] (globalStart 기준)
- segments: [{"globalStart": float, "globalEnd": float, "label": str, ...}]
"""

from __future__ import annotations
import sys


MAX_SILENCE_SEC = 1.0   # 이 이상 침묵은 trim 대상
KEEP_SILENCE_SEC = 1.0  # 침묵을 이 길이로 압축


def _log(msg: str):
    print(f"[silence_trim] {msg}", file=sys.stderr, flush=True)


def trim_silence_in_segments(
    segments: list[dict],
    speech_segments: list[dict],
    max_silence: float = MAX_SILENCE_SEC,
    keep_silence: float = KEEP_SILENCE_SEC,
) -> list[dict]:
    """각 segment 안의 1초+ 침묵 구간을 1초로 압축 (분할).

    segment 내부의 음성 segment들을 찾아 갭 분석:
    - 갭 < max_silence: 그대로 유지
    - 갭 >= max_silence: segment를 분할 (이전 음성 끝 + 1초 / 다음 음성 시작 직전)

    분할된 segment의 globalEnd - globalStart가 KEEP_SILENCE_SEC보다 작으면 합쳐 둠.
    """
    if not segments or not speech_segments:
        return segments

    # 음성 segments 시간순 정렬
    speech_sorted = sorted(speech_segments, key=lambda s: s.get("start", 0))

    result: list[dict] = []
    new_id = 0
    trimmed_count = 0
    saved_seconds = 0.0

    for seg in segments:
        seg_start = seg.get("globalStart", 0)
        seg_end = seg.get("globalEnd", 0)
        if seg_end <= seg_start:
            continue

        # segment 내부에 걸치는 음성 추출 (segment 경계로 클램프)
        in_speech: list[tuple[float, float]] = []
        for sp in speech_sorted:
            sp_start = sp.get("start", 0)
            sp_end = sp.get("end", 0)
            if sp_end <= seg_start or sp_start >= seg_end:
                continue
            cs = max(sp_start, seg_start)
            ce = min(sp_end, seg_end)
            if ce > cs:
                in_speech.append((cs, ce))

        # 음성 없으면 그대로 유지 (비말소리 segment)
        if not in_speech:
            new_seg = dict(seg)
            new_seg["id"] = new_id
            result.append(new_seg)
            new_id += 1
            continue

        # 음성 사이 갭 (segment 시작/끝 갭도 포함)
        # gap 분석:
        #   gap_0: seg_start ~ in_speech[0][0]
        #   gap_i: in_speech[i-1][1] ~ in_speech[i][0]
        #   gap_n: in_speech[-1][1] ~ seg_end
        # 갭이 max_silence보다 크면 잘라냄 (앞쪽은 음성 끝 + keep_silence/2까지, 뒤쪽은 다음 음성 시작 - keep_silence/2부터)

        # 단순화: 음성 segments의 union을 만들고, 갭을 keep_silence로 압축한 segment 시퀀스 생성
        # 결과는 여러 개의 (sub_start, sub_end) 튜플
        ranges: list[tuple[float, float]] = []
        prev_end = seg_start

        # 시작 부분 갭
        first_speech_start = in_speech[0][0]
        if first_speech_start - prev_end > max_silence:
            # 시작 침묵을 keep_silence로 줄임
            ranges.append((first_speech_start - keep_silence, first_speech_start))
            saved_seconds += (first_speech_start - prev_end) - keep_silence
            trimmed_count += 1
            prev_end = first_speech_start
        else:
            prev_end = seg_start

        # 음성 + 사이 갭 처리
        for i, (sp_s, sp_e) in enumerate(in_speech):
            if i == 0:
                # 시작 음성 자체
                if not ranges:
                    ranges.append((prev_end, sp_e))
                else:
                    # ranges 마지막 끝과 이어붙임
                    ls, le = ranges[-1]
                    if le >= sp_s:
                        ranges[-1] = (ls, sp_e)
                    else:
                        ranges.append((sp_s, sp_e))
            else:
                prev_speech_end = in_speech[i - 1][1]
                gap = sp_s - prev_speech_end
                if gap > max_silence:
                    # 갭을 keep_silence로 압축: 이전 음성 끝 + keep_silence/2 까지, 다음 음성 시작 - keep_silence/2 부터
                    half = keep_silence / 2.0
                    # 이전 range 확장
                    ls, le = ranges[-1]
                    new_le = min(prev_speech_end + half, prev_speech_end + keep_silence)
                    ranges[-1] = (ls, new_le)
                    # 다음 range 시작
                    next_start = max(sp_s - half, sp_s - keep_silence)
                    ranges.append((next_start, sp_e))
                    saved_seconds += gap - keep_silence
                    trimmed_count += 1
                else:
                    # 갭이 짧으면 그냥 음성 끝까지 확장
                    ls, le = ranges[-1]
                    ranges[-1] = (ls, sp_e)

        # 끝 부분 갭
        last_speech_end = in_speech[-1][1]
        if seg_end - last_speech_end > max_silence:
            ls, le = ranges[-1]
            ranges[-1] = (ls, last_speech_end + keep_silence)
            saved_seconds += (seg_end - last_speech_end) - keep_silence
            trimmed_count += 1
        else:
            # 끝까지 확장
            ls, le = ranges[-1]
            ranges[-1] = (ls, seg_end)

        # ranges → segments 생성
        for sub_s, sub_e in ranges:
            if sub_e - sub_s < 0.3:
                continue  # 너무 짧은 조각 무시
            new_seg = dict(seg)
            new_seg["id"] = new_id
            new_seg["globalStart"] = sub_s
            new_seg["globalEnd"] = sub_e
            result.append(new_seg)
            new_id += 1

    if trimmed_count:
        _log(f"침묵 trim: {trimmed_count}개 갭 압축, 총 {saved_seconds:.1f}초 절약")

    return result
