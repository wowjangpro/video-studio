#!/usr/bin/env python3
"""BGM 영상 분석 스크립트 (Claude Vision)

장면 분석 + 음악 프롬프트 생성을 Claude 1회 호출로 통합 처리.
"""

import sys
import json
import os
import subprocess
import tempfile

import numpy as np
from PIL import Image


def emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False), flush=True)


def progress(stage: str, percent: int, message: str) -> None:
    emit({"type": "progress", "stage": stage, "percent": percent, "message": message})


def _build_env() -> dict:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def extract_frames(video_path: str, start: float, end: float) -> list[str]:
    """영상에서 키 프레임을 추출하여 임시 파일로 저장"""
    from moviepy import VideoFileClip

    clip = VideoFileClip(video_path)
    end_time = min(end, clip.duration)
    duration = end_time - start

    num_frames = min(4, max(2, int(duration / 5)))
    times = np.linspace(start, end_time - 0.1, num_frames)

    tmp_dir = tempfile.mkdtemp(prefix="video-studio-bgm-")
    paths = []

    for i, t in enumerate(times):
        frame = clip.get_frame(t)
        img = Image.fromarray(frame)
        img = img.resize((768, int(768 * img.height / img.width)))
        path = os.path.join(tmp_dir, f"frame_{i}.jpg")
        img.save(path, "JPEG", quality=85)
        paths.append(path)
        pct = 20 + int((i + 1) / num_frames * 30)
        progress("analyzing", pct, f"프레임 추출 중... ({i + 1}/{num_frames})")

    clip.close()
    return paths


def analyze_with_claude(frame_paths: list[str], preference: str = "") -> dict:
    """Claude Vision으로 장면 분석 + 음악 프롬프트 생성 (1회 호출)"""
    pref_section = ""
    if preference.strip():
        pref_section = f"\n사용자 음악 스타일 요청: \"{preference.strip()}\"\n이 요청을 반드시 반영하세요.\n"

    image_list = "\n".join(f"- {p}" for p in frame_paths)

    prompt = (
        f"다음 영상 프레임들을 Read 도구로 읽어서 분석하세요.\n\n"
        f"{image_list}\n\n"
        "이 영상의 배경음악을 만들기 위해 아래 두 가지를 작성하세요.\n\n"
        "1. SCENE DESCRIPTION (2~3문장, 영어)\n"
        "   영상의 분위기, 감정, 장소, 행동, 시간대를 설명하세요.\n\n"
        "2. MUSIC PROMPT (2~3문장, 영어)\n"
        "   AI 음악 생성기를 위한 프롬프트입니다.\n"
        "   장르, 분위기, 템포, 악기, 질감을 구체적으로 작성하세요.\n"
        "   예: 'Warm acoustic folk with fingerpicked guitar and soft piano. "
        "Gentle, nostalgic mood with a slow, swaying rhythm.'\n"
        f"{pref_section}\n"
        "아래 JSON 형식으로만 응답하세요:\n"
        '{"scene_description": "...", "music_prompt": "..."}'
    )

    cmd = [
        "claude", "-p",
        "--model", "sonnet",
        "--output-format", "json",
        "--no-session-persistence",
    ]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            env=_build_env(),
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        text = data.get("result", "")

        # JSON 추출
        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(text[json_start:json_end])
            if "scene_description" in parsed and "music_prompt" in parsed:
                return parsed

        # JSON 파싱 실패 시 전체 텍스트를 scene_description으로 사용
        return {"scene_description": text, "music_prompt": text}

    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"[analyze_claude] 오류: {e}", file=sys.stderr, flush=True)
        return None


def main():
    if len(sys.argv) < 5:
        emit({"type": "error", "message": "인자가 부족합니다: analyze_claude.py <video_path> <start> <end> [preference]"})
        sys.exit(1)

    video_path = sys.argv[1]
    range_start = float(sys.argv[2])
    range_end = float(sys.argv[3])
    preference = sys.argv[4] if len(sys.argv) > 4 else ""

    if not os.path.exists(video_path):
        emit({"type": "error", "message": f"영상 파일을 찾을 수 없습니다: {video_path}"})
        sys.exit(1)

    # Claude CLI 확인
    try:
        subprocess.run(["claude", "--version"], capture_output=True, timeout=10, env=_build_env())
    except Exception:
        emit({"type": "error", "message": "Claude CLI가 설치되어 있지 않습니다"})
        sys.exit(1)

    import shutil
    tmp_dir = None

    try:
        progress("analyzing", 10, "영상을 불러오는 중...")
        frame_paths = extract_frames(video_path, range_start, range_end)
        tmp_dir = os.path.dirname(frame_paths[0]) if frame_paths else None

        progress("analyzing", 55, "Claude가 영상을 분석하는 중...")
        result = analyze_with_claude(frame_paths, preference)

        if not result:
            emit({"type": "error", "message": "Claude 분석에 실패했습니다"})
            sys.exit(1)

        progress("analyzing", 100, "분석 완료")
        emit({
            "type": "analyzed",
            "scene_description": result["scene_description"],
            "music_prompt": result["music_prompt"],
        })

    except Exception as e:
        emit({"type": "error", "message": str(e)})
        sys.exit(1)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
