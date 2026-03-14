#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ─── 사전 요구사항 확인 ───────────────────────────────────────

MISSING=0

if ! command -v brew &>/dev/null; then
    error "Homebrew가 설치되어 있지 않습니다."
    echo "  설치: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    MISSING=1
fi

if ! command -v python3.11 &>/dev/null; then
    error "python3.11이 설치되어 있지 않습니다."
    echo "  설치: brew install python@3.11"
    MISSING=1
fi

if ! command -v ffmpeg &>/dev/null; then
    error "ffmpeg이 설치되어 있지 않습니다."
    echo "  설치: brew install ffmpeg"
    MISSING=1
fi

if ! command -v ollama &>/dev/null; then
    error "ollama가 설치되어 있지 않습니다."
    echo "  설치: brew install ollama"
    MISSING=1
fi

if [ "$MISSING" -ne 0 ]; then
    echo ""
    error "위 항목을 설치한 뒤 다시 실행해주세요."
    exit 1
fi

info "사전 요구사항 확인 완료"

# ─── shared-venv (auto-subtitle + ai-movie-cut) ──────────────

if [ -d "shared-venv" ]; then
    warn "shared-venv가 이미 존재합니다. 건너뜁니다. (재생성: rm -rf shared-venv 후 재실행)"
else
    info "shared-venv 생성 중..."
    python3.11 -m venv shared-venv
    shared-venv/bin/pip install --upgrade pip
    shared-venv/bin/pip install -r requirements-shared.txt
    info "shared-venv 생성 완료"
fi

# ─── bgm-venv (bgm-studio) ───────────────────────────────────

if [ -d "bgm-venv" ]; then
    warn "bgm-venv가 이미 존재합니다. 건너뜁니다. (재생성: rm -rf bgm-venv 후 재실행)"
else
    info "bgm-venv 생성 중..."
    python3.11 -m venv bgm-venv
    bgm-venv/bin/pip install --upgrade pip
    bgm-venv/bin/pip install -r requirements-bgm.txt
    info "bgm-venv 생성 완료"
fi

# ─── Ollama 모델 안내 ─────────────────────────────────────────

echo ""
info "설치 완료! 아래 Ollama 모델이 필요합니다:"
echo ""
echo "  # ai-movie-cut (비전 태깅 + 편집 추론)"
echo "  ollama pull qwen2.5vl:7b"
echo "  ollama pull exaone3.5:7.8b"
echo ""
echo "  # bgm-studio (음악 생성용, 필요 시)"
echo "  ollama pull gemma3:27b"
echo ""
