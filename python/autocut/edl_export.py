"""EDL (Edit Decision List) 생성 모듈 — CMX 3600 포맷

DaVinci Resolve에서 File > Import > EDL로 불러와
타임라인에 KEEP 구간만 자동 배치.
"""

import bisect
import os


def seconds_to_timecode(seconds: float, fps: int = 24) -> str:
    """초 → HH:MM:SS:FF 타임코드 변환"""
    if seconds < 0:
        seconds = 0
    total_frames = round(seconds * fps)
    ff = total_frames % fps
    total_seconds = total_frames // fps
    ss = total_seconds % 60
    mm = (total_seconds // 60) % 60
    hh = total_seconds // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def generate_edl(
    segments: list[dict],
    files: list[dict],
    title: str = "Video Studio",
    fps: int = 24,
) -> str:
    """KEEP 세그먼트와 파일 목록으로 CMX 3600 EDL 생성

    Args:
        segments: [{"globalStart": float, "globalEnd": float, "label": str, ...}]
        files: [{"name": str, "duration": float, "offset": float}]
        title: EDL 타이틀
        fps: 프레임레이트

    Returns:
        EDL 문자열
    """
    if not segments or not files:
        return ""

    # 파일별 누적 오프셋 배열 (bisect용)
    file_offsets = [f["offset"] for f in files]

    lines = [
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        "",
    ]

    event_num = 1
    record_offset = 0.0  # 타임라인상 누적 위치

    for seg in segments:
        g_start = seg["globalStart"]
        g_end = seg["globalEnd"]

        if g_end <= g_start:
            continue

        # 세그먼트가 걸치는 파일 범위 찾기
        fi_start = max(0, bisect.bisect_right(file_offsets, g_start) - 1)
        fi_end = max(0, bisect.bisect_right(file_offsets, g_end - 0.001) - 1)

        # 파일 경계에서 분할
        for fi in range(fi_start, fi_end + 1):
            f = files[fi]
            file_offset = f["offset"]
            file_end = file_offset + f["duration"]

            # 이 파일 내에서의 클립 범위
            clip_start = max(g_start, file_offset)
            clip_end = min(g_end, file_end)

            if clip_end <= clip_start:
                continue

            # 로컬 타임코드 (소스 파일 내 위치, 1시간 오프셋 관례)
            local_start = clip_start - file_offset
            local_end = clip_end - file_offset
            src_in = seconds_to_timecode(local_start + 3600, fps)  # +1h 오프셋
            src_out = seconds_to_timecode(local_end + 3600, fps)

            # 레코드 타임코드 (타임라인상 위치)
            rec_in = seconds_to_timecode(record_offset, fps)
            clip_duration = clip_end - clip_start
            rec_out = seconds_to_timecode(record_offset + clip_duration, fps)

            # 릴 이름 (파일명에서 확장자 제거, 최대 8자)
            reel = os.path.splitext(f["name"])[0][:8].ljust(8)

            lines.append(
                f"{event_num:03d}  {reel}  V     C        "
                f"{src_in} {src_out} {rec_in} {rec_out}"
            )
            lines.append(f"* FROM CLIP NAME: {f['name']}")

            # 라벨/이유가 있으면 코멘트 추가
            label = seg.get("label", "")
            reason = seg.get("reason", "")
            if label:
                comment = f"[{label}]"
                if reason:
                    comment += f" {reason}"
                lines.append(f"* COMMENT: {comment}")

            lines.append("")

            record_offset += clip_duration
            event_num += 1

    return "\n".join(lines)
