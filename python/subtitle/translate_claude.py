#!/usr/bin/env python3
"""자막 번역 스크립트 (Claude CLI)"""

import sys
import json
import os
import subprocess

BATCH_SIZE = 30  # Claude는 컨텍스트가 넓으므로 배치 크기 증가

LANG_NAMES = {
    "en": "English",
    "jp": "Japanese",
}


def _build_env() -> dict:
    """중첩 세션 방지를 위해 CLAUDECODE 환경변수 제거"""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def call_claude(prompt: str, timeout: int = 300) -> str:
    """Claude CLI 호출"""
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
            timeout=timeout,
            env=_build_env(),
        )
        if result.returncode != 0:
            print(f"[translate_claude] Claude 오류: {result.stderr[:300]}", file=sys.stderr, flush=True)
            return ""
        data = json.loads(result.stdout)
        return data.get("result", "")
    except subprocess.TimeoutExpired:
        print("[translate_claude] Claude 타임아웃", file=sys.stderr, flush=True)
        return ""
    except Exception as e:
        print(f"[translate_claude] Claude 호출 실패: {e}", file=sys.stderr, flush=True)
        return ""


def translate_batch(segments: list[dict], lang: str, description: str, context_before: str = "", context_after: str = "") -> list[dict]:
    """Claude로 배치 번역"""
    lang_name = LANG_NAMES.get(lang, lang)

    numbered = "\n".join(f"[{s['id']}] {s['text']}" for s in segments)

    prompt = f"""한국어 자막을 {lang_name}로 번역하세요.

규칙:
- 각 줄의 [번호]를 유지하고 번역만 교체
- 자연스럽고 원어민이 쓰는 표현 사용
- 고유명사는 음역
- 줄 수를 정확히 유지
- 번역 결과만 출력 (설명 불필요)

{f'영상 설명: {description}' if description else ''}
{f'이전 맥락: {context_before}' if context_before else ''}

번역할 자막:
{numbered}

{f'다음 맥락: {context_after}' if context_after else ''}"""

    response = call_claude(prompt)
    if not response:
        return []

    results = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("["):
            continue
        bracket_end = line.find("]")
        if bracket_end < 0:
            continue
        try:
            seg_id = int(line[1:bracket_end])
            text = line[bracket_end + 1:].strip()
            if text:
                results.append({"id": seg_id, "text": text})
        except ValueError:
            continue

    return results


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"입력 파싱 실패: {str(e)}"}), flush=True)
        sys.exit(1)

    lang = input_data.get("lang", "en")
    description = input_data.get("description", "")
    segments = input_data.get("segments", [])
    total = len(segments)

    if total == 0:
        print(json.dumps({"status": "done", "segments": []}, ensure_ascii=False), flush=True)
        sys.exit(0)

    # Claude CLI 확인
    try:
        subprocess.run(["claude", "--version"], capture_output=True, timeout=10, env=_build_env())
    except Exception:
        print(json.dumps({"status": "error", "message": "Claude CLI가 설치되어 있지 않습니다"}, ensure_ascii=False), flush=True)
        sys.exit(1)

    print(json.dumps({"status": "started", "total": total}, ensure_ascii=False), flush=True)

    all_results = {}
    batches = [segments[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]

    for bi, batch in enumerate(batches):
        # 맥락: 이전/다음 배치의 마지막/첫 2줄
        context_before = ""
        if bi > 0:
            prev = batches[bi - 1][-2:]
            context_before = " / ".join(s["text"] for s in prev)

        context_after = ""
        if bi < len(batches) - 1:
            nxt = batches[bi + 1][:2]
            context_after = " / ".join(s["text"] for s in nxt)

        results = translate_batch(batch, lang, description, context_before, context_after)

        for r in results:
            all_results[r["id"]] = r["text"]

        processed = min((bi + 1) * BATCH_SIZE, total)
        print(json.dumps({
            "status": "progress",
            "processed": processed,
            "total": total,
        }, ensure_ascii=False), flush=True)

    # 누락된 세그먼트 일괄 재시도
    missing = [s for s in segments if s["id"] not in all_results]
    if missing:
        results = translate_batch(missing, lang, description)
        for r in results:
            all_results[r["id"]] = r["text"]

    final = [{"id": s["id"], "text": all_results.get(s["id"], s["text"])} for s in segments]
    print(json.dumps({"status": "done", "segments": final}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
