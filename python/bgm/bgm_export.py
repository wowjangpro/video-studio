#!/usr/bin/env python3
"""영상별 BGM 큐(어느 구간에 어떤 곡을 어디서부터) 기록과 다빈치 내보내기

큐는 라이브러리의 usage/ 에 영상마다 JSON 으로 쌓인다. 곡 위치는 라이브러리 기준 상대 경로라
라이브러리 폴더를 옮겨도 그대로 쓸 수 있다. 영상 경로는 절대 경로로 둔다(라이브러리 밖이다).

사용법 (bgm-venv):
    bgm_export.py cues   <root> <video>
    bgm_export.py add    <root> <video> '<cue JSON>'   {"trackId", "rangeStart", "rangeEnd", "offsetSec"}
    bgm_export.py remove <root> <video> <cueId>
    bgm_export.py export <root> <video> [outDir]       → <영상이름>_bgm.fcpxml · <영상이름>_bgm_출처.txt

FCPXML 구성 (다빈치: File > Import > Timeline > Final Cut Pro XML)
- 렌더링한 영상 하나를 주 스토리라인(V1/A1)에, 큐마다 곡을 그 구간 위치의 오디오 레인(-1, -2 …)에 붙인다.
- 곡은 offsetSec 부터 구간 길이만큼 쓴다. 겹치는 큐는 다른 레인으로 나눈다.
- 영상에 박힌 시작 타임코드(다빈치 렌더는 보통 01:00:00:00)를 asset 시작과 타임라인 시작에 그대로 쓴다.
  그래야 다빈치가 미디어를 같은 파일로 알아본다.
"""
import hashlib
import html
import json
import os
import subprocess
import sys
import uuid
from urllib.parse import quote

from music_library import audio_base, audio_file, connect, emit, now_iso, read_manifest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "autocut"))
from edl_export import resolve_tc_base, timecode_to_frames  # noqa: E402

_NTSC_LABEL = {24: "2398", 30: "2997", 60: "5994"}


# ── 큐 기록 ──

def usage_path(root: str, video: str) -> str:
    video = os.path.abspath(video)
    stem = os.path.splitext(os.path.basename(video))[0]
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in stem).strip()[:60] or "video"
    digest = hashlib.sha1(video.encode("utf-8")).hexdigest()[:8]
    return os.path.join(root, "usage", f"{safe}_{digest}.json")


def load_usage(root: str, video: str) -> dict:
    p = usage_path(root, video)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"video": os.path.abspath(video), "cues": []}


def save_usage(root: str, data: dict) -> None:
    data["updatedAt"] = now_iso()
    p = usage_path(root, data["video"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def cue_view(root: str, cue: dict) -> dict:
    """화면에 줄 큐 — 음원 절대 경로를 붙인다."""
    out = dict(cue)
    out["audioPath"] = audio_file(audio_base(root), cue.get("audioRel") or "")
    return out


def add_cue(root: str, video: str, req: dict) -> dict:
    conn = connect(root)
    try:
        t = conn.execute("SELECT id, title, artist, audio_path, license_type, attribution_text, duration_sec "
                         "FROM tracks WHERE id = ?", (req["trackId"],)).fetchone()
    finally:
        conn.close()
    if not t or not t["audio_path"]:
        raise ValueError("라이브러리에 음원이 없는 곡입니다")
    start, end = float(req["rangeStart"]), float(req["rangeEnd"])
    if end <= start:
        raise ValueError("구간 끝이 시작보다 뒤여야 합니다")
    cue = {
        "cueId": uuid.uuid4().hex[:12],
        "trackId": t["id"], "title": t["title"], "artist": t["artist"], "audioRel": t["audio_path"],
        "rangeStart": round(start, 3), "rangeEnd": round(end, 3),
        "offsetSec": round(max(0.0, float(req.get("offsetSec") or 0)), 3),
        "licenseType": t["license_type"], "attributionText": t["attribution_text"],
        "addedAt": now_iso(),
    }
    data = load_usage(root, video)
    data["cues"].append(cue)
    data["cues"].sort(key=lambda c: c["rangeStart"])
    save_usage(root, data)
    return data


def remove_cue(root: str, video: str, cue_id: str) -> dict:
    data = load_usage(root, video)
    data["cues"] = [c for c in data["cues"] if c["cueId"] != cue_id]
    save_usage(root, data)
    return data


# ── 영상 정보 ──

def _rate(v: str) -> float:
    if not v or v in ("0/0", "0"):
        return 0.0
    if "/" in v:
        n, d = v.split("/")
        return float(n) / float(d) if float(d) else 0.0
    return float(v)


def probe_video(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "quiet", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True)
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise ValueError(f"영상 스트림이 없습니다: {path}")
    fps = _rate(v.get("avg_frame_rate", "")) or _rate(v.get("r_frame_rate", "")) or 24.0
    tc = ""
    for s in streams:
        tc = (s.get("tags") or {}).get("timecode", "")
        if tc:
            break
    tc = tc or (data.get("format", {}).get("tags") or {}).get("timecode", "")
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "fps": fps,
        "width": int(v.get("width") or 1920),
        "height": int(v.get("height") or 1080),
        "duration": float(data.get("format", {}).get("duration") or v.get("duration") or 0),
        "timecode": tc,
        "audio_rate": int((a or {}).get("sample_rate") or 48000),
        "audio_channels": int((a or {}).get("channels") or 2),
        "has_audio": a is not None,
    }


def probe_audio(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "quiet", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True)
    data = json.loads(out.stdout or "{}")
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {"duration": float(data.get("format", {}).get("duration") or 0),
            "rate": int(a.get("sample_rate") or 44100), "channels": int(a.get("channels") or 2)}


# ── FCPXML ──

def assign_lanes(cues: list[dict]) -> list[int]:
    """겹치는 큐를 서로 다른 레인(-1, -2, …)에 놓는다."""
    lane_end: list[float] = []
    lanes = []
    for c in cues:
        for i, end in enumerate(lane_end):
            if c["rangeStart"] >= end - 1e-6:
                lane_end[i] = c["rangeEnd"]
                lanes.append(-(i + 1))
                break
        else:
            lane_end.append(c["rangeEnd"])
            lanes.append(-len(lane_end))
    return lanes


def build_fcpxml(root: str, data: dict) -> str:
    video = data["video"]
    info = probe_video(video)
    fps_int, actual_fps = resolve_tc_base(info["fps"])
    ntsc = abs(actual_fps - fps_int) > 0.001

    def frames(sec: float) -> int:
        return round(max(0.0, sec) * actual_fps)

    def t(fr: int) -> str:
        return f"{fr * 1001}/{fps_int * 1000}s" if ntsc else f"{fr}/{fps_int}s"

    tc_frames, drop = 0, False
    if info["timecode"]:
        tc = info["timecode"] if ntsc else info["timecode"].replace(";", ":")
        tc_frames, drop = timecode_to_frames(tc, fps_int)
    tc_format = "DF" if drop else "NDF"
    video_frames = frames(info["duration"])
    rate_label = _NTSC_LABEL.get(fps_int, str(fps_int)) if ntsc else str(fps_int)
    frame_duration = f"1001/{fps_int * 1000}s" if ntsc else f"1/{fps_int}s"

    def url(p: str) -> str:
        return "file://" + quote(os.path.abspath(p), safe="/")

    name = os.path.splitext(os.path.basename(video))[0]
    base = audio_base(root)
    if data["cues"] and not os.path.isdir(base):
        raise ValueError(f"음원 폴더가 보이지 않습니다: {base} (NAS 연결을 확인하세요)")
    assets = [
        f'    <asset id="r2" name="{html.escape(name)}" src="{url(video)}" start="{t(tc_frames) if tc_frames else "0s"}" '
        f'duration="{t(video_frames)}" hasVideo="1" hasAudio="{1 if info["has_audio"] else 0}" format="r1" '
        f'audioSources="1" audioChannels="{info["audio_channels"]}" audioRate="{info["audio_rate"]}"/>'
    ]
    asset_ids: dict[str, str] = {}
    track_frames: dict[str, int] = {}
    connected = []
    cues = sorted(data["cues"], key=lambda c: c["rangeStart"])
    for cue, lane in zip(cues, assign_lanes(cues)):
        path = audio_file(base, cue.get("audioRel") or "")
        if not path or not os.path.isfile(path):
            raise ValueError(f"음원 파일이 없습니다: {cue.get('title')} ({cue.get('audioRel')})")
        if cue["trackId"] not in asset_ids:
            a = probe_audio(path)
            aid = f"r{len(asset_ids) + 3}"
            asset_ids[cue["trackId"]] = aid
            track_frames[cue["trackId"]] = frames(a["duration"])
            label = html.escape(f"{cue['artist']} - {cue['title']}")
            assets.append(
                f'    <asset id="{aid}" name="{label}" src="{url(path)}" '
                f'start="0s" duration="{t(frames(a["duration"]))}" hasAudio="1" '
                f'audioSources="1" audioChannels="{a["channels"]}" audioRate="{a["rate"]}"/>')
        start = min(cue["rangeStart"], info["duration"])
        end = min(cue["rangeEnd"], info["duration"])
        src_start = frames(cue["offsetSec"])
        # 곡이 구간보다 짧거나 시작 지점이 늦으면 곡 끝에서 자른다 (원본 범위를 넘는 클립은 다빈치·FCP 가 거부)
        dur = min(frames(end) - frames(start), track_frames[cue["trackId"]] - src_start)
        if dur <= 0:
            continue
        connected.append(
            f'          <asset-clip ref="{asset_ids[cue["trackId"]]}" lane="{lane}" '
            f'offset="{t(tc_frames + frames(start))}" start="{t(src_start)}" '
            f'duration="{t(dur)}" name="{html.escape(cue["title"])}" audioRole="music"/>')

    project = html.escape(f"{name}_bgm")
    seq_start = t(tc_frames) if tc_frames else "0s"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat{info['height']}p{rate_label}" frameDuration="{frame_duration}" width="{info['width']}" height="{info['height']}" colorSpace="1-1-1 (Rec. 709)"/>
{chr(10).join(assets)}
  </resources>
  <library>
    <event name="{project}">
      <project name="{project}">
        <sequence format="r1" duration="{t(video_frames)}" tcStart="{seq_start}" tcFormat="{tc_format}" audioLayout="stereo" audioRate="48k">
          <spine>
            <asset-clip ref="r2" offset="{seq_start}" start="{seq_start}" duration="{t(video_frames)}" name="{html.escape(name)}" tcFormat="{tc_format}">
{chr(10).join(connected)}
            </asset-clip>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def build_credits(data: dict) -> str:
    seen, cc, others = set(), [], []
    for c in sorted(data["cues"], key=lambda c: c["rangeStart"]):
        if c["trackId"] in seen:
            continue
        seen.add(c["trackId"])
        if c.get("licenseType") == "attribution_required":
            cc.append(c.get("attributionText") or f"{c['title']} by {c['artist']} (출처 문구 없음 — 보관함에서 확인 필요)")
        else:
            others.append(f"{c['title']} - {c['artist']}")
    lines = ["[유튜브 설명란에 반드시 넣을 출처 — CC BY 곡]", ""]
    lines += ["\n\n".join(cc) if cc else "(없음 — 이 영상에 쓴 곡은 모두 출처 표기가 필요 없습니다)", ""]
    lines += ["[참고: 출처 표기가 필요 없는 곡 (YouTube 오디오 보관함)]", ""]
    lines += others or ["(없음)"]
    return "\n".join(lines) + "\n"


def export(root: str, video: str, out_dir: str | None) -> dict:
    data = load_usage(root, video)
    if not data["cues"]:
        raise ValueError("이 영상에 넣은 곡이 없습니다")
    out_dir = out_dir or os.path.dirname(os.path.abspath(video))
    stem = os.path.splitext(os.path.basename(video))[0]
    fcpxml_path = os.path.join(out_dir, f"{stem}_bgm.fcpxml")
    credits_path = os.path.join(out_dir, f"{stem}_bgm_출처.txt")
    # 먼저 다 만든 뒤에 쓴다 — 만들다 실패하면(음원 폴더 끊김 등) 지난번 결과 파일이 빈 파일로 덮였다 (2026-11-22)
    fcpxml = build_fcpxml(root, data)
    credits = build_credits(data)
    with open(fcpxml_path, "w", encoding="utf-8") as f:
        f.write(fcpxml)
    with open(credits_path, "w", encoding="utf-8") as f:
        f.write(credits)
    data["exportedAt"] = now_iso()
    save_usage(root, data)
    return {"fcpxmlPath": fcpxml_path, "creditsPath": credits_path, "credits": credits}


def main() -> None:
    if len(sys.argv) < 4:
        emit({"type": "error", "message": "사용법: bgm_export.py cues|add|remove|export <root> <video> [...]"})
        sys.exit(1)
    cmd, root, video = sys.argv[1], os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
    try:
        if read_manifest(root) is None:
            raise ValueError(f"음악 라이브러리 폴더가 아닙니다: {root}")
        if cmd == "cues":
            data = load_usage(root, video)
        elif cmd == "add":
            data = add_cue(root, video, json.loads(sys.argv[4]))
        elif cmd == "remove":
            data = remove_cue(root, video, sys.argv[4])
        elif cmd == "export":
            res = export(root, video, sys.argv[4] if len(sys.argv) > 4 else None)
            emit({"type": "exported", **res})
            return
        else:
            raise ValueError(f"모르는 명령: {cmd}")
        emit({"type": "cues", "cues": [cue_view(root, c) for c in data["cues"]]})
    except Exception as e:  # noqa: BLE001
        emit({"type": "error", "message": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
