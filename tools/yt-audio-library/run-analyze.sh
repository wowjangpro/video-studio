#!/bin/bash
# 라이브러리 곡 분석을 끝날 때까지 돌린다. 프로세스가 죽으면(메모리 부족 강제 종료 등) 다시 띄운다.
# library_analyze.py 는 분석한 곡을 건너뛰므로 다시 띄워도 이어서 한다.
#
# 사용: run-analyze.sh <libraryRoot> [workers]
#   nohup caffeinate -i tools/yt-audio-library/run-analyze.sh /Volumes/Subdisk1/YTAudioLibrary > <root>/logs/analyze.log 2>&1 &
#
# 주의: 분석 중에 같은 맥에서 CLAP 모델을 쓰는 다른 작업(추천 시험 등)을 함께 돌리면
#       메모리가 모자라 분석기가 경고 없이 죽은 적이 있다 (2026-11-17, 두 번).
set -u
ROOT="$1"
WORKERS="${2:-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BGM="$HERE/../../python/bgm"
PY="$HERE/../../python/bgm-venv/bin/python"
MAX_RESTARTS=30

for i in $(seq 1 $MAX_RESTARTS); do
  echo "{\"type\": \"supervisor\", \"message\": \"분석 시작 ($i 번째)\", \"at\": \"$(date '+%F %T')\"}"
  out=$(cd "$BGM" && "$PY" library_analyze.py "$ROOT" --workers "$WORKERS" 2>&1 | tee /dev/stderr | tail -n 1)
  if echo "$out" | grep -q '"type": "analyzed"'; then
    echo "{\"type\": \"supervisor\", \"message\": \"분석 끝\", \"at\": \"$(date '+%F %T')\"}"
    exit 0
  fi
  echo "{\"type\": \"supervisor\", \"message\": \"분석기가 끝나지 않고 멈춤 — 30초 뒤 다시 시작\", \"at\": \"$(date '+%F %T')\"}"
  sleep 30
done
echo "{\"type\": \"supervisor\", \"message\": \"$MAX_RESTARTS 번 다시 시작해도 끝나지 않아 멈춥니다\"}"
exit 1
