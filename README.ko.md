# Video Studio

[English](README.md) · **한국어**

3개의 AI 영상 편집 도구를 하나로 통합한 데스크톱 앱.

- **AI 편집** — 영상 폴더를 분석하여 자동으로 KEEP/CUT 편집 가이드 SRT 생성
- **자막 추출** — 영상에서 음성을 인식하여 자막 SRT 생성 + 맞춤법 교정 + 번역
- **BGM 생성** — 영상 분위기를 분석하여 AI 배경음악 생성

## AI 엔진 선택

각 모듈은 **Ollama**(로컬 GPU)와 **Claude**(Claude Code CLI) 중 하나를 선택해 실행할 수 있습니다. 모듈마다 UI에서 엔진을 토글하며, 선택은 `localStorage`에 저장됩니다.

| 모듈 | 단계 | Ollama 모델 | Claude 옵션 | 기본값 |
|------|------|-------------|-------------|--------|
| AI 편집 | Stage 2 비전 태깅 | `qwen2.5vl:7b` | Claude Vision (Read 도구로 프레임 분석) | Claude |
| AI 편집 | LLM 편집 추론 | `qwen3:14b` | Claude (하이브리드 1차 편집 + Score 기반 보강) | Claude |
| 자막 추출 | 음성인식 교정 | 네이버 맞춤법 검사기 | Claude (오인식 단어 복원) | Claude |
| 자막 추출 | 번역 (영/일) | `qwen2.5:14b` | Claude | Claude |
| BGM 생성 | 장면 분석 + 음악 프롬프트 | `llama3.2-vision:11b` + `qwen2.5:14b` | Claude Vision (1회 호출 통합) | Claude |

> Claude 엔진을 사용하면 해당 모듈은 로컬 GPU/Ollama를 점유하지 않으므로, **여러 모듈을 동시 실행해도 GPU 메모리 충돌이 발생하지 않습니다**(`resource-manager.ts`가 `usesClaude` 플래그로 점유량 0 처리).

## 요구사항

| 항목 | 버전 | 설치 |
|------|------|------|
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Python | 3.11 | `brew install python@3.11` |
| FFmpeg | 최신 | `brew install ffmpeg` |
| Ollama | 최신 (선택) | `brew install ollama` |
| Claude Code CLI | 최신 (선택) | [claude.com/claude-code](https://claude.com/claude-code) |

> Ollama 엔진과 Claude 엔진은 **하나만 설치해도 동작**합니다. 단, 모듈 UI에서 선택한 엔진의 CLI/서버가 없으면 해당 모듈은 실행 시점에 오류를 반환합니다. 모든 모듈을 Claude 엔진으로 사용한다면 Ollama 설치는 생략 가능합니다.

### Claude Code CLI

Claude 엔진은 시스템에 설치된 `claude` 명령을 `claude -p` (pipe mode)로 호출합니다. 옵션은 아래를 사용합니다:

- `--model claude-opus-4-7`
- `--output-format json`
- `--no-session-persistence` — 메인 세션에 영향 주지 않음
- `--dangerously-skip-permissions` — 자동 분석 중 권한 프롬프트 우회
- `--json-schema` — 편집 분류 등 구조화 출력 강제 (해당 호출에 한함)

서브프로세스 환경에서는 `CLAUDECODE` 환경변수를 제거하여 중첩 세션을 방지합니다. CLI가 없거나 인증되지 않은 환경에서는 모듈이 "Claude CLI를 찾을 수 없습니다" 오류를 표시합니다.

### Ollama 모델 (Ollama 엔진 사용 시)

```bash
# AI 편집
ollama pull qwen2.5vl:7b      # Stage 2 비전 태깅
ollama pull qwen3:14b          # 편집 추론

# 자막 번역
ollama pull qwen2.5:14b        # 영어/일본어 번역

# BGM 분석
ollama pull llama3.2-vision:11b
```

## 설치 및 실행

```bash
# 1. 저장소 클론
git clone https://github.com/wowjangpro/video-studio.git
cd video-studio

# 2. Node.js 의존성 설치
npm install

# 3. Python 가상환경 생성 + 의존성 설치
npm run setup

# 4. (Ollama 엔진 사용 시) Ollama 서버 실행
ollama serve

# 5. (Claude 엔진 사용 시) Claude Code CLI 로그인 확인
claude --version

# 6. 앱 실행
npm run dev
```

## 사용 방법

### AI 편집

1. 영상 파일이 들어있는 **폴더**를 선택합니다
2. 편집 코멘트를 입력합니다 (예: "요리 장면 위주로, 완성본 20분")
3. **AI 엔진**을 선택합니다
   - `Claude (1차 편집)` — Claude 하이브리드 (기본). Stage 2 비전 태깅과 1차 편집 판단을 Claude로 수행 후 Score 기반 보강
   - `Score 기반 (LLM 없음)` — 메트릭 점수만으로 결정 (가장 빠르고 결정론적)
4. "분석 시작"을 클릭하면 아래 파이프라인이 자동 실행됩니다:
   - 오디오 추출 (FFmpeg)
   - Stage 1 스캔 (모션/오디오/밝기 메트릭)
   - VAD + STT (Silero VAD + faster-whisper large-v3)
   - Stage 2 비전 태깅 (Qwen2.5-VL:7B **또는 Claude Vision** — 5프레임 맥락 분석, Claude는 배치 호출)
   - 발화 그룹 분류 A/B/C/D (Claude — 시청자 멘트 보호)
   - AI 편집 (Qwen3:14B **또는 Claude** — 스토리보드 기반 KEEP/CUT)
   - 침묵 trim (STT timing 기반 1초+ 침묵을 1초로 압축)
   - SRT / EDL / FCPXML 출력 (다빈치 리졸브 호환)
5. 분석 결과는 `영상폴더/autocut/` 하위에 저장됩니다 (cache.json으로 이어서 하기 지원)
6. SRT/EDL/FCPXML 파일을 DaVinci Resolve 등 편집기에서 가이드로 활용합니다

### 자막 추출

1. 영상 파일을 선택하거나 YouTube URL을 붙여넣습니다
2. 영상 설명을 입력하면 인식 정확도가 향상됩니다
3. **음성인식 교정 엔진**을 선택합니다
   - `Claude 교정` (기본) — 오인식 단어를 문맥으로 복원
   - `네이버 맞춤법 검사기` — 표기/띄어쓰기 위주 교정
4. "자막 생성"을 클릭하면 아래 과정이 실행됩니다:
   - 오디오 추출 (FFmpeg → 16kHz WAV)
   - 음성인식 (Silero VAD + faster-whisper large-v3)
   - 교정 (선택한 엔진)
5. 생성된 자막을 편집하고 SRT로 저장합니다
6. 영어/일본어 번역이 필요하면 툴바에서 **번역 엔진**(Claude / Ollama `qwen2.5:14b`)을 선택 후 "번역" 버튼을 사용합니다
7. 결과는 `영상 위치/subtitle/` 하위에 저장됩니다

### BGM 생성

1. 영상 파일을 선택합니다
2. BGM을 적용할 구간을 설정합니다
3. **분석 엔진**을 선택합니다
   - `Claude` (기본) — Claude Vision으로 장면 분석 + 음악 프롬프트 생성을 **1회 호출로 통합**
   - `Ollama` — LLaMA 3.2 Vision:11B (장면 분석) + Qwen2.5:14B (프롬프트 생성)
4. "분석"을 클릭하면 AI가 장면 분위기를 분석합니다
5. 생성된 음악 프롬프트를 확인/수정합니다
6. "BGM 생성"을 클릭하면 배경음악이 만들어집니다 (ACE-Step 1.5, bgm-venv 사용)
7. 결과는 `영상 위치/bgm/` 하위에 저장됩니다

## 프로젝트 구조

```
video-studio/
├── src/
│   ├── main/                    # Electron 메인 프로세스
│   │   ├── index.ts             # 앱 엔트리
│   │   ├── ipc-handlers.ts      # IPC 라우터
│   │   ├── services/            # 공유 서비스 (Python, FFmpeg, 리소스 관리)
│   │   └── modules/             # 모듈별 IPC (autocut, subtitle, bgm)
│   ├── preload/index.ts         # contextBridge API
│   └── renderer/src/
│       ├── App.tsx              # 탭 기반 라우팅
│       ├── common/              # 공통 (TabBar, app-store, 스타일)
│       ├── autocut/             # AI 편집 (컴포넌트 + 스토어 + CSS)
│       ├── subtitle/            # 자막 추출
│       └── bgm/                 # BGM 생성
├── python/
│   ├── autocut/                 # AI 편집 스크립트
│   │   ├── claude_client.py     # Claude CLI 래퍼 (텍스트/비전 공용)
│   │   ├── stage2.py            # Ollama / Claude 비전 태깅
│   │   ├── storyboard.py        # 하이브리드 편집 + 발화 그룹 분류
│   │   └── ...
│   ├── subtitle/                # 자막 추출 스크립트
│   │   ├── spellcheck_claude.py / spellcheck.py
│   │   ├── translate_claude.py / translate.py
│   │   └── translate_text_claude.py / translate_text.py
│   ├── bgm/                     # BGM 생성 스크립트
│   │   ├── analyze_claude.py    # Claude Vision 1회 호출 통합 분석
│   │   ├── generate.py          # ACE-Step BGM 생성
│   │   └── ...
│   ├── shared-venv/             # subtitle + autocut 공유 venv
│   ├── bgm-venv/                # bgm 전용 venv (torch)
│   └── setup.sh                 # venv 생성 스크립트
└── out/                         # 빌드 출력
```

## 출력 파일 구조

각 모듈이 생성하는 파일은 영상 폴더 내 하위 디렉토리에 저장됩니다:

```
영상폴더/
├── video1.mp4
├── autocut/                    # AI 편집 결과
│   ├── cache.json              # 이어서 하기용 진행 상태
│   ├── analysis.json           # 분석 결과 메타
│   ├── thumbs/                 # Stage 2 비전 입력 프레임
│   ├── 폴더명.srt              # 편집 가이드 SRT
│   ├── 폴더명.edl              # EDL (24fps NDF)
│   └── 폴더명.fcpxml           # FCPXML (다빈치 리졸브 권장)
├── subtitle/                   # 자막 추출 결과
│   ├── video1.srt
│   ├── video1_en.srt
│   └── video1_jp.srt
└── bgm/                        # BGM 생성 결과
    └── video1_bgm.wav
```

## 빌드 및 배포

```bash
# 개발 모드
npm run dev

# 프로덕션 빌드 (out/ 디렉토리에 출력)
npm run build

# macOS .app 패키징
npm run dist
```

## 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama 서버 주소 |
| `CLAUDECODE` | (자동 제거) | Python 서브프로세스에서 중첩 세션 방지를 위해 자동 unset |

## 기술 스택

- **프론트엔드**: React, Zustand, TypeScript
- **데스크톱**: Electron, electron-vite
- **AI/ML**:
  - faster-whisper (STT)
  - Ollama (Qwen2.5-VL, Qwen3, LLaMA 3.2 Vision, Qwen2.5)
  - Claude Code CLI (`claude -p`, Opus 4.7) — 비전/텍스트 양쪽 모두 활용
  - ACE-Step 1.5 (BGM 생성)
- **미디어**: FFmpeg, Silero VAD

## 라이선스

MIT
