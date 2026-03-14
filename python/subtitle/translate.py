#!/usr/bin/env python3
"""자막 번역 스크립트 (Ollama + Qwen2.5)"""

import sys
import json
import os
import re
import requests

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"
MODEL = "qwen2.5:14b"
BATCH_SIZE = 20  # 한 번에 번역할 세그먼트 수

LANG_NAMES = {
    "en": "English",
    "jp": "Japanese",
}


def build_input(segments):
    """세그먼트를 [N] 마커 형식으로 연결"""
    return "\n".join(f"[{seg['id']}] {seg['text']}" for seg in segments)


def parse_response(text, segment_ids):
    """[N] 마커로 분리하여 세그먼트별 번역 추출"""
    result = {}
    pattern = re.compile(r'\[(\d+)\]\s*')
    parts = pattern.split(text)

    i = 1
    while i < len(parts) - 1:
        try:
            sid = int(parts[i])
            translated = parts[i + 1].strip()
            if translated:
                result[sid] = translated
        except (ValueError, IndexError):
            pass
        i += 2

    return result


def translate_batch(segments, target_lang, context_before="", context_after="", description=""):
    """Ollama로 배치 번역"""
    lang_name = LANG_NAMES.get(target_lang, target_lang)
    input_text = build_input(segments)

    context_parts = []
    if context_before:
        context_parts.append(f"이전 자막:\n{context_before}")
    if context_after:
        context_parts.append(f"다음 자막:\n{context_after}")
    context_str = "\n\n".join(context_parts)

    desc_line = f"Video description: {description}\n\n" if description else ""

    prompt = f"""Translate Korean subtitles to {lang_name}.
Keep each line's number prefix like [0], [1], [2] unchanged.
Translate naturally based on full context. Output translated lines only.
IMPORTANT: You must translate ALL lines. Do not skip any line.

{desc_line}{f"Context (reference only, do not translate):{chr(10)}{context_str}{chr(10)}{chr(10)}" if context_str else ""}Input:
{input_text}

Output:"""

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.3},
    }

    resp = requests.post(OLLAMA_URL, json=body, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("message", {}).get("content", "")

    segment_ids = [seg["id"] for seg in segments]
    return parse_response(content, segment_ids)


def translate_single(segment, target_lang, context="", description=""):
    """누락된 세그먼트 개별 번역"""
    lang_name = LANG_NAMES.get(target_lang, target_lang)

    desc_line = f"Video description: {description}\n\n" if description else ""

    prompt = f"""Translate this Korean subtitle to {lang_name}.
Output only the translated text, nothing else.

{desc_line}{f"Context:{chr(10)}{context}{chr(10)}{chr(10)}" if context else ""}Korean: {segment['text']}

{lang_name}:"""

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.3},
    }

    resp = requests.post(OLLAMA_URL, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("message", {}).get("content", "").strip()
    # [N] 마커가 포함된 경우 제거
    content = re.sub(r'^\[\d+\]\s*', '', content).strip()
    return content if content else None


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"입력 파싱 실패: {str(e)}"}), flush=True)
        sys.exit(1)

    segments = input_data.get("segments", [])
    target_lang = input_data.get("lang", "en")
    description = input_data.get("description", "")
    total = len(segments)

    if total == 0:
        print(json.dumps({"status": "done", "segments": []}, ensure_ascii=False), flush=True)
        sys.exit(0)

    # Ollama 연결 확인
    try:
        ollama_base = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        requests.get(f"{ollama_base}/api/tags", timeout=5)
    except Exception:
        print(json.dumps({"status": "error", "message": "Ollama 서버에 연결할 수 없습니다"}, ensure_ascii=False), flush=True)
        sys.exit(1)

    print(json.dumps({"status": "started", "total": total}, ensure_ascii=False), flush=True)

    # 배치 분리
    batches = []
    for i in range(0, total, BATCH_SIZE):
        batches.append(segments[i : i + BATCH_SIZE])

    all_translations = {}
    processed = 0

    for batch_idx, batch in enumerate(batches):
        # 앞뒤 배치를 컨텍스트로 제공
        prev_context = build_input(batches[batch_idx - 1]) if batch_idx > 0 else ""
        next_context = build_input(batches[batch_idx + 1]) if batch_idx < len(batches) - 1 else ""

        try:
            parsed = translate_batch(batch, target_lang, prev_context, next_context, description)
            all_translations.update(parsed)
        except Exception as e:
            print(json.dumps({"status": "error", "message": f"번역 실패: {str(e)}"}), flush=True)
            sys.exit(1)

        processed += len(batch)
        print(
            json.dumps({"status": "progress", "processed": processed, "total": total}, ensure_ascii=False),
            flush=True,
        )

    # 누락된 세그먼트 개별 재시도
    missing = [seg for seg in segments if seg["id"] not in all_translations]
    if missing:
        print(
            json.dumps({"status": "progress", "message": f"누락된 {len(missing)}개 세그먼트 재번역 중..."}, ensure_ascii=False),
            flush=True,
        )
        seg_by_id = {seg["id"]: seg for seg in segments}
        for seg in missing:
            # 앞뒤 세그먼트를 컨텍스트로 제공
            context_lines = []
            for sid in range(seg["id"] - 2, seg["id"] + 3):
                if sid == seg["id"]:
                    continue
                if sid in all_translations:
                    context_lines.append(f"[{sid}] {all_translations[sid]}")
                elif sid in seg_by_id:
                    context_lines.append(f"[{sid}] {seg_by_id[sid]['text']}")
            context = "\n".join(context_lines)

            try:
                translated = translate_single(seg, target_lang, context, description)
                if translated:
                    all_translations[seg["id"]] = translated
            except Exception:
                pass  # 개별 실패는 원본 텍스트 유지

    results = []
    for seg in segments:
        results.append({
            "id": seg["id"],
            "text": all_translations.get(seg["id"], seg["text"]),
        })

    print(json.dumps({"status": "done", "segments": results}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
