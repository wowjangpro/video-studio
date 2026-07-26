#!/usr/bin/env python3
"""YouTube 제목/설명 번역 — Claude 엔진"""

import sys
import json
import os
import subprocess

LANG_NAMES = {
    "en": "English",
    "jp": "Japanese",
    "zh": "Chinese (Simplified)",
}


def call_claude(prompt: str, timeout: int = 300) -> str:
    cmd = [
        "claude", "-p",
        "--model", "claude-opus-5",
        "--output-format", "json",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        if result.returncode != 0:
            print(f"[translate_text_claude] Claude 오류: {result.stderr[:300]}",
                  file=sys.stderr, flush=True)
            return ""
        data = json.loads(result.stdout)
        return data.get("result", "")
    except subprocess.TimeoutExpired:
        print("[translate_text_claude] Claude 타임아웃", file=sys.stderr, flush=True)
        return ""
    except Exception as e:
        print(f"[translate_text_claude] Claude 호출 실패: {e}",
              file=sys.stderr, flush=True)
        return ""


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

    content = call_claude(prompt, timeout=300).strip()
    if not content:
        return {"title": title, "description": description}

    # ```json ... ``` 블록 제거
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines).strip()

    try:
        result = json.loads(content)
        return {
            "title": result.get("title", title),
            "description": result.get("description", description),
        }
    except json.JSONDecodeError:
        return {"title": title, "description": content}


def check_claude_available() -> bool:
    try:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True,
            timeout=10, env=env,
        )
        return result.returncode == 0
    except Exception:
        return False


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

    if not check_claude_available():
        print(json.dumps({
            "status": "error",
            "message": "Claude CLI를 찾을 수 없습니다. claude 명령어 설치 필요",
        }, ensure_ascii=False), flush=True)
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
