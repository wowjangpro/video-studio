# Video Studio

**English** · [한국어](README.ko.md)

A desktop app that bundles three AI video-editing tools into a single tabbed interface.

- **AI Edit** — Analyze a folder of clips and generate a KEEP/CUT editing-guide SRT automatically
- **Subtitle** — Transcribe speech from video to SRT, then spell-check and translate it
- **BGM** — Analyze a clip's mood and generate AI background music

## AI Engine Selection

Each module can be run with either **Ollama** (local GPU) or **Claude** (Claude Code CLI). Engine choice is toggled per-module in the UI and persisted in `localStorage`.

| Module | Stage | Ollama model | Claude option | Default |
|--------|-------|--------------|---------------|---------|
| AI Edit | Stage 2 vision tagging | `qwen2.5vl:7b` | Claude Vision (analyzes frames via the Read tool) | Claude |
| AI Edit | LLM editing decision | `qwen3:14b` | Claude (hybrid first-pass + score-based refinement) | Claude |
| Subtitle | ASR correction | Naver spell checker | Claude (recovers mis-recognized words from context) | Claude |
| Subtitle | Translation (EN / JA) | `qwen2.5:14b` | Claude | Claude |
| BGM | Scene analysis + music prompt | `llama3.2-vision:11b` + `qwen2.5:14b` | Claude Vision (combined in a single call) | Claude |

> When a module runs on Claude it does **not** occupy local GPU / Ollama, so **multiple modules can run concurrently without GPU-memory conflicts** (`resource-manager.ts` reports 0 GB when the `usesClaude` flag is set).

## Requirements

| Item | Version | Install |
|------|---------|---------|
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Python | 3.11 | `brew install python@3.11` |
| FFmpeg | latest | `brew install ffmpeg` |
| Ollama | latest (optional) | `brew install ollama` |
| Claude Code CLI | latest (optional) | [claude.com/claude-code](https://claude.com/claude-code) |

> You only need **one** of Ollama / Claude installed. A module will fail at run time if the engine you selected in its UI is unavailable. If you plan to run every module on Claude, you can skip the Ollama install entirely.

### Claude Code CLI

The Claude engine shells out to the system `claude` binary in pipe mode (`claude -p`) with these options:

- `--model claude-opus-4-7`
- `--output-format json`
- `--no-session-persistence` — does not touch your main Claude Code session
- `--dangerously-skip-permissions` — bypasses permission prompts during automated analysis
- `--json-schema` — forces structured output for editing-classification calls

The `CLAUDECODE` environment variable is stripped from subprocess env to prevent nested sessions. If the CLI is missing or unauthenticated the module surfaces a "Claude CLI not found" error.

### Ollama models (only when using the Ollama engine)

```bash
# AI Edit
ollama pull qwen2.5vl:7b      # Stage 2 vision tagging
ollama pull qwen3:14b          # editing decision

# Subtitle translation
ollama pull qwen2.5:14b        # EN / JA translation

# BGM analysis
ollama pull llama3.2-vision:11b
```

## Install & Run

```bash
# 1. Clone
git clone https://github.com/wowjangpro/video-studio.git
cd video-studio

# 2. Node dependencies
npm install

# 3. Python venvs + dependencies
npm run setup

# 4. (Ollama engine) start the Ollama server
ollama serve

# 5. (Claude engine) confirm the Claude CLI is installed and signed in
claude --version

# 6. Launch the app
npm run dev
```

## Usage

### AI Edit

1. Select a **folder** of video files
2. Enter an editing comment (e.g. "focus on cooking shots, target 20 minutes")
3. Pick an **AI engine**:
   - `Claude (first-pass)` — Claude hybrid (default). Stage 2 vision tagging and the first-pass KEEP/CUT decision run on Claude, then score-based logic refines the result.
   - `Score-based (no LLM)` — metric-only decisions; fastest and fully deterministic.
4. Click **Start analysis** — the pipeline runs:
   - Audio extraction (FFmpeg)
   - Stage 1 scan (motion / audio / brightness metrics)
   - VAD + STT (Silero VAD + faster-whisper large-v3)
   - Stage 2 vision tagging (Qwen2.5-VL:7B **or Claude Vision** — 5-frame context, batched on Claude)
   - Speech-group classification A/B/C/D (Claude — protects to-camera lines)
   - AI editing (Qwen3:14B **or Claude** — storyboard-driven KEEP/CUT)
   - Silence trim (STT-timed silences ≥ 1 s collapsed to 1 s)
   - SRT / EDL / FCPXML output (DaVinci Resolve compatible)
5. Output is written under `<videoFolder>/autocut/` (supports resume via `cache.json`)
6. Import the SRT / EDL / FCPXML into DaVinci Resolve (or your NLE of choice) as an editing guide

### Subtitle

1. Pick a video file or paste a YouTube URL
2. A short video description improves recognition accuracy
3. Pick an **ASR correction engine**:
   - `Claude correction` (default) — restores mis-recognized words from context
   - `Naver spell checker` — focused on spelling / spacing
4. Click **Generate subtitles**:
   - Audio extraction (FFmpeg → 16 kHz WAV)
   - Speech recognition (Silero VAD + faster-whisper large-v3)
   - Correction (selected engine)
5. Edit the subtitles inline and save as SRT
6. For EN / JA translation, pick a **translation engine** (Claude / Ollama `qwen2.5:14b`) in the toolbar and click **Translate**
7. Output is written under `<video location>/subtitle/`

### BGM

1. Pick a video file
2. Set the time range you want BGM for
3. Pick an **analysis engine**:
   - `Claude` (default) — Claude Vision handles scene analysis **and** music-prompt generation in a single call
   - `Ollama` — LLaMA 3.2 Vision:11B (scene analysis) + Qwen2.5:14B (prompt generation)
4. Click **Analyze** to inspect the scene
5. Review / edit the generated music prompt
6. Click **Generate BGM** to render the track (ACE-Step 1.5, uses `bgm-venv`)
7. Output is written under `<video location>/bgm/`

## Project Structure

```
video-studio/
├── src/
│   ├── main/                    # Electron main process
│   │   ├── index.ts             # app entry
│   │   ├── ipc-handlers.ts      # IPC router
│   │   ├── services/            # shared services (Python, FFmpeg, resource manager)
│   │   └── modules/             # per-module IPC (autocut, subtitle, bgm)
│   ├── preload/index.ts         # contextBridge API
│   └── renderer/src/
│       ├── App.tsx              # tab-based routing
│       ├── common/              # shared (TabBar, app-store, styles)
│       ├── autocut/             # AI Edit (components + store + CSS)
│       ├── subtitle/            # Subtitle
│       └── bgm/                 # BGM
├── python/
│   ├── autocut/                 # AI Edit scripts
│   │   ├── claude_client.py     # Claude CLI wrapper (text + vision)
│   │   ├── stage2.py            # Ollama / Claude vision tagging
│   │   ├── storyboard.py        # hybrid editing + speech-group classification
│   │   └── ...
│   ├── subtitle/                # Subtitle scripts
│   │   ├── spellcheck_claude.py / spellcheck.py
│   │   ├── translate_claude.py / translate.py
│   │   └── translate_text_claude.py / translate_text.py
│   ├── bgm/                     # BGM scripts
│   │   ├── analyze_claude.py    # Claude Vision combined analysis
│   │   ├── generate.py          # ACE-Step BGM generation
│   │   └── ...
│   ├── shared-venv/             # subtitle + autocut shared venv
│   ├── bgm-venv/                # bgm-only venv (torch)
│   └── setup.sh                 # venv setup script
└── out/                         # build output
```

## Output Layout

Each module writes its files under a sub-directory of the source video folder:

```
videoFolder/
├── video1.mp4
├── autocut/                    # AI Edit results
│   ├── cache.json              # resume state
│   ├── analysis.json           # analysis metadata
│   ├── thumbs/                 # Stage 2 vision input frames
│   ├── <folder>.srt            # editing-guide SRT
│   ├── <folder>.edl            # EDL (24 fps NDF)
│   └── <folder>.fcpxml         # FCPXML (recommended for DaVinci Resolve)
├── subtitle/                   # Subtitle results
│   ├── video1.srt
│   ├── video1_en.srt
│   └── video1_jp.srt
└── bgm/                        # BGM results
    └── video1_bgm.wav
```

## Build & Distribute

```bash
# dev mode
npm run dev

# production build (outputs to out/)
npm run build

# macOS .app packaging
npm run dist
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server address |
| `CLAUDECODE` | (auto-stripped) | Removed from Python subprocess env to prevent nested sessions |

## Tech Stack

- **Frontend**: React, Zustand, TypeScript
- **Desktop**: Electron, electron-vite
- **AI / ML**:
  - faster-whisper (STT)
  - Ollama (Qwen2.5-VL, Qwen3, LLaMA 3.2 Vision, Qwen2.5)
  - Claude Code CLI (`claude -p`, Opus 4.7) — used for both vision and text
  - ACE-Step 1.5 (BGM generation)
- **Media**: FFmpeg, Silero VAD

## License

MIT
