"""EDL (Edit Decision List) 생성 모듈 — CMX 3600 포맷

DaVinci Resolve에서 File > Import > EDL로 불러와
타임라인에 KEEP 구간만 자동 배치.
"""

import bisect


def _resolve_fps(fps: float) -> tuple[int, bool, str]:
    """실수 fps → (정수 fps, drop_frame 여부, FCM 라벨)

    NTSC 계열(29.97, 59.94, 23.976)은 frame count base를 정수로 사용한다.
    29.97/59.94는 drop frame, 23.976은 non-drop으로 처리.
    """
    if abs(fps - 23.976) < 0.05 or abs(fps - 24000 / 1001) < 0.05:
        return 24, False, "NON-DROP FRAME"
    if abs(fps - 29.97) < 0.05 or abs(fps - 30000 / 1001) < 0.05:
        return 30, True, "DROP FRAME"
    if abs(fps - 59.94) < 0.05 or abs(fps - 60000 / 1001) < 0.05:
        return 60, True, "DROP FRAME"
    return int(round(fps)), False, "NON-DROP FRAME"


def seconds_to_timecode(seconds: float, fps: int = 24, drop_frame: bool = False) -> str:
    """초 → HH:MM:SS:FF 타임코드 변환

    drop_frame=True인 경우 NTSC drop frame timecode(SMPTE) 사용:
      - 매 분의 처음 2프레임을 drop (단, 10분의 배수 제외)
      - 표기 구분자: ';' (NDF는 ':')
    """
    if seconds < 0:
        seconds = 0

    if not drop_frame:
        total_frames = round(seconds * fps)
        ff = total_frames % fps
        total_seconds = total_frames // fps
        ss = total_seconds % 60
        mm = (total_seconds // 60) % 60
        hh = total_seconds // 3600
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    # SMPTE drop frame (29.97 = 30 base, 59.94 = 60 base)
    # 실시간 초를 drop frame timecode로 변환
    actual_fps = fps * 1000 / 1001  # 29.97 또는 59.94
    frame_number = round(seconds * actual_fps)

    drop_frames = 2 if fps == 30 else 4  # 60fps drop은 4프레임
    frames_per_min = fps * 60 - drop_frames
    frames_per_10min = fps * 600 - drop_frames * 9

    d = frame_number // frames_per_10min
    m = frame_number % frames_per_10min
    if m > drop_frames:
        frame_number += drop_frames * 9 * d + drop_frames * ((m - drop_frames) // frames_per_min)
    else:
        frame_number += drop_frames * 9 * d

    ff = frame_number % fps
    ss = (frame_number // fps) % 60
    mm = (frame_number // (fps * 60)) % 60
    hh = (frame_number // (fps * 3600)) % 24
    return f"{hh:02d}:{mm:02d}:{ss:02d};{ff:02d}"


def generate_edl(
    segments: list[dict],
    files: list[dict],
    title: str = "Video Studio",
    fps: float = 24.0,
) -> str:
    """KEEP 세그먼트와 파일 목록으로 CMX 3600 EDL 생성

    Args:
        segments: [{"globalStart": float, "globalEnd": float, "label": str, ...}]
        files: [{"name": str, "duration": float, "offset": float, "path": str}]
        title: EDL 타이틀
        fps: 프레임레이트(실수). 29.97 → drop frame, 23.976/24/25/30/60 → NDF

    Returns:
        EDL 문자열

    Note:
        파일별로 유니크한 인덱스 기반 reel name(`R001`, `R002`...) 사용.
        파일명 앞 8자가 동일한 경우(예: `20260319_115649.mp4`,
        `20260319_124939.mp4`) 다빈치 리졸브에서 첫 번째 파일만 매칭되고
        나머지가 offline으로 표시되는 문제를 방지한다.
        `* SOURCE FILE:` 코멘트로 절대 경로를 함께 제공하여 다빈치
        auto-conform이 정확한 파일을 찾을 수 있게 한다.
    """
    if not segments or not files:
        return ""

    # 다빈치 리졸브 timeline의 default starting TC가 01:00:00:00이므로
    # record TC도 같은 오프셋으로 시작하면 conform이 자연스럽게 맞는다.
    # (source TC는 영상 metadata TC와 맞추기 위해 00:00:00 시작 유지)
    RECORD_TC_OFFSET_SEC = 3600

    fps_int, drop_frame, fcm = _resolve_fps(fps)

    # 파일별 누적 오프셋 배열 (bisect용)
    file_offsets = [f["offset"] for f in files]

    # 파일별 유니크 reel name (인덱스 기반, 8자 제한)
    file_reels = [f"R{i+1:03d}    "[:8] for i in range(len(files))]

    lines = [
        f"TITLE: {title}",
        f"FCM: {fcm}",
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

            # 로컬 타임코드 (소스 파일 내 위치, 0:00:00 시작 — 다빈치 metadata TC 매칭)
            local_start = clip_start - file_offset
            local_end = clip_end - file_offset
            src_in = seconds_to_timecode(local_start, fps_int, drop_frame)
            src_out = seconds_to_timecode(local_end, fps_int, drop_frame)

            # 레코드 타임코드 (타임라인상 위치, 01:00:00 시작 — 다빈치 default 매칭)
            rec_in = seconds_to_timecode(RECORD_TC_OFFSET_SEC + record_offset, fps_int, drop_frame)
            clip_duration = clip_end - clip_start
            rec_out = seconds_to_timecode(RECORD_TC_OFFSET_SEC + record_offset + clip_duration, fps_int, drop_frame)

            # 유니크 reel name (인덱스 기반, 파일명 충돌 방지)
            reel = file_reels[fi]

            lines.append(
                f"{event_num:03d}  {reel}  V     C        "
                f"{src_in} {src_out} {rec_in} {rec_out}"
            )
            lines.append(f"* FROM CLIP NAME: {f['name']}")

            # 절대 경로 — 다빈치 auto-conform이 파일 찾을 때 사용
            if f.get("path"):
                lines.append(f"* SOURCE FILE: {f['path']}")

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
