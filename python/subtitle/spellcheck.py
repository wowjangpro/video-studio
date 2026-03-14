#!/usr/bin/env python3
"""네이버 맞춤법 검사기를 이용한 자막 교정 스크립트"""

import sys
import json
import re
import requests

SPELL_CHECK_URL = "https://m.search.naver.com/p/csearch/ocontent/util/SpellerProxy"
MAX_TEXT_LEN = 500  # 네이버 API 최대 글자 수


def get_passport_key():
    """네이버 맞춤법 검사 passportKey 획득"""
    try:
        resp = requests.get(
            "https://search.naver.com/search.naver?where=nexearch&query=맞춤법검사기",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        match = re.search(r'passportKey=([a-zA-Z0-9]+)', resp.text)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"[spellcheck] passportKey 획득 실패: {e}", file=sys.stderr, flush=True)
    return None


def check_spelling(text, passport_key):
    """네이버 맞춤법 검사 API 호출"""
    if not text.strip():
        return text

    params = {
        "passportKey": passport_key,
        "q": text,
        "where": "nexearch",
        "color_blindness": 0,
    }

    try:
        resp = requests.get(SPELL_CHECK_URL, params=params, timeout=10)
        data = resp.json()
        result = data.get("message", {}).get("result", {})
        corrected = result.get("notag_html", "")
        if corrected:
            return corrected
    except Exception as e:
        print(f"[spellcheck] 맞춤법 검사 실패: {e}", file=sys.stderr, flush=True)

    return text


def main():
    """stdin으로 JSON 세그먼트 배열을 받아 교정 후 stdout으로 출력"""
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"입력 파싱 실패: {str(e)}"}), flush=True)
        sys.exit(1)

    segments = input_data.get("segments", [])
    total = len(segments)

    if total == 0:
        print(json.dumps({"status": "done", "segments": []}, ensure_ascii=False), flush=True)
        sys.exit(0)

    passport_key = get_passport_key()
    if not passport_key:
        print(f"[spellcheck] passportKey 획득 실패 — 원본 텍스트를 그대로 반환합니다", file=sys.stderr, flush=True)
        results = [{"id": seg["id"], "text": seg["text"]} for seg in segments]
        print(json.dumps({"status": "done", "segments": results}, ensure_ascii=False), flush=True)
        sys.exit(0)

    print(json.dumps({"status": "started", "total": total}, ensure_ascii=False), flush=True)

    results = []
    for i, seg in enumerate(segments):
        sid = seg["id"]
        text = seg["text"]

        corrected = check_spelling(text, passport_key)
        results.append({"id": sid, "text": corrected})

        print(
            json.dumps({"status": "progress", "processed": i + 1, "total": total}, ensure_ascii=False),
            flush=True,
        )

    print(json.dumps({"status": "done", "segments": results}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
