#!/usr/bin/env python3
"""오디오 보관함 곡 목록(메타데이터)을 모아 라이브러리 카탈로그에 넣는다

사용법 (python/bgm-venv 의 Python 3.11 로 실행):
    collect.py <browserPageId> <libraryRoot> [--full]

- 기본(새 곡만): 필터 없이 최신 순으로 넘기다가, 새 곡이 하나도 없는 페이지가 나오면 멈춘다.
  보관함은 매달 곡이 추가되므로 정기 갱신은 이것으로 충분하다.
- --full (처음 한 번): 보관함 목록은 한 번에 2,000곡까지만 넘길 수 있어서
  ① 최신 순 / 오래된 순 양 끝, ② 장르 × 분위기 140개 조합(조합마다 정확한 곡 수가 나온다),
  ③ 넘김이 막힌 조합은 제목·길이·아티스트 정렬로 다시 훑는다.
- 모은 행은 <root>/sources/collected-YYYY-MM-DD.jsonl 에 원본 그대로 쌓고, 끝나면 카탈로그로 가져온다.
- 화면 조작 사이 4~9초 쉰다. download.py 와 같은 탭을 쓰므로 동시에 돌리지 않는다.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BGM_DIR = os.path.join(HERE, "..", "..", "python", "bgm")
sys.path.insert(0, HERE)
sys.path.insert(0, BGM_DIR)

from music_library import connect, read_manifest  # noqa: E402
from orca_page import GENRES, MOODS, PAGE_CAP, LibraryPage, OrcaError  # noqa: E402


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Collector:
    def __init__(self, page: LibraryPage, out_path: str, known: set[str]):
        self.page = page
        self.out = open(out_path, "a", encoding="utf-8")
        self.known = known          # 카탈로그에 이미 있는 곡
        self.seen: set[str] = set()  # 이번 실행에서 기록한 곡

    def take(self, rows) -> int:
        """이번 실행에서 처음 본 행을 기록하고, 카탈로그에 없던 곡 수를 돌려준다."""
        new = 0
        for row in rows:
            tid = (row.get("track") or {}).get("trackId")
            if not tid or tid in self.seen:
                continue
            self.seen.add(tid)
            self.out.write(json.dumps(row, ensure_ascii=False) + "\n")
            if tid not in self.known:
                new += 1
        self.out.flush()
        return new

    def pages(self, label: str, stop_when_no_new: bool = False) -> bool:
        """현재 필터·정렬로 끝까지 넘긴다. 넘김이 막혀 멈췄으면 True."""
        while True:
            d = self.page.extract()
            first = d["footer"]["firstElementIndex"]
            new = self.take(d["rows"])
            log(f"  [{label}] {first}~ {len(d['rows'])}행, 새 곡 {new}, 누적 {len(self.seen)}")
            if stop_when_no_new and new == 0:
                return False
            if not d["footer"].get("hasNext") or first + len(d["rows"]) > PAGE_CAP:
                return False
            time.sleep(random.uniform(5, 9))
            if not self.page.next_page():
                log(f"  [{label}] {first}~ 에서 넘김이 막힘")
                return True

    def clear_filters(self):
        """필터 칩을 하나씩 지운다 (지울 때마다 칩 목록이 다시 그려진다)."""
        for _ in range(6):
            left = self.page.ev_int("(() => { const x = document.querySelector('ytmus-library-filter ytcp-chip #delete-icon');"
                                    " if (!x) return 0; x.click(); return 1 })()")
            if not left:
                return
            time.sleep(3)
        raise OrcaError("필터 칩을 모두 지우지 못했습니다")

    def run_new(self):
        self.clear_filters()
        self.page.ensure_date_order("DESC")
        self.pages("최신 순", stop_when_no_new=True)

    def run_full(self):
        self.clear_filters()
        for direction in ("DESC", "ASC"):
            self.page.ensure_date_order(direction)
            self.pages(f"전체 {direction}")
        stalled = []
        for genre in GENRES:
            for mood in MOODS:
                st = self.page.set_filters(genre, mood)
                label = f"{genre} × {mood}"
                if st["n"] == 0:
                    continue
                self.page.ensure_date_order("ASC")
                if self.pages(label):
                    self.page.ensure_date_order("DESC")
                    if self.pages(label + " DESC"):
                        stalled.append((genre, mood))
                time.sleep(random.uniform(4, 7))
        for genre, mood in stalled:
            self.page.set_filters(genre, mood)
            for col in ("Track title", "Duration", "Artist"):
                for _ in range(2):
                    self.page.click_header(col)
                    self.pages(f"{genre} × {mood} / {col}")
                    time.sleep(random.uniform(4, 7))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("page_id")
    ap.add_argument("root")
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if read_manifest(root) is None:
        sys.exit(f"음악 라이브러리 폴더가 아닙니다: {root}")
    conn = connect(root)
    known = {r[0] for r in conn.execute("SELECT id FROM tracks")}
    conn.close()

    out_path = os.path.join(root, "sources", f"collected-{time.strftime('%Y-%m-%d')}.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    c = Collector(LibraryPage(args.page_id), out_path, known)
    try:
        c.run_full() if args.full else c.run_new()
    except OrcaError as e:
        log(f"Orca 브라우저 조작 실패로 멈춥니다 (모은 것까지는 저장됨): {e}")
    finally:
        c.out.close()

    new_ids = c.seen - known
    log(f"이번에 본 곡 {len(c.seen)}, 새 곡 {len(new_ids)} → 카탈로그로 가져옵니다")
    subprocess.run([sys.executable, os.path.join(BGM_DIR, "library_import.py"), root, out_path], check=False)


if __name__ == "__main__":
    main()
