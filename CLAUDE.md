# CLAUDE.md

- 대답은 항상 한글로

## Project

3개의 독립 Electron 앱(auto-subtitle, bgm-studio, ai-movie-cut)을 하나로 통합한 영상 편집 도구.
탭 기반 UI로 자막 추출, BGM 생성, AI 편집을 동시에 사용 가능.

## Architecture

```
video-studio/
├── src/main/
│   ├── index.ts                 # Electron 메인 프로세스 엔트리
│   ├── ipc-handlers.ts          # 통합 IPC 라우터
│   ├── services/
│   │   ├── python.ts            # 모듈별 Python spawn (venv 자동 선택)
│   │   ├── resource-manager.ts  # GPU 리소스 충돌 관리 (Ollama)
│   │   ├── health-check.ts      # 환경 검증 (Python/FFmpeg/Ollama)
│   │   ├── ffmpeg.ts            # 공유 FFmpeg 유틸
│   │   └── srt.service.ts       # SRT 파일 읽기/쓰기
│   └── modules/
│       ├── autocut/ipc.ts       # autocut:* IPC 핸들러
│       ├── subtitle/ipc.ts      # subtitle:* IPC 핸들러
│       └── bgm/ipc.ts           # bgm:* IPC 핸들러
├── src/preload/index.ts         # 통합 preload (모듈별 API)
├── src/renderer/src/
│   ├── App.tsx                  # 탭바 + 모듈 조건부 렌더링
│   ├── stores/
│   │   ├── app-store.ts         # 공통 상태 (activeModule)
│   │   ├── autocut-store.ts     # AI 편집 상태
│   │   ├── subtitle-store.ts    # 자막 추출 상태
│   │   └── bgm-store.ts         # BGM 생성 상태
│   ├── components/
│   │   ├── common/              # TabBar 등 공유 컴포넌트
│   │   ├── autocut/             # AI 편집 컴포넌트
│   │   ├── subtitle/            # 자막 추출 컴포넌트
│   │   └── bgm/                 # BGM 생성 컴포넌트
│   └── styles/
│       ├── global.css           # 공통 + autocut 기본 스타일
│       ├── tabs.css             # 탭바 스타일
│       ├── autocut.css          # autocut 모듈 전용
│       ├── subtitle.css         # subtitle 모듈 전용
│       └── bgm.css              # bgm 모듈 전용
└── python/
    ├── autocut/                 # AI 편집 Python 스크립트
    ├── subtitle/                # 자막 추출 Python 스크립트
    ├── bgm/                     # BGM 생성 Python 스크립트
    ├── shared-venv/             # subtitle + autocut 공유 venv
    ├── bgm-venv/                # bgm 전용 venv (torch)
    └── setup.sh                 # 두 venv 모두 생성
```

## Key Design

- **통신**: Python stdout JSON Lines (`{"type": "progress", ...}`)
- **IPC 네임스페이스**: `{module}:{action}` (예: `autocut:start-analysis`)
- **상태 관리**: 모듈별 독립 Zustand 스토어
- **리소스 관리**: GPU 메모리 합계 32GB 초과 시 충돌 경고
- **Python venv**: shared-venv (subtitle+autocut) / bgm-venv (torch 충돌 분리)

## Commands

```bash
npm run dev              # Electron + React 핫 리로드
npm run build            # 프로덕션 빌드
npm run setup            # Python venv 생성 + 의존성 설치
```

## External Dependencies

- **FFmpeg**: 오디오/프레임 추출
- **Ollama**: 비전 태깅(qwen2.5vl:7b), 편집 추론(exaone3.5:7.8b), 자막 교정(qwen2.5:14b), BGM 분석(llama3.2-vision:11b)
- **faster-whisper**: Silero VAD + 음성 인식
- **ACE-Step**: BGM 생성 (bgm-venv)

## Sister Projects (원본 참조)

- `../auto-subtitle/` — 자막 추출 원본
- `../bgm-studio/` — BGM 생성 원본
- `../ai-movie-cut/` — AI 편집 원본
