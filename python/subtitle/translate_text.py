#!/usr/bin/env python3
"""YouTube 제목/설명 번역 스크립트 (Ollama + Qwen2.5)"""

import sys
import json
import os
import requests

OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"
MODEL = "qwen2.5:14b"

LANG_NAMES = {
    "en": "English",
    "jp": "Japanese",
}


def translate(title, description, target_lang):
    lang_name = LANG_NAMES.get(target_lang, target_lang)

    prompt = f"""Translate the following YouTube video title and description from Korean to {lang_name}.
Keep the original formatting (line breaks, links, timestamps, hashtags).
Do not add any extra text or explanation.
Output ONLY a JSON object with "title" and "description" keys.

Title: {title}

Description:
{description}

Output JSON:"""

    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.3},
    }

    resp = requests.post(OLLAMA_URL, json=body, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("message", {}).get("content", "").strip()

    # JSON 파싱 시도
    try:
        # ```json ... ``` 블록 제거
        if content.startswith("```"):
            lines = content.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines).strip()

        result = json.loads(content)
        return {
            "title": result.get("title", title),
            "description": result.get("description", description),
        }
    except json.JSONDecodeError:
        # JSON 파싱 실패 시 전체를 description으로
        return {"title": title, "description": content}


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"입력 파싱 실패: {str(e)}"}), flush=True)
        sys.exit(1)

    title = input_data.get("title", "")
    description = input_data.get("description", "")
    target_lang = input_data.get("lang", "en")

    if not title and not description:
        print(json.dumps({"status": "done", "title": "", "description": ""}, ensure_ascii=False), flush=True)
        sys.exit(0)

    # Ollama 연결 확인
    try:
        ollama_base = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        requests.get(f"{ollama_base}/api/tags", timeout=5)
    except Exception:
        print(json.dumps({"status": "error", "message": "Ollama 서버에 연결할 수 없습니다"}, ensure_ascii=False), flush=True)
        sys.exit(1)

    print(json.dumps({"status": "started"}, ensure_ascii=False), flush=True)

    try:
        result = translate(title, description, target_lang)
        print(json.dumps({
            "status": "done",
            "title": result["title"],
            "description": result["description"],
        }, ensure_ascii=False), flush=True)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"번역 실패: {str(e)}"}), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
