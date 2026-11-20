#!/usr/bin/env python3
"""카탈로그에서 음원이 없는 곡을 오디오 보관함 화면에서 하나씩 천천히 내려받아 라이브러리에 넣는다

사용법 (python/bgm-venv 의 Python 3.11 로 실행):
    download.py <browserPageId> <libraryRoot> [--limit N] [--min-free-gb 15]
    download.py --verify <libraryRoot>      받아 둔 음원을 끝까지 디코드해 검사, 불량은 다시 받을 대상으로 되돌림
    download.py --adopt <dir> <libraryRoot>  다른 곳에 받아 둔 mp3 (`제목 - 아티스트.mp3`) 를 검사해 라이브러리로 옮김

- Orca 내장 브라우저에 YouTube Studio 오디오 보관함 탭이 로그인된 상태로 열려 있어야 한다.
- 곡이 속한 (장르, 분위기) 필터를 걸고 화면의 Download 버튼을 누른다. 곡 사이 6~12초 쉰다.
- 기본은 `orca download` 로 라이브러리 디스크의 `.incoming/` 에 바로 받는다 (내장 디스크를 쓰지 않음).
  실제 클릭이 막히면(사용자가 Orca 에서 한글 입력 중 등) 내장 디스크 여유가 있을 때만 ~/Downloads 경유로 받고,
  없으면 기다렸다 다시 시도한다.
- 받은 파일은 이름(`제목 - 아티스트.mp3`)으로 그 곡의 파일인지 확인하고, MP3 머리 정보 길이와
  끝까지 디코드한 실제 길이가 맞을 때만(= 끝까지 받은 파일) `audio/아티스트 - 제목 [곡ID].mp3` 로 옮겨 기록한다.
- 맞지 않거나 다른 곡의 파일은 ~/Downloads/yt-audio-library-rejected/ 로 치운다.
- 라이브러리 디스크 여유가 --min-free-gb(기본 15) 아래면 멈춘다. --min-free-downloads-gb(기본 5) 는
  ~/Downloads 경유 예비 경로를 쓸지 정하는 기준이다. 다시 실행하면 이어서 받는다.
"""
import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "python", "bgm"))

from music_library import audio_rel_path, connect, now_iso, read_manifest, resolve_inside  # noqa: E402
from orca_page import LibraryPage, OrcaError, PageLost  # noqa: E402

DOWNLOADS = os.path.expanduser("~/Downloads")
REJECTED = os.path.join(DOWNLOADS, "yt-audio-library-rejected")
TEMP_SUFFIXES = (".crdownload", ".download", ".part", ".tmp")
HEADER_TOLERANCE = 1.0      # 머리 정보 길이 vs 디코드 길이 (초)
MIN_CATALOG_RATIO = 0.75    # 디코드 길이 ≥ 목록 길이 × 이 값
# 보관함 화면이 빈 장르·분위기에 보여주는 표시
EMPTY_LABELS = {"", "–", "-", "—"}
INCOMING = ".incoming"      # 라이브러리 디스크 안의 받는 중 폴더
MAX_CONSECUTIVE_RECOVERIES = 10  # 곡을 하나도 못 받은 채 보관함으로 되돌아간 횟수가 이만큼이면 멈춘다
MAX_LOST_PER_TRACK = 2           # 한 곡을 받다가 탭이 이만큼 이탈하면 그 곡은 이번 실행에서 실패로 둔다


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def decoded_duration(path: str) -> float | None:
    """파일을 끝까지 디코드해 실제 재생 길이(초)를 잰다. 디코드 오류가 있으면 None."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-stats", "-i", path, "-f", "null", "-"],
                         capture_output=True, text=True)
    lines = out.stderr.replace("\r", "\n").splitlines()
    if out.returncode != 0 or any(l and not l.startswith("size=") for l in lines):
        return None
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", out.stderr)
    if not times:
        return None
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def header_duration(path: str) -> float | None:
    """MP3 머리(Info/Xing) 정보에 적힌 길이. 파일이 잘려도 원래 길이가 나온다."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def is_complete(path: str, expected: float | None) -> bool:
    """끝까지 받은 파일인지 본다.

    - 머리 정보 길이와 실제 디코드 길이가 맞아야 한다 (잘린 파일은 크게 다르다: 164초 대 75초).
      프레임 경계에서 깔끔하게 잘려 디코드 오류가 없는 경우도 이것으로 잡힌다.
    - 보관함 목록 길이는 실제 음원과 2~35초씩 다른 곡이 있어(2026-11-16 확인) 크게 어긋난 경우만 거른다.
    """
    dec = decoded_duration(path)
    hdr = header_duration(path)
    if dec is None or hdr is None or abs(hdr - dec) > HEADER_TOLERANCE:
        return False
    return expected is None or dec >= expected * MIN_CATALOG_RATIO


def free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / 1024 ** 3


def norm(text: str) -> str:
    """파일 이름 비교용: 대소문자·기호·공백을 무시한다 (브라우저가 바꾸는 문자를 피하려고)."""
    text = unicodedata.normalize("NFC", text).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def snapshot_downloads() -> set[str]:
    return set(os.listdir(DOWNLOADS))


def reject(path: str, why: str) -> None:
    os.makedirs(REJECTED, exist_ok=True)
    dest = os.path.join(REJECTED, os.path.basename(path))
    if os.path.exists(dest):
        base, ext = os.path.splitext(dest)
        dest = f"{base} ({int(time.time())}){ext}"
    shutil.move(path, dest)
    log(f"    ↳ 치움 ({why}): {os.path.basename(path)}")


def wait_for_track(before: set[str], track: dict, timeout: float = 120) -> str | None:
    """클릭 뒤 ~/Downloads 에 그 곡의 파일이 다 받아지기를 기다린다.

    - 이름이 `제목 - 아티스트` 로 시작하는 새 mp3 만 그 곡의 파일로 본다.
    - 클릭 뒤 새로 생긴 임시 파일이 남아 있는 동안은 기다린다.
    - 크기가 2초 동안 그대로이고 끝까지 디코드한 길이가 맞으면 완료.
    """
    want = norm(f"{track['title']} - {track['artist'] or ''}")
    deadline = time.time() + timeout
    last_size, stable_since = -1, time.time()
    while time.time() < deadline:
        time.sleep(1)
        new = snapshot_downloads() - before
        if any(n.endswith(TEMP_SUFFIXES) for n in new):
            continue
        mine = [n for n in new if n.lower().endswith(".mp3") and norm(os.path.splitext(n)[0]).startswith(want)]
        if len(mine) != 1:
            continue
        path = os.path.join(DOWNLOADS, mine[0])
        size = os.path.getsize(path)
        if size != last_size:
            last_size, stable_since = size, time.time()
            continue
        if size > 0 and time.time() - stable_since >= 2 and is_complete(path, track["duration_sec"]):
            return path
    return None


def sweep_strays(before: set[str], keep: str | None = None) -> None:
    """이번 클릭과 상관없이 새로 생긴 mp3 (늦게 도착한 앞 곡 파일 등)를 치운다."""
    for n in snapshot_downloads() - before:
        p = os.path.join(DOWNLOADS, n)
        if p != keep and n.lower().endswith(".mp3") and os.path.isfile(p):
            reject(p, "다른 곡이거나 늦게 도착")


def pending_groups(conn, root: str):
    """음원이 없는 곡을 (장르 표시 이름, 분위기 표시 이름) 별로 묶는다."""
    groups: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    skipped = []
    for row in conn.execute("SELECT id, title, artist, duration_sec, audio_path, source_json FROM tracks"):
        if row["audio_path"]:
            full = resolve_inside(root, row["audio_path"])
            if full and os.path.isfile(full):
                continue
        src = json.loads(row["source_json"] or "{}")
        genre, mood = (src.get("genreLabel") or "").strip(), (src.get("moodLabel") or "").strip()
        if genre in EMPTY_LABELS or mood in EMPTY_LABELS:
            skipped.append(row["title"])
            continue
        groups[(genre, mood)][row["id"]] = dict(row)
    return groups, skipped


def store(conn, root: str, track: dict, path: str) -> None:
    rel = audio_rel_path(track["artist"], track["title"], track["id"])
    dest = os.path.join(root, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(path, dest)
    with conn:
        conn.execute("UPDATE tracks SET audio_path = ?, downloaded_at = ? WHERE id = ?", (rel, now_iso(), track["id"]))


def download_direct(page: LibraryPage, conn, root: str, track: dict) -> str:
    """외장(라이브러리) 디스크로 바로 받는다. 'OK' | 'NOTFOUND' | 'BAD' | 'FAILED:...'"""
    incoming = os.path.join(root, INCOMING)
    os.makedirs(incoming, exist_ok=True)
    tmp = os.path.join(incoming, f"{track['id']}.mp3")
    if os.path.exists(tmp):
        os.remove(tmp)
    r = page.download_to(track["id"], tmp)
    if r != "OK":
        if os.path.exists(tmp):
            os.remove(tmp)
        return r
    if not os.path.isfile(tmp) or not is_complete(tmp, track["duration_sec"]):
        if os.path.exists(tmp):
            os.remove(tmp)
        return "BAD"
    store(conn, root, track, tmp)
    return "OK"


def download_via_downloads(page: LibraryPage, conn, root: str, track: dict) -> bool:
    """예비 경로: 페이지 안 클릭으로 받아 ~/Downloads 를 거친다 (실제 클릭이 막힐 때만)."""
    if not page.on_library():
        raise PageLost()
    before = snapshot_downloads()
    page.close_dialogs()
    if not page.download_row(track["id"]):
        return False
    path = wait_for_track(before, track)
    sweep_strays(before, keep=path)
    page.close_dialogs()
    if not path:
        for n in snapshot_downloads() - before:  # 덜 받은 그 곡 파일도 치운다
            p = os.path.join(DOWNLOADS, n)
            if os.path.isfile(p) and not n.endswith(TEMP_SUFFIXES):
                reject(p, "검사 실패")
        return False
    store(conn, root, track, path)
    return True


def download_one(page: LibraryPage, conn, root: str, track: dict, min_free_downloads_gb: float) -> bool:
    for attempt in range(3):
        r = download_direct(page, conn, root, track)
        if r == "OK":
            return True
        if r in ("NOTFOUND", "BAD"):
            if not page.on_library():
                raise PageLost()
            log(f"  ✗ {'화면에 없음' if r == 'NOTFOUND' else '검사 실패'}: {track['title']}")
            return False
        # 실제 클릭이 막힘 (사용자가 Orca 에서 한글 입력 중이거나 창이 가림)
        log(f"  … 바로 받기 실패 ({r[7:120]}), 다시 시도")
        if free_gb(DOWNLOADS) >= min_free_downloads_gb:
            log("  … ~/Downloads 경유로 받습니다")
            ok = download_via_downloads(page, conn, root, track)
            if not ok and not page.on_library():
                raise PageLost()
            return ok
        time.sleep(30 * (attempt + 1))
    log(f"  ✗ 받지 못함: {track['title']}")
    return False


def verify_library(root: str) -> None:
    """받아 둔 음원을 모두 끝까지 디코드해 검사하고, 불량은 audio_path 를 비워 다시 받게 한다."""
    conn = connect(root)
    rows = conn.execute("SELECT id, title, duration_sec, audio_path FROM tracks WHERE audio_path IS NOT NULL").fetchall()
    bad = 0
    for r in rows:
        full = resolve_inside(root, r["audio_path"])
        if full and os.path.isfile(full) and is_complete(full, r["duration_sec"]):
            continue
        bad += 1
        log(f"  불량: {r['title']} ({r['audio_path']})")
        if full and os.path.isfile(full):
            os.remove(full)
        with conn:
            conn.execute("UPDATE tracks SET audio_path = NULL, downloaded_at = NULL WHERE id = ?", (r["id"],))
    conn.close()
    log(f"검사 {len(rows)}곡, 불량 {bad}곡 (다시 받을 대상으로 되돌림)")


def adopt_files(src_dir: str, root: str) -> None:
    """`제목 - 아티스트.mp3` 파일을 이름으로 곡과 맞춰, 검사를 통과하면 라이브러리로 옮긴다."""
    conn = connect(root)
    pending: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute("SELECT id, title, artist, duration_sec, audio_path FROM tracks"):
        if r["audio_path"]:
            full = resolve_inside(root, r["audio_path"])
            if full and os.path.isfile(full):
                continue
        pending[norm(f"{r['title']} - {r['artist'] or ''}")].append(dict(r))
    adopted = 0
    for name in sorted(os.listdir(src_dir)):
        if not name.lower().endswith(".mp3"):
            continue
        path = os.path.join(src_dir, name)
        matches = pending.get(norm(os.path.splitext(name)[0]), [])
        if len(matches) != 1:
            log(f"  - 곡을 하나로 정할 수 없음 ({len(matches)}개): {name}")
            continue
        track = matches[0]
        if not is_complete(path, track["duration_sec"]):
            log(f"  ✗ 검사 실패: {name}")
            continue
        rel = audio_rel_path(track["artist"], track["title"], track["id"])
        dest = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(path, dest)
        with conn:
            conn.execute("UPDATE tracks SET audio_path = ?, downloaded_at = ? WHERE id = ?", (rel, now_iso(), track["id"]))
        pending.pop(norm(os.path.splitext(name)[0]), None)
        adopted += 1
        log(f"  ✓ {name}")
    conn.close()
    log(f"옮김 {adopted}곡")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("args", nargs="+", help="<browserPageId> <libraryRoot> | --verify <libraryRoot>")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--adopt", metavar="DIR")
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 받을 최대 곡 수 (0 = 전부)")
    ap.add_argument("--min-free-gb", type=float, default=15.0)
    ap.add_argument("--min-free-downloads-gb", type=float, default=5.0)
    ap.add_argument("--library-url", help="탭이 보관함을 벗어났을 때 돌아갈 주소 (기본: 시작할 때 탭의 주소)")
    a = ap.parse_args()

    root = os.path.abspath(a.args[-1])
    if read_manifest(root) is None:
        sys.exit(f"음악 라이브러리 폴더가 아닙니다: {root}")
    if a.verify:
        verify_library(root)
        return
    if a.adopt:
        adopt_files(os.path.abspath(a.adopt), root)
        return
    if len(a.args) != 2:
        sys.exit("사용법: download.py <browserPageId> <libraryRoot>")

    page = LibraryPage(a.args[0])
    if a.library_url:
        page.library_url = a.library_url
    if page.on_library():
        page.remember_library_url()
    elif page.library_url:
        log("탭이 보관함 화면이 아니라 보관함으로 돌아갑니다")
        page.recover()
    else:
        sys.exit("탭이 오디오 보관함 화면이 아닙니다. --library-url 로 보관함 주소를 알려 주세요.")
    conn = connect(root)
    incoming = os.path.join(root, INCOMING)
    if os.path.isdir(incoming):  # 지난 실행이 남긴 받는 중 파일 (한 번에 하나만 실행한다는 전제)
        for n in os.listdir(incoming):
            os.remove(os.path.join(incoming, n))
    groups, skipped = pending_groups(conn, root)
    total = sum(len(g) for g in groups.values())
    log(f"받을 곡 {total} (필터 묶음 {len(groups)}개), 장르·분위기가 비어 건너뜀 {len(skipped)}: {skipped}")

    stats = {"done": 0, "failed": 0, "total": total, "since_recovery": 0, "lost": defaultdict(int)}
    recoveries = 0
    for (genre, mood), tracks in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        log(f"[{genre} × {mood}] {len(tracks)}곡")
        while True:
            try:
                if process_group(page, conn, root, genre, mood, tracks, stats, a) == "STOP":
                    return
                break
            except PageLost:
                recoveries = recoveries + 1 if stats["since_recovery"] == 0 else 1
                stats["since_recovery"] = 0
                if recoveries > MAX_CONSECUTIVE_RECOVERIES:
                    log(f"곡을 받지 못한 채 보관함 복구를 {MAX_CONSECUTIVE_RECOVERIES}번 연속 해서 멈춥니다")
                    return
                log(f"  ↺ 탭이 보관함 화면을 벗어났습니다 — 돌아가서 이어 받습니다 (연속 {recoveries}번째)")
                page.recover()
        if tracks:
            log(f"  ! 화면에서 찾지 못한 곡 {len(tracks)}개: {[t['title'] for t in list(tracks.values())[:5]]}")
        time.sleep(random.uniform(4, 8))
    log(f"끝: 이번에 {stats['done']}곡 받음, 실패 {stats['failed']}곡 (다시 실행하면 실패한 곡을 다시 받습니다)")


def process_group(page: LibraryPage, conn, root: str, genre: str, mood: str, tracks: dict, stats: dict, a) -> str | None:
    """한 (장르, 분위기) 묶음을 받는다. 탭이 보관함을 벗어나면 PageLost (받던 곡은 tracks 에 되돌림)."""
    page.set_filters(genre, mood)
    for direction in ("ASC", "DESC"):
        if not tracks:
            return None
        page.ensure_date_order(direction)
        while tracks:
            rows = page.extract()["rows"]
            on_page = [r["track"]["trackId"] for r in rows if r.get("track") and r["track"]["trackId"] in tracks]
            for tid in on_page:
                if free_gb(root) < a.min_free_gb:
                    log(f"{root} 디스크 여유 {free_gb(root):.1f}GB — {a.min_free_gb}GB 아래라 멈춥니다")
                    return "STOP"
                track = tracks.pop(tid)
                try:
                    ok = download_one(page, conn, root, track, a.min_free_downloads_gb)
                except PageLost:
                    stats["lost"][tid] += 1
                    if stats["lost"][tid] <= MAX_LOST_PER_TRACK:
                        tracks[tid] = track  # 돌아가서 다시 받는다
                    else:
                        stats["failed"] += 1
                        log(f"  ✗ 받을 때마다 탭이 이탈해서 이번 실행에서는 건너뜀: {track['title']}")
                    raise
                except (OSError, sqlite3.Error) as e:
                    log(f"  ✗ 오류로 건너뜀: {track['title']} — {e}")
                    ok = False
                if ok:
                    stats["done"] += 1
                    stats["since_recovery"] += 1
                    log(f"  ✓ {stats['done']}/{stats['total']} {track['artist']} - {track['title']}")
                    if a.limit and stats["done"] >= a.limit:
                        log(f"--limit {a.limit} 도달")
                        return "STOP"
                else:
                    stats["failed"] += 1
                time.sleep(random.uniform(6, 12))
            if not tracks:
                return None
            if not page.on_library():
                raise PageLost()
            if not page.next_page():
                break
    return None


if __name__ == "__main__":
    try:
        main()
    except OrcaError as e:
        log(f"Orca 브라우저 조작 실패로 멈춥니다: {e}")
        sys.exit(2)
