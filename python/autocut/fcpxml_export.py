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


def _to_fraction(seconds: float, fps: int) -> str:
    """초 → FCPXML 시간 표기 (frames/rate s)"""
    if seconds < 0:
        seconds = 0
    frames = round(seconds * fps)
    return f"{frames}/{fps}s"


def _file_url(path: str) -> str:
    """절대 경로 → file:// URL (한글/공백 인코딩)"""
    abs_path = os.path.abspath(path)
    return "file://" + quote(abs_path, safe="/")


def generate_fcpxml(
    segments: list[dict],
    files: list[dict],
    fps: int = 24,
    project_name: str = "Video Studio",
    width: int = 3840,
    height: int = 2160,
) -> str:
    """KEEP 세그먼트와 파일 목록으로 FCPXML 생성.

    Args:
        segments: [{"globalStart", "globalEnd", "label", ...}]
        files: [{"path", "name", "duration", "offset"}]
        fps: timeline frame rate (기본 24)
        project_name: 프로젝트/이벤트 이름
        width, height: timeline 해상도 (기본 4K)

    Returns:
        FCPXML 문자열
    """
    if not segments or not files:
        return ""

    file_offsets = [f["offset"] for f in files]

    # asset 정의 — start는 영상 metadata TC 기준 (다빈치 매칭용)
    asset_lines = []
    for i, f in enumerate(files):
        asset_id = f"r{i + 2}"  # r1은 format이라 r2부터
        name = os.path.splitext(f["name"])[0]
        url = _file_url(f["path"])
        dur = _to_fraction(f["duration"], fps)
        tc_seconds = f.get("tc_seconds", 0.0)
        start_tc = _to_fraction(tc_seconds, fps) if tc_seconds > 0 else "0s"
        asset_lines.append(
            f'    <asset id="{asset_id}" name="{html.escape(name)}" '
            f'src="{url}" start="{start_tc}" duration="{dur}" '
            f'hasVideo="1" hasAudio="1" format="r1" '
            f'audioSources="1" audioChannels="2" audioRate="48000"/>'
        )

    # asset-clip 생성 (timeline 위치별)
    clip_lines = []
    record_offset = 0.0

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
            clip_duration = clip_end - clip_start

            # asset의 start origin이 영상 metadata TC이므로,
            # asset-clip의 start는 (metadata TC + 영상 내 위치) 가 되어야 한다.
            tc_seconds = f.get("tc_seconds", 0.0)
            source_start = tc_seconds + local_start

            asset_id = f"r{fi + 2}"
            label = seg.get("label", "")
            clip_name = f["name"]
            note = f"[{label}] {seg.get('reason', '')}" if label else seg.get("reason", "")

            clip_lines.append(
                f'        <asset-clip ref="{asset_id}" '
                f'offset="{_to_fraction(record_offset, fps)}" '
                f'start="{_to_fraction(source_start, fps)}" '
                f'duration="{_to_fraction(clip_duration, fps)}" '
                f'name="{html.escape(clip_name)}" '
                f'tcFormat="NDF">'
            )
            if note:
                clip_lines.append(
                    f'          <note>{html.escape(note)}</note>'
                )
            clip_lines.append(f'        </asset-clip>')

            record_offset += clip_duration

    total_duration = _to_fraction(record_offset, fps)
    safe_project = html.escape(project_name)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat{height}p{fps}" frameDuration="1/{fps}s" width="{width}" height="{height}" colorSpace="1-1-1 (Rec. 709)"/>
{chr(10).join(asset_lines)}
  </resources>
  <library>
    <event name="{safe_project}">
      <project name="{safe_project}">
        <sequence format="r1" duration="{total_duration}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
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
