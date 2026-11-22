# CLAUDE.md

- 대답은 항상 한글로

## Project

3개의 독립 Electron 앱(auto-subtitle, bgm-studio, ai-movie-cut)을 하나로 통합한 영상 편집 도구 (수동 도구).
탭 기반 UI: AI 편집 · 자막 추출 · BGM 추천(유튜브 오디오 보관함) · 썸네일.
촬영 → 업로드 자동화는 별도 프로젝트 `../video-pipeline/` (명령줄 + 스킬)가 이 리포의 `python/` 모듈과 가상환경을 불러 쓴다.

**AI 는 Claude 만 쓴다** — 로컬 모델(Ollama · qwen 등)은 더 이상 쓰지 않는다 (2026-11-22). 코드에 남은 Ollama 경로
(자막 번역 · 교정의 Ollama 선택지, AI 편집의 엔진 선택 등)는 예전 것이라 쓰지 않는다.

## Architecture

```
video-studio/
├── src/main/
│   ├── index.ts                 # Electron 메인 프로세스 엔트리
│   ├── ipc-handlers.ts          # 통합 IPC 라우터
│   ├── services/
│   │   ├── python.ts            # 모듈별 Python spawn (venv 자동 선택)
│   │   ├── resource-manager.ts  # GPU 리소스 충돌 관리 (예전 Ollama 용 — 지금은 쓰지 않음)
│   │   ├── health-check.ts      # 환경 검증 (Python/FFmpeg, Ollama 항목은 예전 것)
│   │   ├── ffmpeg.ts            # 공유 FFmpeg 유틸
│   │   └── srt.service.ts       # SRT 파일 읽기/쓰기
│   └── modules/
│       ├── autocut/ipc.ts       # autocut:* IPC 핸들러
│       ├── subtitle/ipc.ts      # subtitle:* IPC 핸들러
│       ├── bgm/ipc.ts           # bgm:* IPC 핸들러
│       └── thumbnail/ipc.ts     # thumbnail:* IPC 핸들러
├── src/preload/index.ts         # 통합 preload (모듈별 API)
├── src/renderer/src/
│   ├── App.tsx                  # 탭바 + 모듈 조건부 렌더링
│   ├── common/                  # 공통 (TabBar, app-store, global.css, tabs.css)
│   ├── autocut/                 # AI 편집 (컴포넌트 + autocut-store + autocut.css)
│   ├── subtitle/                # 자막 추출 (컴포넌트 + subtitle-store + subtitle.css)
│   ├── bgm/                     # BGM 추천 (컴포넌트 + bgm-store + bgm.css)
│   └── thumbnail/               # 썸네일 (컴포넌트 + thumbnail-store + thumbnail.css)
└── python/
    ├── autocut/                 # AI 편집 (분석 analyze.py · 스토리 엔진 story_planner/story_editor · EDL/FCPXML)
    ├── subtitle/                # 자막 (transcribe · spellcheck_claude · translate_claude)
    ├── bgm/                     # BGM 추천 (오디오 보관함 라이브러리 · CLAP 검색 · 다빈치 FCPXML 내보내기)
    ├── thumbnail/               # 썸네일 (Claude 콘셉트 · 문구, 스타일 7종 · 저작권 무료 글꼴만)
    ├── shared-venv/             # subtitle + autocut + thumbnail 공유 venv
    ├── bgm-venv/                # bgm 전용 venv (torch · CLAP · AST)
    └── setup.sh                 # 두 venv 모두 생성
```

## Key Design

- **통신**: Python stdout JSON Lines (`{"type": "progress", ...}`)
- **IPC 네임스페이스**: `{module}:{action}` (예: `autocut:start-analysis`)
- **상태 관리**: 모듈별 독립 Zustand 스토어
- **Python venv**: shared-venv (subtitle+autocut) / bgm-venv (torch 충돌 분리)

## Commands

```bash
npm run dev              # Electron + React 핫 리로드
npm run build            # 프로덕션 빌드
npm run setup            # Python venv 생성 + 의존성 설치
```

## External Dependencies

- **Claude Code CLI (`claude -p`)**: 영상 분석(장면 설명 · Claude 비전), 스토리 편집, 자막 교정 · 번역, BGM 장면 분석, 썸네일
- **FFmpeg**: 오디오/프레임 추출
- **faster-whisper**: Silero VAD + 음성 인식 (로컬, large-v3)
- **CLAP · AST (bgm-venv)**: BGM 라이브러리 검색 · 소리 태그 (로컬 임베딩 모델 — 생성 모델 아님)
- **유튜브 오디오 보관함 라이브러리**: 검색 데이터는 로컬 볼륨, 음원은 NAS (`library.json` 의 audio_dir)
- 글꼴: 결과물에는 저작권 무료 글꼴만 (배민 · 카페24 · 교보손글씨 2020 · 나눔스퀘어라운드 · 강원교육모두) — 맥 기본 글꼴 금지

## Sister Projects (원본 참조)

- `../auto-subtitle/` — 자막 추출 원본
- `../bgm-studio/` — BGM 생성 원본
- `../ai-movie-cut/` — AI 편집 원본
