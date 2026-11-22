#!/usr/bin/env python3
"""음악 라이브러리(YouTube 오디오 보관함 로컬 사본) 열기·초기화

라이브러리는 폴더 하나로 완결된다. 폴더를 통째로 옮겨도 동작하도록
DB 안의 파일 위치는 모두 라이브러리 루트 기준 상대 경로로만 저장한다.

    <root>/
      library.json     라이브러리 식별자 + 형식 버전
      catalog.sqlite   곡 메타데이터 · 분석 결과
      audio/           음원 파일 (library.json 의 audio_dir 로 다른 곳 — 예: NAS — 을 가리킬 수 있다)
      usage/           영상별 사용 기록
      sources/         보관함에서 수집한 원본 목록 (JSON Lines, 수집일별)

사용법:
    music_library.py open <root>   기존 라이브러리를 열어 곡 목록을 출력
    music_library.py init <root>   새 라이브러리를 만든 뒤 open 과 같은 출력
    music_library.py set-audio <root> <dir>
                                   음원 폴더를 바꾼 뒤 open 과 같은 출력 (라이브러리 안 audio/ 를 고르면 기본값으로 되돌림)

출력(JSON Lines, 마지막 한 줄):
    {"type": "library", "info": {...}, "tracks": [...]}
    {"type": "not_library", "root": "...", "empty": bool}
    {"type": "error", "message": "..."}
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

LIBRARY_FORMAT = "yt-audio-library"
FORMAT_VERSION = 1
MANIFEST_NAME = "library.json"
CATALOG_NAME = "catalog.sqlite"
SUBDIRS = ("audio", "usage", "sources")
AUDIO_SUBDIR = "audio"
# library.json 에 적는 음원 폴더 (없으면 <root>/audio). 절대 경로 또는 루트 기준 상대 경로.
# 검색 데이터(catalog.sqlite)는 빠른 디스크에, 음원(45GB)은 NAS 에 두려고 나눌 수 있게 했다 (2026-11-22).
AUDIO_DIR_KEY = "audio_dir"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 보관함에서 수집한 곡. source_json 에는 보관함 원본 메타데이터를 가공 없이 보관한다
-- (유튜브 설명란 출처 문구는 원본 기준이어야 하므로).
CREATE TABLE IF NOT EXISTS tracks (
    id               TEXT PRIMARY KEY,   -- 보관함 곡 ID
    title            TEXT NOT NULL,
    artist           TEXT,
    genre            TEXT,               -- 보관함 표시 이름, 여러 개면 ", " 로 연결
    mood             TEXT,
    instruments      TEXT,
    duration_sec     REAL,
    license_type     TEXT,               -- attribution_required | attribution_not_required
    license_text     TEXT,               -- 보관함 라이선스 안내 원문 (줄바꿈 구분)
    attribution_text TEXT,               -- 보관함이 제공한 저작자 표시 문구 원문
    artist_url       TEXT,
    published_at     TEXT,
    audio_path       TEXT,               -- 루트 기준 상대 경로 (다운로드 전이면 NULL)
    source_json      TEXT,
    collected_at     TEXT,
    downloaded_at    TEXT
);

-- 로컬 분석 결과 (원본 음원이 있으면 언제든 다시 만들 수 있다)
CREATE TABLE IF NOT EXISTS analysis (
    track_id         TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    bpm              REAL,
    musical_key      TEXT,
    has_vocals       INTEGER,            -- 1 | 0 | NULL(미판정)
    features_json    TEXT,
    analyzer_version TEXT,
    analyzed_at      TEXT
);

-- 소리 임베딩 (CLAP). 장면 설명 문장과 같은 공간이라 문장으로 곡·구간을 찾을 수 있다.
-- mean: 곡 전체 평균 (float32 × dim), segments: segment_sec 간격 구간별 (float16 × n × dim)
CREATE TABLE IF NOT EXISTS embeddings (
    track_id    TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    segment_sec REAL NOT NULL,
    n_segments  INTEGER NOT NULL,
    mean        BLOB NOT NULL,
    segments    BLOB NOT NULL
);
"""


def emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False), flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_manifest(root: str) -> dict | None:
    path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("format") != LIBRARY_FORMAT:
        return None
    return manifest


def audio_base(root: str, manifest: dict | None = None) -> str:
    """음원 폴더의 절대 경로."""
    manifest = manifest if manifest is not None else (read_manifest(root) or {})
    custom = manifest.get(AUDIO_DIR_KEY)
    if isinstance(custom, str) and custom.strip():
        return os.path.abspath(os.path.join(root, custom))
    return os.path.abspath(os.path.join(root, AUDIO_SUBDIR))


def audio_file(base: str, rel_path: str) -> str | None:
    """DB 의 audio_path(`audio/파일.mp3`)를 음원 폴더 안의 절대 경로로 바꾼다. 폴더 밖을 가리키면 None."""
    if not rel_path or os.path.isabs(rel_path):
        return None
    prefix = AUDIO_SUBDIR + "/"
    name = rel_path[len(prefix):] if rel_path.startswith(prefix) else rel_path
    # realpath 를 쓰지 않는다 — NAS 에서는 곡마다 네트워크 조회가 생겨 7천 곡 목록·추천이 느려진다.
    # 경로 문자열만으로 폴더 밖(../)을 막는다 (파일 이름은 우리가 만든 것이라 심볼릭 링크는 없다).
    base = os.path.normpath(base)
    full = os.path.normpath(os.path.join(base, name))
    if os.path.commonpath([base, full]) != base:
        return None
    return full


def write_manifest(root: str, manifest: dict) -> None:
    path = os.path.join(root, MANIFEST_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def set_audio_dir(root: str, chosen: str) -> None:
    """음원 폴더를 바꾼다. 라이브러리 폴더 통째 사본(안에 audio/ 가 있는 폴더)을 골라도 그 audio/ 를 쓴다."""
    manifest = read_manifest(root)
    if manifest is None:
        raise RuntimeError(f"음악 라이브러리 폴더가 아닙니다: {root}")
    chosen = os.path.abspath(chosen)
    if not os.path.isdir(chosen):
        raise RuntimeError(f"음원 폴더를 찾을 수 없습니다: {chosen}")
    nested = os.path.join(chosen, AUDIO_SUBDIR)
    if os.path.basename(chosen.rstrip("/")) != AUDIO_SUBDIR and os.path.isdir(nested):
        chosen = nested
    default = os.path.realpath(os.path.join(root, AUDIO_SUBDIR))
    if os.path.realpath(chosen) == default:
        manifest.pop(AUDIO_DIR_KEY, None)
    else:
        # 라이브러리 안이면 상대 경로로 적어 폴더째 옮겨도 맞게 한다
        root_real = os.path.realpath(root)
        real = os.path.realpath(chosen)
        inside = os.path.commonpath([root_real, real]) == root_real
        manifest[AUDIO_DIR_KEY] = os.path.relpath(real, root_real) if inside else chosen
    write_manifest(root, manifest)


# ExFAT · NTFS 에서 파일 이름에 쓸 수 없는 문자
_UNSAFE_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


def audio_rel_path(artist: str | None, title: str, track_id: str, ext: str = ".mp3") -> str:
    """음원 파일의 라이브러리 내 상대 경로 — `audio/아티스트 - 제목 [곡ID].mp3`

    어떤 디스크(ExFAT 포함)로 옮겨도 쓸 수 있는 이름만 만든다.
    """
    def clean(text: str) -> str:
        text = "".join("_" if ch in _UNSAFE_CHARS else ch for ch in text)
        return " ".join(text.split()).strip(" .")

    stem = clean(f"{artist} - {title}" if artist else title) or "track"
    # 파일 이름 한도는 255 바이트 → 곡 ID · 확장자 자리를 남기고 UTF-8 바이트 기준으로 자른다
    raw = stem.encode("utf-8")[:180]
    stem = raw.decode("utf-8", errors="ignore").rstrip(" .") or "track"
    return f"audio/{stem} [{clean(track_id)}]{ext}"


def resolve_inside(root: str, rel_path: str) -> str | None:
    """상대 경로를 루트 안의 절대 경로로 바꾼다. 루트 밖을 가리키면 None."""
    if not rel_path or os.path.isabs(rel_path):
        return None
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_real, rel_path))
    if os.path.commonpath([root_real, full]) != root_real:
        return None
    return full


# 이전 형식으로 만든 catalog 에 나중에 추가된 칸 (이름, 타입)
ADDED_TRACK_COLUMNS = (
    ("instruments", "TEXT"),
    ("license_text", "TEXT"),
    ("artist_url", "TEXT"),
    ("published_at", "TEXT"),
)


# 다른 프로세스(분석기·다운로더·앱)가 쓰는 중이면 이만큼 기다린다
BUSY_TIMEOUT_SEC = 30
REQUIRED_TABLES = {"meta", "tracks", "analysis", "embeddings"}


def connect(root: str) -> sqlite3.Connection:
    """카탈로그를 연다. 스키마·칸이 모자랄 때만 쓴다.

    열 때마다 쓰기(스키마 실행·meta 기록)를 하면, 긴 분석이 도는 중에 앱이 라이브러리를 열기만 해도
    잠금이 겹쳐 분석기가 "database is locked" 로 죽었다 (2026-11-18).
    """
    conn = sqlite3.connect(os.path.join(root, CATALOG_NAME), timeout=BUSY_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    changed = False
    if not REQUIRED_TABLES <= tables:
        conn.executescript(SCHEMA)
        changed = True
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(tracks)")}
    for name, col_type in ADDED_TRACK_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {col_type}")
            changed = True
    if not conn.execute("SELECT 1 FROM meta WHERE key = 'format_version'").fetchone():
        conn.execute("INSERT INTO meta (key, value) VALUES ('format_version', ?)", (str(FORMAT_VERSION),))
        changed = True
    if changed:
        conn.commit()
    return conn


def init_library(root: str) -> None:
    os.makedirs(root, exist_ok=True)
    if read_manifest(root) is None:
        # 같은 이름의 다른 파일을 덮어쓰거나 남의 DB 에 테이블을 추가하지 않는다
        taken = [n for n in (MANIFEST_NAME, CATALOG_NAME) if os.path.exists(os.path.join(root, n))]
        if taken:
            raise RuntimeError(
                f"폴더에 이미 {', '.join(taken)} 파일이 있어 새 라이브러리를 만들 수 없습니다. 다른 폴더를 선택하세요."
            )
        manifest = {
            "format": LIBRARY_FORMAT,
            "format_version": FORMAT_VERSION,
            "source": "YouTube Studio 오디오 보관함",
            "created_at": now_iso(),
        }
        write_manifest(root, manifest)
    for sub in SUBDIRS:
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    connect(root).close()


def load_library(root: str) -> tuple[dict, list[dict]]:
    manifest = read_manifest(root)
    conn = connect(root)
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.artist, t.genre, t.mood, t.instruments, t.duration_sec,
                   t.license_type, t.attribution_text, t.audio_path,
                   a.bpm, a.has_vocals, a.analyzed_at
            FROM tracks t
            LEFT JOIN analysis a ON a.track_id = t.id
            ORDER BY t.title COLLATE NOCASE, t.artist COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()

    # 곡마다 파일이 있는지 보지 않는다 — NAS 에서는 7천 곡을 하나씩 확인하느라 열기가 느려진다.
    # 받은 곡 여부는 DB(audio_path) 기준이고, 음원 폴더가 보이는지만 한 번 확인한다.
    base = audio_base(root, manifest)
    base_ok = os.path.isdir(base)
    tracks = []
    downloaded = 0
    analyzed = 0
    for r in rows:
        abs_path = audio_file(base, r["audio_path"]) if r["audio_path"] else None
        downloaded += abs_path is not None
        analyzed += r["analyzed_at"] is not None
        tracks.append({
            "id": r["id"],
            "title": r["title"],
            "artist": r["artist"],
            "genre": r["genre"],
            "mood": r["mood"],
            "instruments": r["instruments"],
            "durationSec": r["duration_sec"],
            "licenseType": r["license_type"],
            "attributionText": r["attribution_text"],
            "audioPath": abs_path if base_ok else None,
            "bpm": r["bpm"],
            "hasVocals": None if r["has_vocals"] is None else bool(r["has_vocals"]),
            "analyzed": r["analyzed_at"] is not None,
        })

    return {
        "root": os.path.abspath(root),
        "formatVersion": manifest.get("format_version") if manifest else FORMAT_VERSION,
        "createdAt": manifest.get("created_at") if manifest else None,
        "trackCount": len(tracks),
        "downloadedCount": downloaded,
        "analyzedCount": analyzed,
        "audioDir": base,
        "audioDirCustom": bool((manifest or {}).get(AUDIO_DIR_KEY)),
        "audioDirOk": base_ok,
    }, tracks


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in ("open", "init", "set-audio") or (
            sys.argv[1] == "set-audio" and len(sys.argv) < 4):
        emit({"type": "error", "message": "사용법: music_library.py open|init <root> · set-audio <root> <dir>"})
        sys.exit(1)

    command, root = sys.argv[1], sys.argv[2]

    try:
        if command == "init":
            init_library(root)
        elif command == "set-audio":
            set_audio_dir(root, sys.argv[3])
        else:
            if not os.path.isdir(root):
                emit({"type": "error", "message": f"라이브러리 폴더를 찾을 수 없습니다: {root}"})
                sys.exit(1)
            if read_manifest(root) is None:
                visible = [n for n in os.listdir(root) if not n.startswith(".")]
                emit({"type": "not_library", "root": root, "empty": len(visible) == 0})
                return

        info, tracks = load_library(root)
        emit({"type": "library", "info": info, "tracks": tracks})
    except Exception as e:  # noqa: BLE001 — 모든 실패를 화면에 전달
        what = "음원 폴더를 바꾸지 못했습니다" if command == "set-audio" else "라이브러리를 열지 못했습니다"
        emit({"type": "error", "message": f"{what}: {e}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
