#!/usr/bin/env python3
"""음성인식 오류 교정 스크립트 (Claude)

Whisper가 잘못 인식한 단어를 문맥 기반으로 복원합니다.
문장을 다듬거나 바꾸지 않고, 오인식된 단어만 올바른 단어로 교체합니다.
"""

import sys
import json
import os
import subprocess

BATCH_SIZE = 40


def _build_env() -> dict:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


def call_claude(prompt: str, timeout: int = 300) -> str:
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
            return ""
        data = json.loads(result.stdout)
        return data.get("result", "")
    except Exception as e:
        print(f"[spellcheck_claude] 오류: {e}", file=sys.stderr, flush=True)
        return ""


def correct_batch(segments: list[dict], description: str) -> list[dict]:
    """Claude로 음성인식 오류 교정"""
    numbered = "\n".join(f"[{s['id']}] {s['text']}" for s in segments)

    desc_section = ""
    if description.strip():
        desc_section = f"\n영상 설명: {description.strip()}\n이 맥락을 참고하여 도메인 용어를 정확히 교정하세요.\n"

    prompt = f"""음성인식(Whisper)으로 생성된 한국어 자막입니다. 오인식된 단어를 교정하세요.

규칙:
- 화자가 실제로 말한 단어를 정확히 복원하는 것이 목표입니다
- 문장을 다듬거나 바꾸지 마세요. 오인식된 단어만 교체하세요
- 띄어쓰기 오류를 교정하세요
- 발음이 비슷하지만 잘못 인식된 단어를 올바른 단어로 바꾸세요
- 교정할 필요가 없는 문장은 그대로 유지하세요
- 각 줄의 [번호]를 유지하고 텍스트만 교정하세요
- 줄 수를 정확히 유지하세요
{desc_section}
자막:
{numbered}

교정된 결과만 출력하세요. 설명이나 주석 없이 [번호] 텍스트 형식으로만 응답하세요."""

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

    segments = input_data.get("segments", [])
    description = input_data.get("description", "")
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
        results = correct_batch(batch, description)
        for r in results:
            all_results[r["id"]] = r["text"]

        processed = min((bi + 1) * BATCH_SIZE, total)
        print(json.dumps({
            "status": "progress",
            "processed": processed,
            "total": total,
        }, ensure_ascii=False), flush=True)

    # 누락된 세그먼트는 원본 유지
    final = [{"id": s["id"], "text": all_results.get(s["id"], s["text"])} for s in segments]
    print(json.dumps({"status": "done", "segments": final}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
