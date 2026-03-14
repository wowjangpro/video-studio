"""BGM 생성 스크립트

사용법:
  분석: python generate.py analyze <video_path> <range_start> <range_end> [preference]
  생성: python generate.py generate <video_path> <range_start> <range_end> <prompt> [count]

JSONL 출력 프로토콜:
  {"type": "progress", "stage": "analyzing"|"generating", "percent": N, "message": "..."}
  {"type": "analyzed", "scene_description": "...", "music_prompt": "..."}
  {"type": "complete", "bgm_paths": ["/path/to/output.wav"]}
  {"type": "error", "message": "에러 내용"}
"""

import json
import re
import sys
import os
import time
import base64
import io
import shutil
import httpx
import numpy as np
from PIL import Image

# ACE-Step 프로젝트를 Python 경로에 추가
ACESTEP_ROOT = os.path.join(os.path.dirname(__file__), "ACE-Step-1.5")
if ACESTEP_ROOT not in sys.path:
    sys.path.insert(0, ACESTEP_ROOT)

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
VISION_MODEL = "llama3.2-vision:11b"
TEXT_MODEL = "qwen2.5:14b"

# ACE-Step 핸들러 (lazy init)
_dit_handler = None
_llm_handler = None


def emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False), flush=True)


def progress(stage: str, percent: int, message: str) -> None:
    emit({"type": "progress", "stage": stage, "percent": percent, "message": message})


def frame_to_base64(frame: np.ndarray) -> str:
    """numpy 프레임을 base64 JPEG로 변환"""
    img = Image.fromarray(frame)
    img = img.resize((512, int(512 * img.height / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def analyze_scene(frames_b64: list[str]) -> str:
    """1단계: 비전 모델로 영상 장면 분석 (프레임별 분석 후 종합)"""
    frame_prompt = (
        "Describe this video frame briefly. Focus on mood, atmosphere, "
        "setting, and any actions. 1-2 sentences only."
    )

    descriptions = []
    for i, img_b64 in enumerate(frames_b64):
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": VISION_MODEL,
                "prompt": frame_prompt,
                "images": [img_b64],
                "stream": False,
                "options": {"temperature": 0.3},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        descriptions.append(resp.json()["response"].strip())

    # 여러 프레임 설명을 종합
    if len(descriptions) == 1:
        return descriptions[0]

    summary_prompt = (
        "These are descriptions of different frames from the same video:\n\n"
        + "\n".join(f"Frame {i+1}: {d}" for i, d in enumerate(descriptions))
        + "\n\nSummarize the overall scene in 2-3 sentences. "
        "Focus on the overall mood, atmosphere, pace, and setting. "
        "Respond with ONLY the summary."
    )

    resp = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": TEXT_MODEL,
            "prompt": summary_prompt,
            "stream": False,
            "options": {"temperature": 0.3},
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def scene_to_music_prompt(scene_description: str, user_preference: str = "") -> str:
    """2단계: 장면 설명을 음악 생성 프롬프트로 변환"""
    pref_section = ""
    if user_preference.strip():
        pref_section = (
            f"\nUSER'S MUSIC PREFERENCE (must respect this):\n"
            f"\"{user_preference.strip()}\"\n"
            "Incorporate the user's preference into the prompt. "
            "The scene sets the mood, but the user's preference determines the style.\n"
        )

    prompt = (
        "You are an expert at writing prompts for AI music generators.\n"
        "Given a video scene description, write a music prompt for background music.\n\n"
        "RULES:\n"
        "- Describe the desired music in 2-3 sentences with rich detail\n"
        "- Include genre, mood, tempo, instruments, and texture\n"
        "- Use natural, descriptive English\n"
        "- Focus on creating cinematic, professional-sounding background music\n\n"
        "GOOD examples:\n"
        "- 'Warm acoustic folk with fingerpicked guitar and soft piano. Gentle, nostalgic mood with a slow, swaying rhythm.'\n"
        "- 'Cinematic orchestral piece with sweeping strings and French horn. Epic and dramatic, building from quiet to powerful.'\n"
        "- 'Lo-fi jazz with mellow saxophone and brushed drums. Relaxed and dreamy atmosphere, slow tempo.'\n\n"
        f"Scene description: {scene_description}\n"
        f"{pref_section}\n"
        "Write ONLY the music prompt, nothing else."
    )

    resp = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": TEXT_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3},
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip().strip('"').strip("'")


def analyze_video(video_path: str, start: float, end: float, preference: str = "") -> str:
    """영상 구간에서 프레임을 추출하고 Ollama Vision으로 분석하여 장면 설명 반환"""
    from moviepy import VideoFileClip

    progress("analyzing", 10, "영상을 불러오는 중...")

    clip = VideoFileClip(video_path)
    end_time = min(end, clip.duration)
    duration = end_time - start

    progress("analyzing", 20, "키 프레임을 추출하는 중...")

    num_frames = min(4, max(2, int(duration / 5)))
    times = np.linspace(start, end_time - 0.1, num_frames)

    frames_b64 = []
    for i, t in enumerate(times):
        frame = clip.get_frame(t)
        frames_b64.append(frame_to_base64(frame))
        pct = 20 + int((i + 1) / num_frames * 30)
        progress("analyzing", pct, f"프레임 추출 중... ({i + 1}/{num_frames})")

    clip.close()

    progress("analyzing", 50, f"AI가 영상을 분석하는 중... ({VISION_MODEL})")
    scene_description = analyze_scene(frames_b64)

    progress("analyzing", 85, f"음악 프롬프트를 생성하는 중... ({TEXT_MODEL})")
    music_prompt = scene_to_music_prompt(scene_description, preference)

    progress("analyzing", 100, "분석 완료")

    return {"scene_description": scene_description, "music_prompt": music_prompt}


def translate_to_english(text: str) -> str:
    """한글이 포함된 텍스트를 Ollama로 영어 음악 프롬프트로 번역"""
    if not re.search(r"[가-힣]", text):
        return text

    progress("generating", 0, "스타일을 영어로 번역하는 중... (qwen2.5:7b)")

    resp = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": "qwen2.5:7b",
            "prompt": (
                "Translate the following Korean music style description to a natural English "
                "music prompt for an AI music generator. Keep it as 1-2 flowing sentences. "
                "Respond with ONLY the English translation.\n\n"
                f"{text}"
            ),
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def create_prompt_variations(base_prompt: str, count: int) -> list[str]:
    """기본 프롬프트에서 느낌이 조금씩 다른 변형을 생성"""
    if count <= 1:
        return [base_prompt]

    progress("generating", 2, f"프롬프트 변형을 생성하는 중... ({TEXT_MODEL})")

    resp = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": TEXT_MODEL,
            "prompt": (
                "You are creating prompt variations for an AI music generator.\n"
                f"Base prompt: \"{base_prompt}\"\n\n"
                f"Create {count} variations that keep the same overall mood but differ in:\n"
                "- Slightly different genre blend or instrument choices\n"
                "- Slightly different energy or texture\n\n"
                "RULES:\n"
                "- Each variation must be 2-3 sentences with rich detail\n"
                "- Keep the same mood as the original\n"
                "- Variations should be subtle, not dramatically different\n\n"
                f"Respond with EXACTLY {count} variations separated by '---'. "
                "No numbering, no other text."
            ),
            "stream": False,
            "options": {"temperature": 0.7},
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    raw = resp.json()["response"].strip()

    if "---" in raw:
        variations = [v.strip() for v in raw.split("---") if v.strip()]
    else:
        variations = [line.strip().strip('"').strip("'").lstrip("0123456789.-) ")
                      for line in raw.split("\n") if line.strip()]
    while len(variations) < count:
        variations.append(base_prompt)
    return variations[:count]


def _init_acestep():
    """ACE-Step DiT + LLM 핸들러를 초기화하고 캐시"""
    global _dit_handler, _llm_handler
    if _dit_handler is not None:
        return _dit_handler, _llm_handler

    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler

    progress("generating", 3, "ACE-Step DiT 모델을 로딩하는 중...")

    handler = AceStepHandler()
    status_msg, ok = handler.initialize_service(
        project_root=ACESTEP_ROOT,
        config_path="acestep-v15-turbo",
        device="auto",
    )
    if not ok:
        raise RuntimeError(f"ACE-Step 초기화 실패: {status_msg}")

    progress("generating", 7, "ACE-Step 5Hz LM을 로딩하는 중...")

    llm = LLMHandler()
    checkpoint_dir = os.path.join(ACESTEP_ROOT, "checkpoints")
    status_msg, ok = llm.initialize(
        checkpoint_dir=checkpoint_dir,
        lm_model_path="acestep-5Hz-lm-1.7B",
        backend="pt",
        device="auto",
    )
    if not ok:
        raise RuntimeError(f"ACE-Step LLM 초기화 실패: {status_msg}")

    _dit_handler = handler
    _llm_handler = llm
    return _dit_handler, _llm_handler


def generate_bgm(prompt: str, duration: float, output_paths: list[str]) -> None:
    """ACE-Step 직접 호출로 BGM 생성"""
    from acestep.inference import GenerationParams, GenerationConfig, generate_music

    count = len(output_paths)
    prompts = create_prompt_variations(prompt, count)

    handler, llm = _init_acestep()

    for i in range(count):
        label = f" ({i + 1}/{count})" if count > 1 else ""
        pct_base = int((i / count) * 85) + 10

        progress("generating", pct_base, f"BGM 생성 중{label}... (ACE-Step)")

        params = GenerationParams(
            task_type="text2music",
            caption=prompts[i],
            lyrics="[Instrumental]",
            instrumental=True,
            duration=duration,
            thinking=True,
            inference_steps=20,
            shift=3.0,
        )

        config = GenerationConfig(
            batch_size=1,
            audio_format="wav",
        )

        save_dir = os.path.dirname(output_paths[i])
        result = generate_music(handler, llm, params, config, save_dir=save_dir)

        if not result.success:
            raise RuntimeError(f"BGM 생성 실패: {result.error}")

        if not result.audios:
            raise RuntimeError("생성된 오디오 파일이 없습니다")

        src_path = result.audios[0]["path"]
        if src_path != output_paths[i]:
            shutil.move(src_path, output_paths[i])

        pct = pct_base + int(85 / count)
        progress("generating", min(pct, 98), f"BGM 생성 완료{label}")

    progress("generating", 100, "완료!")


def main():
    if len(sys.argv) < 2:
        emit({"type": "error", "message": "명령이 필요합니다: analyze 또는 generate"})
        sys.exit(1)

    command = sys.argv[1]

    try:
        if command == "analyze":
            if len(sys.argv) < 5:
                emit({"type": "error", "message": "인자가 부족합니다: analyze <video_path> <start> <end>"})
                sys.exit(1)

            video_path = sys.argv[2]
            range_start = float(sys.argv[3])
            range_end = float(sys.argv[4])
            preference = sys.argv[5] if len(sys.argv) > 5 else ""

            if not os.path.exists(video_path):
                emit({"type": "error", "message": f"영상 파일을 찾을 수 없습니다: {video_path}"})
                sys.exit(1)

            analysis = analyze_video(video_path, range_start, range_end, preference)
            emit({
                "type": "analyzed",
                "scene_description": analysis["scene_description"],
                "music_prompt": analysis["music_prompt"],
            })

        elif command == "generate":
            if len(sys.argv) < 5:
                emit({"type": "error", "message": "인자가 부족합니다: generate <video_path> <start> <end> <prompt>"})
                sys.exit(1)

            video_path = sys.argv[2]
            range_start = float(sys.argv[3])
            range_end = float(sys.argv[4])
            prompt = translate_to_english(sys.argv[5])
            count = int(sys.argv[6]) if len(sys.argv) > 6 else 1

            video_dir = os.path.dirname(video_path)
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            bgm_dir = os.path.join(video_dir, "bgm")
            os.makedirs(bgm_dir, exist_ok=True)

            output_paths = []
            for _ in range(count):
                base_path = os.path.join(bgm_dir, f"{video_name}_bgm")
                output_path = f"{base_path}.wav"
                counter = 1
                while os.path.exists(output_path) or output_path in output_paths:
                    output_path = f"{base_path}_{counter}.wav"
                    counter += 1
                output_paths.append(output_path)

            duration = range_end - range_start
            generate_bgm(prompt, duration, output_paths)
            emit({"type": "complete", "bgm_paths": output_paths})

        else:
            emit({"type": "error", "message": f"알 수 없는 명령: {command}"})
            sys.exit(1)

    except Exception as e:
        emit({"type": "error", "message": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
