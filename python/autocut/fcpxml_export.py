"""FCPXML (Final Cut Pro XML) 생성 모듈

다빈치 리졸브와 Final Cut Pro 모두 표준으로 인식.
EDL 대비 장점:
  - reference id로 매칭 → 영상 metadata timecode와 무관
  - 영상 fps와 timeline fps가 달라도 다빈치가 자동 conform
  - 한글 파일명/특수문자 안전

사용 방법:
  - 다빈치: File > Import > Timeline > Final Cut Pro XML
"""

import bisect
import html
import os
from urllib.parse import quote

from edl_export import resolve_tc_base, resolve_edl_drop_frame, timecode_to_frames, RECORD_START_TC

# NTSC 레이트의 FCP 포맷 이름 접미사
_NTSC_LABEL = {24: "2398", 30: "2997", 60: "5994"}


def _frames(seconds: float, fps_int: int, ntsc: bool) -> int:
    """초 → 프레임 번호 (NTSC는 실제 레이트 기준)"""
    if seconds < 0:
        seconds = 0
    return round(seconds * (fps_int * 1000 / 1001 if ntsc else fps_int))


def _frames_str(frames: int, fps_int: int, ntsc: bool) -> str:
    """프레임 번호 → FCPXML 시간 표기"""
    if ntsc:
        return f"{frames * 1001}/{fps_int * 1000}s"
    return f"{frames}/{fps_int}s"


def _file_url(path: str) -> str:
    """절대 경로 → file:// URL (한글/공백 인코딩)"""
    abs_path = os.path.abspath(path)
    return "file://" + quote(abs_path, safe="/")


def generate_fcpxml(
    segments: list[dict],
    files: list[dict],
    fps: float = 24.0,
    project_name: str = "Video Studio",
    width: int = 3840,
    height: int = 2160,
) -> str:
    """KEEP 세그먼트와 파일 목록으로 FCPXML 생성.

    Args:
        segments: [{"globalStart", "globalEnd", "label", ...}]
        files: [{"path", "name", "duration", "offset"}]
        fps: timeline frame rate. 29.97 등 NTSC 실수를 그대로 넘긴다
        project_name: 프로젝트/이벤트 이름
        width, height: timeline 해상도 (기본 4K)

    Returns:
        FCPXML 문자열
    """
    if not segments or not files:
        return ""

    fps_int, actual_fps = resolve_tc_base(fps)
    ntsc = abs(actual_fps - fps_int) > 0.001
    drop_frame = resolve_edl_drop_frame(files, fps_int, actual_fps)
    tc_format = "DF" if drop_frame else "NDF"

    file_offsets = [f["offset"] for f in files]

    # asset 정의 — start는 영상 metadata TC 기준 (다빈치 매칭용)
    asset_lines = []
    asset_ranges = []  # 파일별 (시작 프레임, 끝 프레임) — 클립 범위 clamp용
    for i, f in enumerate(files):
        asset_id = f"r{i + 2}"  # r1은 format이라 r2부터
        name = os.path.splitext(f["name"])[0]
        url = _file_url(f["path"])
        tc_seconds = f.get("tc_seconds") or 0.0  # None 방어 (EDL 익스포터와 동일)
        start_frame = _frames(tc_seconds, fps_int, ntsc)
        dur_frames = _frames(f["duration"], fps_int, ntsc)
        asset_ranges.append((start_frame, start_frame + dur_frames))
        dur = _frames_str(dur_frames, fps_int, ntsc)
        start_tc = _frames_str(start_frame, fps_int, ntsc) if tc_seconds > 0 else "0s"
        asset_lines.append(
            f'    <asset id="{asset_id}" name="{html.escape(name)}" '
            f'src="{url}" start="{start_tc}" duration="{dur}" '
            f'hasVideo="1" hasAudio="1" format="r1" '
            f'audioSources="1" audioChannels="2" audioRate="48000"/>'
        )

    # asset-clip 생성 (timeline 위치별)
    clip_lines = []
    # 타임라인 시작 TC — EDL(generate_edl)과 같은 상수를 같은 방식(프레임)으로 해석해
    # 두 포맷의 컷 위치가 대칭이 되게 한다. spine 의 첫 offset 이 곧 tcStart 다.
    tc_start_frames = timecode_to_frames(RECORD_START_TC + (";00" if drop_frame else ":00"), fps_int)[0]
    record_frames = tc_start_frames  # spine은 연속이어야 하므로 프레임으로 누적한다.
    # (초로 누적하면 offset과 duration이 따로 반올림되어 1프레임 갭/겹침이 생긴다)

    for seg in segments:
        g_start = seg.get("globalStart", 0.0)
        g_end = seg.get("globalEnd", 0.0)
        if g_end <= g_start:
            continue

        # 세그먼트가 걸치는 파일 범위
        fi_start = max(0, bisect.bisect_right(file_offsets, g_start) - 1)
        fi_end = max(0, bisect.bisect_right(file_offsets, g_end - 0.001) - 1)

        for fi in range(fi_start, fi_end + 1):
            f = files[fi]
            file_offset = f["offset"]
            file_end = file_offset + f["duration"]

            clip_start = max(g_start, file_offset)
            clip_end = min(g_end, file_end)
            if clip_end <= clip_start:
                continue

            local_start = clip_start - file_offset
            local_end = clip_end - file_offset

            # asset의 start origin이 영상 metadata TC이므로,
            # asset-clip의 start는 (metadata TC + 영상 내 위치) 가 되어야 한다.
            # tc0와 파일 내 위치를 따로 반올림한다 — EDL(edl_export.py)과 같은
            # 방식이어야 두 포맷의 컷 지점이 프레임 단위로 일치한다.
            tc0_frame = asset_ranges[fi][0]
            src_start_frame = tc0_frame + _frames(local_start, fps_int, ntsc)
            src_end_frame = tc0_frame + _frames(local_end, fps_int, ntsc)

            # asset 선언 범위를 넘지 않게 자른다. 세그먼트는 파일 경계에서
            # 잘리므로 정상 입력에서는 도달하지 않지만, files 를 외부에서
            # 조립해 duration 이 어긋난 경우 FCP/다빈치가 소스 범위를
            # 거부하는 것을 막는다.
            asset_end = asset_ranges[fi][1]
            if src_end_frame > asset_end:
                src_end_frame = asset_end
            clip_frames = src_end_frame - src_start_frame
            if clip_frames <= 0:
                continue

            asset_id = f"r{fi + 2}"
            label = seg.get("label", "")
            clip_name = f["name"]
            note = f"[{label}] {seg.get('reason', '')}" if label else seg.get("reason", "")

            clip_lines.append(
                f'        <asset-clip ref="{asset_id}" '
                f'offset="{_frames_str(record_frames, fps_int, ntsc)}" '
                f'start="{_frames_str(src_start_frame, fps_int, ntsc)}" '
                f'duration="{_frames_str(clip_frames, fps_int, ntsc)}" '
                f'name="{html.escape(clip_name)}" '
                f'tcFormat="{tc_format}">'
            )
            if note:
                clip_lines.append(
                    f'          <note>{html.escape(note)}</note>'
                )
            # 펀치인(디지털 줌) — 스토리 편집이 같은 앵글 연속 컷에 붙인다.
            # 다빈치가 import 시 Transform 으로 반영한다. (EDL 은 표현 불가)
            zoom = seg.get("zoom")
            if zoom and float(zoom) > 1.0:
                clip_lines.append(
                    f'          <adjust-transform scale="{float(zoom):.2f} {float(zoom):.2f}"/>'
                )
            clip_lines.append(f'        </asset-clip>')

            record_frames += clip_frames

    total_duration = _frames_str(record_frames - tc_start_frames, fps_int, ntsc)
    safe_project = html.escape(project_name)
    frame_duration = f"1001/{fps_int * 1000}s" if ntsc else f"1/{fps_int}s"
    tc_start = _frames_str(tc_start_frames, fps_int, ntsc)
    if tc_start_frames == 0:
        tc_start = "0s"
    rate_label = _NTSC_LABEL.get(fps_int, str(fps_int)) if ntsc else str(fps_int)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat{height}p{rate_label}" frameDuration="{frame_duration}" width="{width}" height="{height}" colorSpace="1-1-1 (Rec. 709)"/>
{chr(10).join(asset_lines)}
  </resources>
  <library>
    <event name="{safe_project}">
      <project name="{safe_project}">
        <sequence format="r1" duration="{total_duration}" tcStart="{tc_start}" tcFormat="{tc_format}" audioLayout="stereo" audioRate="48k">
          <spine>
{chr(10).join(clip_lines)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
    return xml
