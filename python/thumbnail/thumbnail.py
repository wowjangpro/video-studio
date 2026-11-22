#!/usr/bin/env python3
"""유튜브 썸네일 5장 + 제목·설명 생성 (원본 촬영 폴더 기준)

  thumbnail.py load     <folder>
  thumbnail.py generate [--synopsis TEXT] -- <folder>
  thumbnail.py compose  <folder> <id> <text_json>
  thumbnail.py select   <folder> <id>

흐름 (generate)
  1. 원본 폴더를 읽는다. AI 편집 탭이 남긴 `autocut/analysis.json` (10초 창마다 비전 설명)이 있으면
     그것을, 없으면 클립 전체에서 고르게 뽑은 프레임 밀착 인화지(contact sheet)를 쓴다.
  2. Claude 가 시놉시스·스토리 요약·장면 설명을 읽고 서로 다른 썸네일 콘셉트 5개와 후보 장면을 고른다.
  3. 후보 장면마다 원본에서 프레임 몇 장을 뽑아 가장 선명한 것을 남긴다.
  4. Claude 가 후보 프레임을 직접 보고 콘셉트마다 한 장을 골라 썸네일 문구·제목·설명·해시태그를 쓴다.
  5. Pillow 로 1280×720 썸네일을 합성한다 (문구 없는 원본 프레임도 함께 저장).

결과는 `<folder>/thumbnail/` 에 둔다: thumb_N.jpg, thumb_N_raw.jpg, result.json,
고른 안은 selected.jpg · selected.txt.
"""

import argparse
import bisect
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "autocut"))
from claude_client import DEFAULT_MODEL, call_claude_text, check_claude_available  # noqa: E402


OUT_DIRNAME = "thumbnail"
RESULT_FILE = "result.json"
THUMB_W, THUMB_H = 1280, 720
CONCEPT_COUNT = 5
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".webm")

POSITIONS = ["top-left", "top-right", "top-center", "bottom-left", "bottom-right", "bottom-center"]
COLORS = {
    "yellow": (255, 221, 0),
    "red": (255, 59, 48),
    "orange": (255, 140, 0),
    "cyan": (0, 214, 255),
    "green": (52, 230, 120),
    "white": (255, 255, 255),
}
# 사용자 맥에 깔린 굵은 한글 글꼴. 없으면 Apple SD Gothic Neo Heavy 로 대신한다.
FONTS = {
    "dohyeon": ("~/Library/Fonts/BMDOHYEON_otf.otf", 0),
    "hanna": ("~/Library/Fonts/BMHANNAProOTF.otf", 0),
    "ssurround": ("~/Library/Fonts/Cafe24Ssurround-v2.0.otf", 0),
    "gothic": ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 16),
}


# ── 출력 ──

def emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False), flush=True)


def progress(percent: int, message: str) -> None:
    emit({"type": "progress", "stage": "generating", "percent": percent, "message": message})


def log(msg: str) -> None:
    print(f"[thumbnail] {msg}", file=sys.stderr, flush=True)


def fail(message: str) -> None:
    emit({"type": "error", "message": message})
    sys.exit(1)


def fmt_time(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── 원본 폴더 읽기 ──

def out_dir(folder: str) -> str:
    return os.path.join(folder, OUT_DIRNAME)


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def load_result(folder: str) -> dict | None:
    path = os.path.join(out_dir(folder), RESULT_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_result(folder: str, result: dict) -> None:
    os.makedirs(out_dir(folder), exist_ok=True)
    path = os.path.join(out_dir(folder), RESULT_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return 0.0


def load_analysis(folder: str) -> dict | None:
    """AI 편집 탭의 장면 분석. 원본 클립이 모두 그대로 있을 때만 쓴다."""
    path = os.path.join(folder, "autocut", "analysis.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    files = data.get("files") or []
    windows = data.get("windows") or []
    if not files or not windows:
        return None
    if not all(os.path.exists(os.path.join(folder, f["name"])) for f in files):
        log("analysis.json 의 클립 일부가 폴더에 없어 장면 분석을 쓰지 않습니다")
        return None
    return data


def scan_clips(folder: str, analysis: dict | None) -> list[dict]:
    """[{name, path, duration, offset}] — offset 은 전체 이어 붙인 시간축의 시작점"""
    if analysis:
        names = [(f["name"], float(f["duration"])) for f in analysis["files"]]
    else:
        names = []
        for name in sorted(os.listdir(folder)):
            if name.startswith(".") or not name.lower().endswith(VIDEO_EXTS):
                continue
            names.append((name, probe_duration(os.path.join(folder, name))))
    clips, offset = [], 0.0
    for name, dur in names:
        if dur <= 0:
            continue
        clips.append({"name": name, "path": os.path.join(folder, name), "duration": dur, "offset": offset})
        offset += dur
    return clips


def locate(clips: list[dict], global_t: float) -> tuple[dict, float]:
    """전체 시간축의 한 시점 → (클립, 클립 안 시각)"""
    starts = [c["offset"] for c in clips]
    i = max(0, bisect.bisect_right(starts, global_t) - 1)
    clip = clips[i]
    local = min(max(0.0, global_t - clip["offset"]), max(0.0, clip["duration"] - 0.5))
    return clip, local


def find_synopsis(folder: str) -> str:
    return read_text(os.path.join(folder, "autocut", "synopsis.md"))


def find_story_summary(folder: str) -> str:
    """가장 최근 스토리 편집 설계의 한 줄 요약·훅·막 제목"""
    paths = glob.glob(os.path.join(folder, "autocut", "*_story.json"))
    paths = [p for p in paths if not os.path.basename(p).startswith("._")]
    if not paths:
        return ""
    latest = max(paths, key=os.path.getmtime)
    try:
        with open(latest, encoding="utf-8") as f:
            story = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    lines = []
    if story.get("logline"):
        lines.append(f"한 줄 요약: {story['logline']}")
    hook = story.get("hook")
    if isinstance(hook, dict) and hook.get("why"):
        lines.append(f"오프닝 훅: {hook['why']}")
    elif isinstance(hook, str):
        lines.append(f"오프닝 훅: {hook}")
    for act in story.get("acts") or []:
        if isinstance(act, dict) and act.get("title"):
            lines.append(f"- 막: {act['title']}")
    return "\n".join(lines)


# ── 프레임 추출 ──

def extract_frame(path: str, t: float, width: int = 1920) -> Image.Image | None:
    """원본 한 시점의 프레임 (PNG 파이프 → RGB). 4K 원본도 키프레임 탐색이라 1초 안팎이다."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
             "-vf", f"scale={width}:-2,format=rgb24", "-f", "image2pipe", "-c:v", "png", "-"],
            capture_output=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    from io import BytesIO
    try:
        return Image.open(BytesIO(proc.stdout)).convert("RGB")
    except OSError:
        return None


def frame_quality(img: Image.Image) -> float:
    """선명도(라플라시안 분산) × 노출 벌점. 흔들리거나 너무 어둡거나 하얗게 날아간 프레임을 거른다."""
    g = np.asarray(img.convert("L").resize((480, 270)), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    sharp = float(lap.var())
    mean = float(g.mean()) / 255
    exposure = 1.0 - min(1.0, abs(mean - 0.5) / 0.5) ** 2
    clipped = float(((g < 8) | (g > 247)).mean())
    return sharp * (0.3 + 0.7 * exposure) * (1.0 - clipped)


def best_frame(clip: dict, start: float, end: float, samples: int = 4) -> tuple[Image.Image, float] | None:
    """구간 안 몇 시점 중 가장 좋은 프레임"""
    span = max(0.0, end - start)
    if span < 1:
        times = [start]
    else:
        times = [start + span * (i + 0.5) / samples for i in range(samples)]
    best = None
    for t in times:
        t = min(max(0.0, t), max(0.0, clip["duration"] - 0.5))
        img = extract_frame(clip["path"], t)
        if img is None:
            continue
        q = frame_quality(img)
        if best is None or q > best[0]:
            best = (q, img, t)
    return (best[1], best[2]) if best else None


# ── Claude 1단계: 콘셉트와 후보 장면 ──

CONCEPT_GUIDE = (
    "당신은 한국 캠핑·아웃도어 유튜브 채널의 썸네일 기획자입니다. 원본 촬영 전체를 보고 "
    f"서로 확연히 다른 썸네일 콘셉트 {CONCEPT_COUNT}개를 정하고, 콘셉트마다 썸네일 배경으로 쓸 후보 장면을 고르세요.\n\n"
    "좋은 썸네일 콘셉트:\n"
    "- 이 영상만의 사건·갈등이 한눈에 보이는 것 (시놉시스의 '가장 보여주고 싶은 것'을 우선)\n"
    "- 표정·몸짓이 살아 있는 인물 장면, 결과물이 완성된 장면, 분위기 있는 풍경·불·음식 장면처럼 시각적으로 강한 것\n"
    "- 클릭하고 싶게 만드는 궁금증 (단, 영상에 없는 일을 꾸며 내지 않는다)\n"
    f"- {CONCEPT_COUNT}개는 각기 다른 각도 (예: 사건/고생, 감성/풍경, 결과/성취, 인물/표정, 반전/대비)\n\n"
    "후보 장면 고르기:\n"
    "- 장면 설명만 보고 고르므로, 한 콘셉트에 후보를 4개씩 넉넉히 고른다 (서로 다른 시점)\n"
    "- 클로즈업·미디엄 샷처럼 피사체가 크게 보이고, 너무 어둡지 않은(밝기 0.2 미만은 피함) 장면을 우선한다\n"
    "- 사용 불가로 표시된 장면은 이미 뺐다\n"
)

CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "minItems": CONCEPT_COUNT,
            "maxItems": CONCEPT_COUNT,
            "items": {
                "type": "object",
                "properties": {
                    "angle": {"type": "string", "description": "콘셉트 이름 (짧게, 예: '입으로 텐트 세우기')"},
                    "idea": {"type": "string", "description": "썸네일이 무엇을 보여 주고 어떤 궁금증을 주는지 1~2문장"},
                    "candidates": {"type": "array", "minItems": 2, "maxItems": 5, "items": {"type": "string"},
                                   "description": "후보 장면 번호 (예: 'W166' 또는 'F12')"},
                },
                "required": ["angle", "idea", "candidates"],
            },
        }
    },
    "required": ["concepts"],
}


def context_block(synopsis: str, story: str, clips: list[dict]) -> str:
    total = sum(c["duration"] for c in clips)
    parts = [f"## 촬영 개요\n원본 클립 {len(clips)}개, 총 {fmt_time(total)}"]
    parts.append("## 촬영자 시놉시스\n" + (synopsis or "(없음 — 장면 설명만으로 판단)"))
    if story:
        parts.append("## AI 편집이 설계한 이야기 (참고)\n" + story)
    return "\n\n".join(parts)


def window_listing(analysis: dict) -> tuple[str, dict[str, tuple[float, float]]]:
    lines, ranges = [], {}
    for i, w in enumerate(analysis["windows"]):
        if str(w.get("usable", "")).lower() == "no":
            continue
        desc = (w.get("desc") or "").replace("\n", " ")[:80]
        speech = (w.get("transcript") or "").replace("\n", " ")[:40]
        bright = w.get("brightness")
        line = f"W{i} {fmt_time(w['globalStart'])} [{w.get('label', '')}/{w.get('shot', '')}"
        if isinstance(bright, (int, float)):
            line += f" 밝기{bright:.2f}"
        line += f"] {desc}"
        if speech:
            line += f" 「{speech}」"
        lines.append(line)
        ranges[f"W{i}"] = (float(w["globalStart"]), float(w["globalEnd"]))
    return "\n".join(lines), ranges


def plan_from_windows(analysis: dict, ctx: str) -> tuple[list[dict], dict]:
    listing, ranges = window_listing(analysis)
    prompt = (
        CONCEPT_GUIDE
        + "\n" + ctx
        + "\n\n## 장면 목록 (10초 단위, 번호 시각 [동작/샷 밝기] 설명 「말소리」)\n"
        + listing
        + "\n\n후보 번호는 위 목록의 W 번호를 그대로 쓰세요."
    )
    log(f"1단계 프롬프트 {len(prompt):,}자")
    text = call_claude_text(prompt, json_schema=CONCEPT_SCHEMA, timeout=900, max_retries=1)
    return parse_concepts(text), ranges


def make_contact_sheets(frames: list[tuple[str, Image.Image, float]], tmp: str, per_sheet: int = 12) -> list[str]:
    """번호를 박은 밀착 인화지 (가로 4칸)"""
    cols, cw, ch = 4, 480, 270
    font = load_font("gothic", 30)
    sheets = []
    for s in range(0, len(frames), per_sheet):
        chunk = frames[s:s + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cw, rows * ch), (0, 0, 0))
        draw = ImageDraw.Draw(sheet)
        for k, (fid, img, _) in enumerate(chunk):
            x, y = (k % cols) * cw, (k // cols) * ch
            sheet.paste(img.resize((cw, ch)), (x, y))
            draw.rectangle([x, y, x + 84, y + 40], fill=(0, 0, 0))
            draw.text((x + 8, y + 4), fid, font=font, fill=(255, 221, 0))
        path = os.path.join(tmp, f"sheet_{len(sheets) + 1}.jpg")
        sheet.save(path, "JPEG", quality=85)
        sheets.append(path)
    return sheets


def plan_from_sampling(clips: list[dict], ctx: str, tmp: str) -> tuple[list[dict], dict]:
    """장면 분석이 없을 때: 촬영 전체에서 고르게 프레임을 뽑아 Claude 가 직접 본다"""
    total = sum(c["duration"] for c in clips)
    count = 48
    step = total / count
    points = [step * (i + 0.5) for i in range(count)]
    frames, ranges = [], {}

    def grab(i_t):
        i, t = i_t
        clip, local = locate(clips, t)
        return i, t, extract_frame(clip["path"], local, width=960)

    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, t, img in pool.map(grab, list(enumerate(points))):
            done += 1
            progress(5 + int(done / count * 20), f"촬영 전체 훑어보는 중... ({done}/{count})")
            if img is None:
                continue
            fid = f"F{i + 1}"
            frames.append((fid, img, t))
            ranges[fid] = (max(0.0, t - step / 2), t + step / 2)
    if not frames:
        fail("원본 영상에서 프레임을 읽지 못했습니다")
    sheets = make_contact_sheets(frames, tmp)
    sheet_list = "\n".join(f"- {p}" for p in sheets)
    times = "\n".join(f"{fid} {fmt_time(t)}" for fid, _, t in frames)
    prompt = (
        CONCEPT_GUIDE
        + "\n" + ctx
        + "\n\n## 촬영 전체에서 고르게 뽑은 프레임\n"
        + "아래 밀착 인화지 이미지를 Read 도구로 모두 읽으세요. 각 칸 왼쪽 위 노란 글씨가 프레임 번호입니다.\n"
        + sheet_list
        + "\n\n프레임 번호와 촬영 시각:\n" + times
        + "\n\n후보 번호는 F 번호를 쓰세요. 후보 주변 구간에서 다시 프레임을 뽑아 고르니, 비슷한 장면이 있을 법한 번호면 됩니다."
    )
    progress(26, "Claude 가 프레임을 보고 썸네일 콘셉트를 정하는 중... (몇 분 걸립니다)")
    text = call_claude_text(prompt, json_schema=CONCEPT_SCHEMA, timeout=900, max_retries=1)
    return parse_concepts(text), ranges


def parse_concepts(text: str) -> list[dict]:
    if not text:
        fail("Claude 가 썸네일 콘셉트를 만들지 못했습니다 (응답 없음)")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}") + 1
        try:
            data = json.loads(text[start:end])
        except (json.JSONDecodeError, ValueError):
            fail("Claude 응답을 읽지 못했습니다 (콘셉트)")
    concepts = data.get("concepts") or []
    if not concepts:
        fail("Claude 가 썸네일 콘셉트를 만들지 못했습니다")
    return concepts[:CONCEPT_COUNT]


# ── Claude 2단계: 프레임 고르고 문구·제목·설명 쓰기 ──

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "thumbnails": {
            "type": "array",
            "minItems": 1,
            "maxItems": CONCEPT_COUNT,
            "items": {
                "type": "object",
                "properties": {
                    "concept": {"type": "integer", "description": "콘셉트 번호 (1부터)"},
                    "frame": {"type": "string", "description": "고른 후보 프레임 번호 (예: '2-3')"},
                    "headline": {"type": "string", "description": "썸네일 큰 문구. 한 줄 8자 안팎, 최대 2줄 (줄바꿈은 \\n)"},
                    "subline": {"type": "string", "description": "작은 보조 문구 (없으면 빈 문자열), 14자 이내"},
                    "highlight": {"type": "string", "description": "headline 안에서 강조색으로 칠할 단어 (headline 에 그대로 있는 부분 문자열, 없으면 빈 문자열)"},
                    "position": {"type": "string", "enum": POSITIONS, "description": "문구 위치 — 피사체(얼굴·핵심 물체)를 가리지 않는 빈 쪽"},
                    "color": {"type": "string", "enum": list(COLORS), "description": "강조색"},
                    "font": {"type": "string", "enum": list(FONTS), "description": "글꼴 느낌: dohyeon=굵고 단단함, hanna=둥글고 친근함, ssurround=귀엽고 통통, gothic=깔끔한 고딕"},
                    "title": {"type": "string", "description": "유튜브 제목 (40자 안팎, 최대 70자)"},
                    "description": {"type": "string", "description": "유튜브 설명 (3~6문장, 줄바꿈 허용)"},
                    "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 6},
                    "reason": {"type": "string", "description": "이 프레임과 문구를 고른 이유 1문장"},
                },
                "required": ["concept", "frame", "headline", "subline", "highlight", "position", "color",
                             "font", "title", "description", "hashtags", "reason"],
            },
        }
    },
    "required": ["thumbnails"],
}


def write_copy(concepts: list[dict], candidates: dict[str, dict], ctx: str) -> list[dict]:
    blocks = []
    for ci, c in enumerate(concepts, 1):
        ids = [fid for fid, cand in candidates.items() if cand["concept"] == ci]
        if not ids:
            continue
        files = "\n".join(f"  - {fid}: {candidates[fid]['preview']}" for fid in ids)
        blocks.append(f"### 콘셉트 {ci}. {c.get('angle', '')}\n{c.get('idea', '')}\n후보 프레임:\n{files}")
    prompt = (
        "당신은 한국 캠핑·아웃도어 유튜브 채널의 썸네일·제목 담당입니다.\n"
        "아래 후보 프레임 이미지를 Read 도구로 모두 직접 보고, 콘셉트마다 썸네일 배경으로 가장 좋은 한 장을 고른 뒤 "
        "그 위에 얹을 문구와 유튜브 제목·설명을 쓰세요.\n\n"
        "프레임 고르기: 초점이 맞고, 피사체가 크고 분명하며, 문구를 얹을 빈 공간이 있는 장면. "
        "콘셉트 설명보다 실제 이미지가 우선이다 — 설명과 다른 장면이면 콘셉트에 맞는 다른 후보를 고른다.\n"
        "썸네일 문구: 휴대폰 작은 화면에서도 읽히게 짧고 굵게. 제목을 그대로 반복하지 말고 제목과 짝을 이루게 쓴다. "
        "영상에 실제로 있는 일만 쓴다 (과장은 되지만 거짓은 안 된다). "
        "문구가 말하는 것이 고른 프레임에 보여야 한다 (예: '맑은 아침'이라 쓰면 밝은 하늘이 보이는 프레임). "
        "시간·횟수 같은 숫자는 시놉시스에 적힌 것만 쓴다 — AI 편집 설계의 분량(몇 분짜리)은 실제 걸린 시간이 아니다.\n"
        "제목: 검색될 단어(캠핑, 지명·장비 등 실제로 나오는 것)를 자연스럽게 넣고, 다섯 안의 제목이 서로 달라야 한다. "
        "이모지는 쓰지 않는다.\n"
        "설명: 첫 두 줄에 영상의 핵심을 담고(검색 결과에 보이는 부분), 이어서 그날의 흐름을 짧게 소개한다. "
        "해시태그는 설명에 넣지 말고 hashtags 에 따로 준다 (# 포함).\n\n"
        + ctx + "\n\n## 콘셉트와 후보 프레임\n" + "\n\n".join(blocks)
    )
    text = call_claude_text(prompt, json_schema=COPY_SCHEMA, timeout=900, max_retries=1)
    if not text:
        fail("Claude 가 썸네일 문구를 만들지 못했습니다 (응답 없음)")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}") + 1
        try:
            data = json.loads(text[start:end])
        except (json.JSONDecodeError, ValueError):
            fail("Claude 응답을 읽지 못했습니다 (문구)")
    return data.get("thumbnails") or []


# ── 합성 ──

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key in _font_cache:
        return _font_cache[key]
    path, index = FONTS.get(name, FONTS["gothic"])
    path = os.path.expanduser(path)
    try:
        font = ImageFont.truetype(path, size, index=index)
    except OSError:
        gpath, gindex = FONTS["gothic"]
        font = ImageFont.truetype(gpath, size, index=gindex)
    _font_cache[key] = font
    return font


def fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    nw, nh = round(sw * scale), round(sh * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def normalize_text(text: dict) -> dict:
    headline = str(text.get("headline") or "").replace("\\n", "\n").strip()
    lines = [ln.strip() for ln in headline.split("\n") if ln.strip()][:3]
    position = text.get("position") if text.get("position") in POSITIONS else "bottom-left"
    color = text.get("color") if text.get("color") in COLORS else "yellow"
    font = text.get("font") if text.get("font") in FONTS else "dohyeon"
    return {
        "headline": "\n".join(lines),
        "subline": str(text.get("subline") or "").strip(),
        "highlight": str(text.get("highlight") or "").strip(),
        "position": position,
        "color": color,
        "font": font,
    }


def compose(raw: Image.Image, text: dict) -> Image.Image:
    """문구 없는 1280×720 프레임 위에 썸네일 문구를 얹는다"""
    t = normalize_text(text)
    img = ImageEnhance.Contrast(raw).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(1.15).convert("RGBA")
    W, H = img.size
    vert, horiz = t["position"].split("-")
    lines = t["headline"].split("\n") if t["headline"] else []
    accent = COLORS[t["color"]]

    # 문구 쪽을 어둡게 (위/아래 그라데이션)
    shade = Image.new("L", (1, H))
    for y in range(H):
        r = y / H if vert == "bottom" else 1 - y / H
        shade.putpixel((0, y), int(190 * max(0.0, (r - 0.35) / 0.65) ** 1.4))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    overlay.putalpha(shade.resize((W, H)))
    img = Image.alpha_composite(img, overlay)

    if not lines and not t["subline"]:
        return img.convert("RGB")

    margin = 56
    max_w = W - margin * 2
    # 큰 문구: 가장 긴 줄이 폭 안에 들어오는 가장 큰 크기 (줄 수에 따라 상한)
    size = {1: 170, 2: 140}.get(len(lines), 112)
    while lines and size > 48:
        font = load_font(t["font"], size)
        if max(font.getlength(ln) for ln in lines) <= max_w:
            break
        size -= 6
    font = load_font(t["font"], size)
    stroke = max(4, size // 11)
    line_h = int(size * 1.12)

    sub_font = load_font(t["font"], max(34, int(size * 0.36)))
    sub_pad = 14
    sub_h = (sub_font.size + sub_pad * 2) if t["subline"] else 0
    gap = 18 if t["subline"] and lines else 0
    block_h = sub_h + gap + line_h * len(lines)

    y = margin if vert == "top" else H - margin - block_h
    draw = ImageDraw.Draw(img)

    def x_for(width: float) -> float:
        if horiz == "left":
            return margin
        if horiz == "right":
            return W - margin - width
        return (W - width) / 2

    # 보조 문구: 강조색 띠 위 검은 글씨
    if t["subline"]:
        sw = sub_font.getlength(t["subline"])
        sx = x_for(sw + sub_pad * 2)
        draw.rounded_rectangle([sx, y, sx + sw + sub_pad * 2, y + sub_h], radius=10, fill=accent + (255,))
        ink = (0, 0, 0) if t["color"] != "red" else (255, 255, 255)
        draw.text((sx + sub_pad, y + sub_h / 2), t["subline"], font=sub_font, fill=ink, anchor="lm")
        y += sub_h + gap

    # 큰 문구: 흰 글씨 + 검은 외곽선 + 그림자, highlight 부분만 강조색
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    runs = []
    for i, ln in enumerate(lines):
        lw = font.getlength(ln)
        lx, ly = x_for(lw), y + i * line_h
        hl = t["highlight"]
        k = ln.find(hl) if hl else -1
        parts = [(ln, (255, 255, 255))] if k < 0 else [
            (ln[:k], (255, 255, 255)), (hl, accent), (ln[k + len(hl):], (255, 255, 255))]
        cx = lx
        for seg, col in parts:
            if seg:
                runs.append((cx, ly, seg, col))
            cx += font.getlength(seg)
        sdraw.text((lx + stroke, ly + stroke * 1.4), ln, font=font, fill=(0, 0, 0, 170),
                   stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(stroke)))
    draw = ImageDraw.Draw(img)
    for cx, ly, seg, col in runs:
        draw.text((cx, ly), seg, font=font, fill=col, stroke_width=stroke, stroke_fill=(0, 0, 0))
    return img.convert("RGB")


# ── 명령 ──

def cmd_load(folder: str) -> None:
    analysis_path = os.path.join(folder, "autocut", "analysis.json")
    has_analysis = os.path.exists(analysis_path)
    clips = []
    if has_analysis:
        analysis = load_analysis(folder)
        has_analysis = analysis is not None
        if analysis:
            clips = scan_clips(folder, analysis)
    if not clips:
        clips = [{"name": n} for n in sorted(os.listdir(folder))
                 if not n.startswith(".") and n.lower().endswith(VIDEO_EXTS)]
    total = sum(c.get("duration", 0.0) for c in clips)
    emit({
        "type": "loaded",
        "folder": folder,
        "clipCount": len(clips),
        "totalSec": total if has_analysis else None,
        "hasAnalysis": has_analysis,
        "synopsis": find_synopsis(folder),
        "hasStory": bool(find_story_summary(folder)),
        "result": load_result(folder),
    })


def cmd_generate(folder: str, synopsis_arg: str | None) -> None:
    if not check_claude_available():
        fail("Claude CLI 를 찾을 수 없습니다")
    progress(2, "원본 폴더를 읽는 중...")
    analysis = load_analysis(folder)
    clips = scan_clips(folder, analysis)
    if not clips:
        fail("폴더에 원본 영상이 없습니다")
    synopsis = synopsis_arg.strip() if synopsis_arg is not None else find_synopsis(folder)
    ctx = context_block(synopsis, find_story_summary(folder), clips)

    tmp = tempfile.mkdtemp(prefix="video-studio-thumb-")
    try:
        if analysis:
            progress(8, f"Claude 가 장면 {len(analysis['windows'])}개를 읽고 썸네일 콘셉트를 정하는 중... (몇 분 걸립니다)")
            concepts, ranges = plan_from_windows(analysis, ctx)
        else:
            concepts, ranges = plan_from_sampling(clips, ctx, tmp)

        # 후보 장면 → 원본에서 가장 좋은 프레임
        jobs = []
        for ci, c in enumerate(concepts, 1):
            seen = set()
            for ref in c.get("candidates") or []:
                ref = str(ref).strip().upper()
                if ref in ranges and ref not in seen:
                    seen.add(ref)
                    jobs.append((ci, len(seen), ref))
        if not jobs:
            fail("Claude 가 고른 후보 장면을 찾지 못했습니다")
        log(f"후보 장면 {len(jobs)}개")

        def grab(job):
            ci, k, ref = job
            g0, g1 = ranges[ref]
            clip, local0 = locate(clips, g0)
            local1 = min(clip["duration"], local0 + (g1 - g0))
            return job, clip, best_frame(clip, local0, local1)

        candidates: dict[str, dict] = {}
        done = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for (ci, k, ref), clip, got in pool.map(grab, jobs):
                done += 1
                progress(35 + int(done / len(jobs) * 30), f"후보 프레임 뽑는 중... ({done}/{len(jobs)})")
                if not got:
                    continue
                img, t = got
                fid = f"{ci}-{k}"
                full = os.path.join(tmp, f"cand_{fid}.png")
                preview = os.path.join(tmp, f"cand_{fid}.jpg")
                img.save(full)
                img.resize((960, round(960 * img.height / img.width))).save(preview, "JPEG", quality=85)
                candidates[fid] = {"concept": ci, "ref": ref, "clip": clip["name"], "time": t,
                                   "full": full, "preview": preview}
        if not candidates:
            fail("후보 프레임을 하나도 뽑지 못했습니다")

        progress(68, "Claude 가 프레임을 보고 썸네일 문구·제목·설명을 쓰는 중... (몇 분 걸립니다)")
        copies = write_copy(concepts, candidates, ctx)
        if not copies:
            fail("Claude 가 썸네일 문구를 만들지 못했습니다")

        progress(92, "썸네일을 합성하는 중...")
        od = out_dir(folder)
        os.makedirs(od, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        items, used = [], set()
        for cp in copies:
            fid = str(cp.get("frame", "")).strip()
            if fid not in candidates:
                # 프레임 번호를 잘못 적으면 그 콘셉트의 첫 후보로
                ci = int(cp.get("concept") or 0)
                fid = next((f for f, c in candidates.items() if c["concept"] == ci), "")
            if not fid or fid in used:
                continue
            used.add(fid)
            cand = candidates[fid]
            n = len(items) + 1
            raw = fit_cover(Image.open(cand["full"]).convert("RGB"), THUMB_W, THUMB_H)
            raw_name, img_name = f"thumb_{n}_raw.jpg", f"thumb_{n}.jpg"
            raw.save(os.path.join(od, raw_name), "JPEG", quality=95)
            text = normalize_text(cp)
            compose(raw, text).save(os.path.join(od, img_name), "JPEG", quality=92)
            concept = concepts[cand["concept"] - 1] if 0 < cand["concept"] <= len(concepts) else {}
            items.append({
                "id": n,
                "angle": concept.get("angle", ""),
                "idea": concept.get("idea", ""),
                "image": img_name,
                "raw": raw_name,
                "source": {"clip": cand["clip"], "time": round(cand["time"], 2), "ref": cand["ref"]},
                "text": text,
                "title": str(cp.get("title", "")).strip(),
                "description": str(cp.get("description", "")).strip(),
                "hashtags": [str(h).strip() for h in cp.get("hashtags") or [] if str(h).strip()],
                "reason": str(cp.get("reason", "")).strip(),
            })
        if not items:
            fail("썸네일을 만들지 못했습니다")

        # 이전 실행에서 남은 번호가 더 큰 파일은 지운다
        for n in range(len(items) + 1, 20):
            for name in (f"thumb_{n}.jpg", f"thumb_{n}_raw.jpg"):
                p = os.path.join(od, name)
                if os.path.exists(p):
                    os.remove(p)

        result = {
            "version": 1,
            "createdAt": stamp,
            "model": DEFAULT_MODEL,
            "basis": "analysis" if analysis else "sampling",
            "synopsis": synopsis,
            "items": items,
            "selected": None,
        }
        save_result(folder, result)
        for name in ("selected.jpg", "selected.txt"):
            p = os.path.join(od, name)
            if os.path.exists(p):
                os.remove(p)
        progress(100, "완료")
        emit({"type": "generated", "result": result})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def find_item(result: dict, item_id: int) -> dict:
    for it in result.get("items") or []:
        if int(it.get("id", -1)) == item_id:
            return it
    fail(f"썸네일 {item_id}번을 찾을 수 없습니다")
    return {}


def write_selected(folder: str, item: dict) -> None:
    od = out_dir(folder)
    shutil.copyfile(os.path.join(od, item["image"]), os.path.join(od, "selected.jpg"))
    tags = " ".join(item.get("hashtags") or [])
    body = f"{item.get('title', '')}\n\n{item.get('description', '')}\n\n{tags}\n"
    with open(os.path.join(od, "selected.txt"), "w", encoding="utf-8") as f:
        f.write(body)


def cmd_compose(folder: str, item_id: int, text_json: str) -> None:
    result = load_result(folder)
    if not result:
        fail("썸네일 결과가 없습니다. 먼저 썸네일을 만드세요")
    item = find_item(result, item_id)
    try:
        patch = json.loads(text_json)
    except json.JSONDecodeError:
        fail("문구 데이터를 읽지 못했습니다")
    text = normalize_text({**item.get("text", {}), **{k: v for k, v in patch.items() if k in
                                                    ("headline", "subline", "highlight", "position", "color", "font")}})
    for key in ("title", "description"):
        if isinstance(patch.get(key), str):
            item[key] = patch[key].strip()
    if isinstance(patch.get("hashtags"), list):
        item["hashtags"] = [str(h).strip() for h in patch["hashtags"] if str(h).strip()]
    od = out_dir(folder)
    raw = Image.open(os.path.join(od, item["raw"])).convert("RGB")
    compose(raw, text).save(os.path.join(od, item["image"]), "JPEG", quality=92)
    item["text"] = text
    save_result(folder, result)
    if result.get("selected") == item_id:
        write_selected(folder, item)
    emit({"type": "composed", "result": result})


def cmd_select(folder: str, item_id: int) -> None:
    result = load_result(folder)
    if not result:
        fail("썸네일 결과가 없습니다")
    item = find_item(result, item_id)
    write_selected(folder, item)
    result["selected"] = item_id
    save_result(folder, result)
    emit({"type": "selected", "result": result})


def main() -> None:
    if len(sys.argv) < 3:
        fail("사용법: thumbnail.py load|generate|compose|select ...")
    command = sys.argv[1]
    if command == "generate":
        parser = argparse.ArgumentParser()
        parser.add_argument("--synopsis", default=None)
        parser.add_argument("folder")
        args = parser.parse_args(sys.argv[2:])
        folder = os.path.abspath(args.folder)
    else:
        folder = os.path.abspath(sys.argv[2])
    if not os.path.isdir(folder):
        fail(f"폴더를 찾을 수 없습니다: {folder}")

    try:
        if command == "load":
            cmd_load(folder)
        elif command == "generate":
            cmd_generate(folder, args.synopsis)
        elif command == "compose" and len(sys.argv) >= 5:
            cmd_compose(folder, int(sys.argv[3]), sys.argv[4])
        elif command == "select" and len(sys.argv) >= 4:
            cmd_select(folder, int(sys.argv[3]))
        else:
            fail(f"알 수 없는 명령: {command}")
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        log(f"예외: {e!r}")
        fail(str(e))


if __name__ == "__main__":
    main()
