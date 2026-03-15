# Video Studio

3개의 AI 영상 편집 도구를 하나로 통합한 데스크톱 앱.

- **AI 편집** — 영상 폴더를 분석하여 자동으로 KEEP/CUT 편집 가이드 SRT 생성
- **자막 추출** — 영상에서 음성을 인식하여 자막 SRT 생성 + 맞춤법 교정 + 번역
- **BGM 생성** — 영상 분위기를 분석하여 AI 배경음악 생성

## 요구사항

| 항목 | 버전 | 설치 |
|------|------|------|
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Python | 3.11 | `brew install python@3.11` |
| FFmpeg | 최신 | `brew install ffmpeg` |
| Ollama | 최신 | `brew install ollama` |

### Ollama 모델

```bash
# AI 편집 (필수)
ollama pull qwen2.5vl:7b      # 비전 태깅
ollama pull qwen3:14b          # 편집 추론

# 자막 번역 (선택)
ollama pull qwen2.5:14b        # 영어/일본어 번역

# BGM 분석 (선택)
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

# 4. Ollama 서버 실행 (별도 터미널)
ollama serve

# 5. 앱 실행
npm run dev
```

## 사용 방법

### AI 편집

1. 영상 파일이 들어있는 **폴더**를 선택합니다
2. 편집 코멘트를 입력합니다 (예: "요리 장면 위주로, 완성본 20분")
3. AI 엔진을 선택합니다 (Ollama 또는 Claude)
4. "분석 시작"을 클릭하면 아래 파이프라인이 자동 실행됩니다:
   - 오디오 추출 (FFmpeg)
   - Stage 1 스캔 (모션/오디오/밝기 메트릭)
   - VAD + STT (Silero VAD + faster-whisper large-v3)
   - Stage 2 비전 태깅 (Qwen2.5-VL:7B — 5프레임 맥락 분석)
   - AI 편집 (Qwen3:14B — 스토리보드 기반 KEEP/CUT)
   - SRT 출력
5. 분석 결과는 `영상폴더/autocut/` 하위에 저장됩니다
6. SRT 파일을 DaVinci Resolve 등 편집기에서 가이드로 활용합니다

### 자막 추출

1. 영상 파일을 선택하거나 YouTube URL을 붙여넣습니다
2. 영상 설명을 입력하면 인식 정확도가 향상됩니다
3. "자막 생성"을 클릭하면 아래 과정이 실행됩니다:
   - 오디오 추출 (FFmpeg → 16kHz WAV)
   - 음성인식 (Silero VAD + faster-whisper large-v3)
   - 맞춤법 교정 (네이버 맞춤법 검사기)
4. 생성된 자막을 편집하고 SRT로 저장합니다
5. 영어/일본어 번역이 필요하면 "번역" 버튼을 사용합니다 (Qwen2.5:14B)
6. 결과는 `영상 위치/subtitle/` 하위에 저장됩니다

### BGM 생성

1. 영상 파일을 선택합니다
2. BGM을 적용할 구간을 설정합니다
3. "분석"을 클릭하면 AI가 장면 분위기를 분석합니다 (LLaMA 3.2 Vision:11B)
4. 생성된 음악 프롬프트를 확인/수정합니다
5. "BGM 생성"을 클릭하면 배경음악이 만들어집니다 (ACE-Step 1.5)
6. 결과는 `영상 위치/bgm/` 하위에 저장됩니다

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
│   ├── subtitle/                # 자막 추출 스크립트
│   ├── bgm/                     # BGM 생성 스크립트
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
│   ├── cache.json
│   ├── analysis.json
│   ├── thumbs/
│   └── 폴더명.srt
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

## 기술 스택

- **프론트엔드**: React, Zustand, TypeScript
- **데스크톱**: Electron, electron-vite
- **AI/ML**: faster-whisper, Ollama (Qwen2.5-VL, Qwen3, LLaMA 3.2 Vision), ACE-Step
- **미디어**: FFmpeg, Silero VAD

## 라이선스

MIT
