#!/usr/bin/env python3
"""오디오 보관함에서 수집한 원본 곡 데이터(JSON Lines)를 라이브러리 카탈로그에 넣는다

입력 한 줄 = 보관함 목록 한 행:
    {"track": {...보관함 원본...}, "genreLabel": "Rock", "moodLabel": "Angry",
     "licenseText": ["YouTube Audio Library License", "...", "No attribution is required."]}

- 같은 곡 ID 가 이미 있으면 메타데이터만 갱신하고, 음원 위치·다운로드 시각은 유지한다.
- 원본 행은 source_json 에 그대로 보관한다.

사용법:
    library_import.py <root> <tracks.jsonl>
출력(JSON Lines): {"type": "imported", "inserted": n, "updated": n, "total": n}
"""

import json
import sys
from datetime import datetime, timezone

from music_library import connect, emit, now_iso, read_manifest

ENUM_PREFIXES = {
    "genre": "CREATOR_MUSIC_GENRE_",
    "mood": "CREATOR_MUSIC_MOOD_",
    "instrument": "CREATOR_MUSIC_INSTRUMENT_",
}
FREE_LICENSE = "CREATOR_MUSIC_LICENSE_TYPE_YOUTUBE"


def enum_to_label(value: str, prefix: str) -> str:
    """보관함 화면에 표시 이름이 없을 때 쓰는 대체 이름 (CREATOR_MUSIC_GENRE_HIP_HOP → Hip Hop)"""
    words = value.removeprefix(prefix).split("_")
    return " ".join("&" if w == "AND" else w.capitalize() for w in words)


def build_label_maps(rows: list[dict]) -> dict[str, dict[str, str]]:
    """화면에 보인 이름(genreLabel/moodLabel)으로 enum → 표시 이름 표를 만든다.

    화면은 첫 번째 값만 보여주므로 첫 번째 enum 과 짝지어 모은다.
    """
    maps: dict[str, dict[str, str]] = {"genre": {}, "mood": {}}
    for row in rows:
        attrs = (row.get("track") or {}).get("attributes") or {}
        for kind, key, label_key in (("genre", "genres", "genreLabel"), ("mood", "moods", "moodLabel")):
            values = attrs.get(key) or []
            label = (row.get(label_key) or "").strip()
            if values and label and "\n" not in label:
                maps[kind].setdefault(values[0], label)
    return maps


def labels(values: list[str], kind: str, label_map: dict[str, str]) -> str | None:
    if not values:
        return None
    prefix = ENUM_PREFIXES[kind]
    return ", ".join(label_map.get(v) or enum_to_label(v, prefix) for v in values)


CC_LICENSES = {
    # 보관함 라이선스 → (화면 표기 이름, 라이선스 주소)
    "CREATOR_MUSIC_LICENSE_TYPE_CCBY_4": (
        "Creative Commons Attribution 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
    ),
}


def cc_attribution(track: dict, license_name: str, license_url: str) -> str:
    """보관함 "View details" 창이 보여주는 설명란 문구와 같은 형식으로 만든다.

    Source / Artist 줄은 해당 링크가 있을 때만 붙는다 (보관함 화면과 동일, 2026-11-15 확인).
    """
    artist = (track.get("artist") or {}).get("name") or ""
    parts = [f"{track.get('title')} by {artist} is licensed under a {license_name} license. {license_url}"]
    if track.get("externalTrackUrl"):
        parts.append(f"Source: {track['externalTrackUrl']}")
    if track.get("externalArtistUrl"):
        parts.append(f"Artist: {track['externalArtistUrl']}")
    return "\n\n".join(parts)


def license_fields(track: dict, license_text: list[str]) -> tuple[str, str | None]:
    """(license_type, attribution_text)

    보관함 안내 문구에 저작자 표시가 필요 없다고 명시된 경우만 표기 불필요로 본다.
    처음 보는 라이선스는 표기 필요로 두고 문구는 비운다 (빠뜨리는 것보다 안전, 화면 확인 필요).
    """
    joined = "\n".join(license_text).lower()
    license_type = track.get("licenseType")
    if license_type == FREE_LICENSE and "no attribution is required" in joined:
        return "attribution_not_required", None
    if license_type in CC_LICENSES:
        name, url = CC_LICENSES[license_type]
        return "attribution_required", cc_attribution(track, name, url)
    return "attribution_required", None


def to_record(row: dict, maps: dict[str, dict[str, str]], collected_at: str) -> dict:
    track = row["track"]
    attrs = track.get("attributes") or {}
    license_text = [t for t in (row.get("licenseText") or []) if t]
    license_type, attribution = license_fields(track, license_text)
    publish = (track.get("publishTime") or {}).get("seconds")
    dur = track.get("duration") or {}
    duration = (float(dur.get("seconds") or 0) + float(dur.get("nanos") or 0) / 1e9) if dur else None
    return {
        "id": track["trackId"],
        "title": track.get("title") or track["trackId"],
        "artist": (track.get("artist") or {}).get("name"),
        "genre": labels(attrs.get("genres") or [], "genre", maps["genre"]),
        "mood": labels(attrs.get("moods") or [], "mood", maps["mood"]),
        "instruments": labels(attrs.get("instruments") or [], "instrument", {}),
        "duration_sec": duration,
        "license_type": license_type,
        "license_text": "\n".join(license_text) or None,
        "attribution_text": attribution,
        "artist_url": track.get("externalArtistUrl"),
        "published_at": datetime.fromtimestamp(int(publish), timezone.utc).date().isoformat() if publish else None,
        "source_json": json.dumps(row, ensure_ascii=False),
        "collected_at": collected_at,
    }


UPSERT = """
INSERT INTO tracks (id, title, artist, genre, mood, instruments, duration_sec, license_type,
                    license_text, attribution_text, artist_url, published_at, source_json, collected_at)
VALUES (:id, :title, :artist, :genre, :mood, :instruments, :duration_sec, :license_type,
        :license_text, :attribution_text, :artist_url, :published_at, :source_json, :collected_at)
ON CONFLICT(id) DO UPDATE SET
    title = excluded.title, artist = excluded.artist, genre = excluded.genre, mood = excluded.mood,
    instruments = excluded.instruments, duration_sec = excluded.duration_sec,
    license_type = excluded.license_type, license_text = excluded.license_text,
    attribution_text = excluded.attribution_text, artist_url = excluded.artist_url,
    published_at = excluded.published_at, source_json = excluded.source_json,
    collected_at = excluded.collected_at
"""


def main() -> None:
    if len(sys.argv) < 3:
        emit({"type": "error", "message": "사용법: library_import.py <root> <tracks.jsonl>"})
        sys.exit(1)
    root, src = sys.argv[1], sys.argv[2]
    if read_manifest(root) is None:
        emit({"type": "error", "message": f"음악 라이브러리 폴더가 아닙니다: {root}"})
        sys.exit(1)

    with open(src, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = [r for r in rows if (r.get("track") or {}).get("trackId")]

    maps = build_label_maps(rows)
    collected_at = now_iso()
    conn = connect(root)
    try:
        existing = {r[0] for r in conn.execute("SELECT id FROM tracks")}
        records = [to_record(r, maps, collected_at) for r in rows]
        with conn:
            conn.executemany(UPSERT, records)
        total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    finally:
        conn.close()

    inserted = sum(1 for r in records if r["id"] not in existing)
    emit({"type": "imported", "inserted": inserted, "updated": len(records) - inserted, "total": total})


if __name__ == "__main__":
    main()
