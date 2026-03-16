#!/usr/bin/env python3
"""AI Movie Cut — 메인 분석 스크립트 (v2: 스토리보드 파이프라인)

사용법:
    python analyze.py <folder_path> [options_json]

options_json 예시:
    {"window_duration": 10, "skip_vision": false, "resume": true}
"""

import sys
import os
import json
import subprocess
import tempfile
import time

from stage1 import analyze_file_stage1
from vad_detector import detect_speech_regions
from stt import transcribe_speech_regions, map_transcripts_to_windows
from stage2 import tag_window_context, tag_windows_batch_claude
from scene_detector import cross_validate_all, group_windows_to_scenes, filter_ng_scenes, log_quality_summary
from storyboard import run_narrative_editing, run_narrative_editing_claude
from merger import merge_adjacent_segments, validate_segments, format_srt_label
from edl_export import generate_edl


def log(msg: str):
    """stderr 디버그 로그 (JSON Lines와 분리)"""
    print(f"[analyze] {msg}", file=sys.stderr, flush=True)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".ts"}

AUTOCUT_DIR = "autocut"
PROGRESS_FILENAME = "progress.jsonl"
ANALYSIS_CACHE_FILE = "analysis.json"


def get_autocut_dir(folder_path: str) -> str:
    """autocut 출력 디렉토리 반환 (없으면 생성)"""
    d = os.path.join(folder_path, AUTOCUT_DIR)
    os.makedirs(d, exist_ok=True)
    return d
PIPELINE_VERSION = "storyboard_v3"

_progress_file = None


def save_analysis_cache_with_scenes(folder_path: str, files: list[dict], options: dict,
                                    windows: list[dict], total_duration: float,
                                    scenes: list[dict]):
    """Stage 1+VAD+Stage 2 결과와 scenes 데이터를 캐시 파일에 저장"""
    cache = {
        "pipeline": PIPELINE_VERSION,
        "window_duration": int(options.get("window_duration", 10)),
        "files": [{"name": f["name"], "duration": f["duration"]} for f in files],
        "total_duration": total_duration,
        "windows": windows,
        "scenes": scenes,
    }
    path = os.path.join(get_autocut_dir(folder_path), ANALYSIS_CACHE_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    log(f"분석 캐시 저장 (scenes 포함): {len(windows)}개 윈도우, {len(scenes)}개 장면 → {path}")


def load_analysis_cache(folder_path: str, files: list[dict], options: dict) -> tuple[list[dict], float] | None:
    """분석 캐시 로드. 유효하면 (windows, total_duration) 반환, 아니면 None"""
    path = os.path.join(get_autocut_dir(folder_path), ANALYSIS_CACHE_FILE)
    if not os.path.exists(path):
        return None

    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log(f"분석 캐시 파싱 실패: {e}")
        return None

    if cache.get("pipeline") != PIPELINE_VERSION:
        log(f"분석 캐시 버전 불일치: {cache.get('pipeline')} != {PIPELINE_VERSION}")
        return None

    if cache.get("window_duration") != int(options.get("window_duration", 10)):
        log("분석 캐시 window_duration 불일치")
        return None

    saved_files = cache.get("files", [])
    if len(saved_files) != len(files):
        log(f"분석 캐시 파일 수 불일치: {len(saved_files)} != {len(files)}")
        return None
    for sf, cf in zip(saved_files, files):
        if sf["name"] != cf["name"]:
            log(f"분석 캐시 파일명 불일치: {sf['name']} != {cf['name']}")
            return None
        if abs(sf["duration"] - cf["duration"]) > 0.5:
            log(f"분석 캐시 파일 길이 불일치: {sf['name']}")
            return None

    windows = cache.get("windows", [])
    total_duration = cache.get("total_duration", 0)
    if not windows:
        log("분석 캐시 윈도우 데이터 없음")
        return None

    log(f"분석 캐시 유효: {len(windows)}개 윈도우, {total_duration/60:.1f}분")
    return windows, total_duration


def _run_phase_b(all_window_data: list[dict]) -> tuple[list[dict], list[dict]]:
    """Phase B: 교차검증 + 장면 그룹핑 + NG 필터

    Returns (scenes, usable_scenes)
    """
    progress("scene_grouping", 81, "교차검증 중...")
    cv_stats = cross_validate_all(all_window_data)

    progress("scene_grouping", 82, "장면 그룹핑 중...")
    scenes = group_windows_to_scenes(all_window_data)
    usable_scenes = filter_ng_scenes(scenes, all_window_data)
    progress("scene_grouping", 83, f"{len(usable_scenes)}개 장면 ({len(scenes)-len(usable_scenes)}개 NG 제거)")

    log_quality_summary(all_window_data, scenes, cv_stats)
    return scenes, usable_scenes


def emit(data: dict):
    """JSON Lines stdout + 진행 파일 동시 출력"""
    line = json.dumps(data, ensure_ascii=False)
    print(line, flush=True)
    if _progress_file:
        _progress_file.write(line + "\n")
        _progress_file.flush()


def progress(stage: str, percent: int, message: str):
    emit({"type": "progress", "stage": stage, "percent": percent, "message": message})


def parse_progress_file(path: str) -> dict:
    """진행 파일(JSONL) 파싱하여 재개 상태 복원"""
    meta = None
    completed_files: set[int] = set()
    cached_results: dict[tuple[int, float], dict] = {}
    file_complete_events: dict[int, dict] = {}
    max_window_id = -1

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            t = data.get("type")
            if t == "meta":
                meta = data
            elif t == "window_result":
                wid = data.get("windowId", 0)
                if wid > max_window_id:
                    max_window_id = wid
                key = (data["fileIndex"], data["start"])
                cached_results[key] = data
            elif t == "file_complete":
                completed_files.add(data["fileIndex"])
                file_complete_events[data["fileIndex"]] = data

    return {
        "meta": meta,
        "completed_files": completed_files,
        "cached_results": cached_results,
        "file_complete_events": file_complete_events,
        "global_window_id": max_window_id + 1 if max_window_id >= 0 else 0,
    }


def validate_resume(meta: dict | None, files: list[dict], options: dict) -> str | None:
    """재개 가능 여부 검증. 불가 시 사유 문자열 반환, 가능하면 None"""
    if meta is None:
        return "meta 정보 없음"

    if meta.get("pipeline") != PIPELINE_VERSION:
        return f"파이프라인 버전 불일치 (저장={meta.get('pipeline')}, 현재={PIPELINE_VERSION})"

    saved_files = meta.get("files", [])
    saved_options = meta.get("options", {})

    if len(saved_files) != len(files):
        return f"파일 수 불일치 (저장={len(saved_files)}, 현재={len(files)})"

    for sf, cf in zip(saved_files, files):
        if sf["name"] != cf["name"]:
            return f"파일 이름 불일치: {sf['name']} != {cf['name']}"
        if abs(sf["duration"] - cf["duration"]) > 0.5:
            return f"파일 길이 불일치: {sf['name']} ({sf['duration']:.1f}s != {cf['duration']:.1f}s)"

    if saved_options.get("window_duration") != options.get("window_duration"):
        return "window_duration 변경됨"

    return None


def get_video_duration(path: str) -> float:
    """ffprobe로 영상 길이 조회"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def extract_audio_wav(video_path: str, output_path: str):
    """FFmpeg로 16kHz WAV 추출"""
    subprocess.run(
        [
            "ffmpeg", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            "-y", "-v", "quiet", output_path,
        ],
        check=True,
    )


def scan_video_files(folder_path: str) -> list[dict]:
    """폴더 내 영상 파일 검색 및 시간순 정렬"""
    files = []
    for name in sorted(os.listdir(folder_path)):
        ext = os.path.splitext(name)[1].lower()
        if ext in VIDEO_EXTS and not name.startswith("."):
            full_path = os.path.join(folder_path, name)
            duration = get_video_duration(full_path)
            if duration > 0:
                files.append({"path": full_path, "name": name, "duration": duration})
    return files


def _classify_low_quality(w: dict) -> tuple[str, str]:
    """Stage 1 필터링된 윈도우에 기본 라벨 부여

    호출 조건: brightness < 0.02 이므로 항상 완전 암전.
    """
    return "dark", "화면이 매우 어둡다 (완전 암전)"


def main():
    global _progress_file

    if len(sys.argv) < 2:
        emit({"type": "error", "message": "사용법: python analyze.py <folder_path> [options_json]"})
        sys.exit(1)

    folder_path = sys.argv[1]
    options = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    window_duration = int(options.get("window_duration", 10))
    skip_vision = bool(options.get("skip_vision", False))
    resume = bool(options.get("resume", False))
    force_reanalyze = bool(options.get("force_reanalyze", False))
    editing_comment = str(options.get("editing_comment", "")).strip()
    ai_engine = str(options.get("ai_engine", "ollama"))  # "ollama" | "claude"

    log(f"시작: folder={folder_path}")
    log(f"옵션: window={window_duration}s, skip_vision={skip_vision}, resume={resume}, force_reanalyze={force_reanalyze}, ai_engine={ai_engine}")
    if editing_comment:
        log(f"편집 코멘트: {editing_comment}")

    # ai_engine에 따라 캐시 버전 분리
    global PIPELINE_VERSION
    if ai_engine == "claude":
        PIPELINE_VERSION = "storyboard_v3_claude"
        log("AI 엔진: Claude Code")
    else:
        log("AI 엔진: Ollama (로컬)")

    t_start = time.time()

    # 1. 초기화
    progress("initializing", 0, "영상 파일 검색 중...")
    files = scan_video_files(folder_path)
    if not files:
        emit({"type": "error", "message": "영상 파일을 찾을 수 없습니다."})
        sys.exit(1)

    total_files = len(files)
    log(f"{total_files}개 파일 발견, 총 {sum(f['duration'] for f in files):.1f}초")
    progress("initializing", 5, f"{total_files}개 영상 파일 발견")

    # 누적 오프셋 계산
    cumulative = 0.0
    for f in files:
        f["offset"] = cumulative
        cumulative += f["duration"]
    total_duration = cumulative

    # 분석 캐시 확인 (Stage 1+VAD+Stage 2 결과 재사용)
    if not force_reanalyze and not resume:
        cached = load_analysis_cache(folder_path, files, options)
        if cached is not None:
            all_window_data, cached_duration = cached
            log(f"분석 캐시 사용: {len(all_window_data)}개 윈도우 → LLM 편집만 실행")
            progress("initializing", 10, f"분석 캐시 로드: {len(all_window_data)}개 윈도우")

            # 파일별 누적 시간 테이블 (fileIndex 역산용)
            import bisect
            file_cumul = []
            t_acc = 0.0
            for fi in files:
                file_cumul.append(t_acc)
                t_acc += fi["duration"]

            # UI 복원: window_result 이벤트 재출력
            for i, wd in enumerate(all_window_data):
                fi = max(0, bisect.bisect_right(file_cumul, wd["globalStart"]) - 1)
                local_start = wd["globalStart"] - file_cumul[fi]
                local_end = wd["globalEnd"] - file_cumul[fi]
                result = {
                    "type": "window_result",
                    "windowId": i,
                    "fileIndex": fi,
                    "start": local_start,
                    "end": local_end,
                    "globalStart": wd["globalStart"],
                    "globalEnd": wd["globalEnd"],
                    "decision": "pending",
                    "label": wd.get("label", ""),
                    "shot": wd.get("shot", ""),
                    "desc": wd.get("desc", ""),
                    "source": wd.get("source", ""),
                    "s1_score": wd.get("s1_score", 0),
                    "has_speech": wd.get("has_speech", False),
                    "score": wd.get("s1_score", 0),
                    "usable": wd.get("usable", "unknown"),
                    "usable_reason": wd.get("usable_reason", ""),
                    "motion": wd.get("motion", 0),
                    "brightness": wd.get("brightness", 0.5),
                }
                print(json.dumps(result, ensure_ascii=False), flush=True)

            progress("stage2_vision", 80, "캐시에서 윈도우 데이터 로드 완료")

            # Phase B: 교차검증 + 장면 그룹핑 + NG 필터
            scenes, usable_scenes = _run_phase_b(all_window_data)

            # Phase C: 내러티브 편집
            log(f"내러티브 편집 시작: {len(usable_scenes)}개 장면 (캐시, engine={ai_engine})")
            if ai_engine == "claude":
                keep_segments = run_narrative_editing_claude(
                    usable_scenes, all_window_data, cached_duration,
                    progress_callback=progress,
                    editing_comment=editing_comment,
                )
            else:
                keep_segments = run_narrative_editing(
                    usable_scenes, all_window_data, cached_duration,
                    progress_callback=progress,
                    editing_comment=editing_comment,
                )
            log(f"내러티브 편집 완료: {len(keep_segments)}개 KEEP")

            # 후처리
            progress("merging", 95, "KEEP 구간 병합 중...")
            merged = merge_adjacent_segments(keep_segments)
            validated = validate_segments(merged)

            anchor = {
                "id": 0, "globalStart": 0.0, "globalEnd": 1.0,
                "label": "start", "score": 0, "reason": "편집 시작점",
            }
            validated = [anchor] + validated
            for i, seg in enumerate(validated):
                seg["id"] = i

            # SRT 생성
            progress("generating_srt", 97, "SRT 파일 생성 중...")
            autocut_dir = get_autocut_dir(folder_path)
            folder_name = os.path.basename(os.path.normpath(folder_path))
            srt_path = os.path.join(autocut_dir, f"{folder_name}.srt")
            if os.path.exists(srt_path):
                n = 2
                while os.path.exists(os.path.join(autocut_dir, f"{folder_name}_{n}.srt")):
                    n += 1
                srt_path = os.path.join(autocut_dir, f"{folder_name}_{n}.srt")
            write_srt(validated, srt_path)

            # EDL 생성
            edl_path = srt_path.replace(".srt", ".edl")
            edl_content = generate_edl(validated, files)
            if edl_content:
                with open(edl_path, "w", encoding="utf-8") as ef:
                    ef.write(edl_content)
                log(f"EDL 생성: {edl_path}")

            elapsed = time.time() - t_start
            log(f"재편집 완료: {len(validated)}개 KEEP, SRT={srt_path}, 총 {elapsed:.1f}s 소요")
            emit({
                "type": "complete",
                "keepSegments": validated,
                "srtPath": srt_path,
                "edlPath": edl_path,
                "totalKeep": len(validated),
                "totalDuration": cached_duration,
            })
            return

    # 재개 상태 로드
    progress_path = os.path.join(get_autocut_dir(folder_path), PROGRESS_FILENAME)
    resume_state = None

    if resume and os.path.exists(progress_path):
        log("재개 모드: 진행 파일 파싱 중...")
        resume_state = parse_progress_file(progress_path)
        reason = validate_resume(resume_state["meta"], files, {
            "window_duration": window_duration,
        })
        if reason:
            log(f"재개 불가: {reason} → fresh start")
            resume_state = None

    if resume_state:
        completed_files = resume_state["completed_files"]
        cached_results = resume_state["cached_results"]
        global_window_id = resume_state["global_window_id"]
        log(f"재개: {len(completed_files)}개 파일 완료, {len(cached_results)}개 윈도우 캐시")

        _progress_file = open(progress_path, "a", encoding="utf-8")

        # 파일별 캐시 인덱스 사전 구축 (O(n) 조회용)
        cache_by_file: dict[int, list[dict]] = {}
        for (fk, _), v in cached_results.items():
            cache_by_file.setdefault(fk, []).append(v)

        # 완료된 파일의 캐시 결과를 stdout으로 재출력 (UI 복원용)
        progress("initializing", 3, "이전 분석 결과 복원 중...")
        for fi_done in sorted(completed_files):
            cached_for_file = cache_by_file.get(fi_done, [])
            cached_for_file.sort(key=lambda x: x.get("windowId", 0))
            for wr in cached_for_file:
                print(json.dumps(wr, ensure_ascii=False), flush=True)
            fc = resume_state["file_complete_events"].get(fi_done)
            if fc:
                print(json.dumps(fc, ensure_ascii=False), flush=True)
    else:
        global_window_id = 0
        completed_files = set()
        cached_results = {}

        if os.path.exists(progress_path):
            os.remove(progress_path)
        _progress_file = open(progress_path, "w", encoding="utf-8")

        emit({
            "type": "meta",
            "pipeline": PIPELINE_VERSION,
            "files": [{"name": f["name"], "duration": f["duration"]} for f in files],
            "options": {
                "window_duration": window_duration,
                "skip_vision": skip_vision,
            },
        })

    try:
        # 모든 윈도우 데이터를 수집 (스토리보드 입력)
        all_window_data: list[dict] = []

        # 재개 시 완료된 파일의 캐시에서 윈도우 데이터 복원
        if resume_state:
            for fi_done in sorted(completed_files):
                cached_for_file = sorted(
                    cache_by_file.get(fi_done, []),
                    key=lambda x: x.get("globalStart", 0),
                )
                for wr in cached_for_file:
                    all_window_data.append({
                        "globalStart": wr["globalStart"],
                        "globalEnd": wr["globalEnd"],
                        "label": wr.get("label", ""),
                        "shot": wr.get("shot", ""),
                        "desc": wr.get("desc", ""),
                        "source": wr.get("source", ""),
                        "s1_score": wr.get("s1_score", 0),
                        "has_speech": wr.get("has_speech", False),
                        "usable": wr.get("usable", "unknown"),
                        "usable_reason": wr.get("usable_reason", ""),
                        "motion": wr.get("motion", 0),
                        "brightness": wr.get("brightness", 0.5),
                        "transcript": wr.get("transcript", ""),
                    })

        for fi, file_info in enumerate(files):
            video_path = file_info["path"]
            file_offset = file_info["offset"]
            file_name = file_info["name"]

            file_range = 80.0 / max(total_files, 1)
            file_percent_base = fi * file_range

            if fi in completed_files:
                log(f"--- [{fi+1}/{total_files}] {file_name} (캐시에서 복원됨) ---")
                continue

            t_file = time.time()
            log(f"--- [{fi+1}/{total_files}] {file_name} (offset={file_offset:.1f}s, dur={file_info['duration']:.1f}s) ---")

            # 2. 오디오 추출
            progress("extracting", int(file_percent_base + file_range * 0.05), f"[{fi+1}/{total_files}] {file_name} 오디오 추출...")
            audio_path = os.path.join(tempfile.gettempdir(), f"video-studio-audio-{os.getpid()}-{fi}.wav")
            audio_ok = False
            try:
                extract_audio_wav(video_path, audio_path)
                audio_ok = True
                log(f"오디오 추출 완료 ({time.time()-t_file:.1f}s)")
            except Exception as e:
                log(f"오디오 추출 실패: {e}")

            # 3. Stage 1 분석
            t_s1 = time.time()
            progress("stage1_scan", int(file_percent_base + file_range * 0.10), f"[{fi+1}/{total_files}] {file_name} Stage 1 스캔...")
            windows = analyze_file_stage1(video_path, window_duration=window_duration)
            log(f"Stage 1 완료: {len(windows)}개 윈도우 ({time.time()-t_s1:.1f}s)")

            # 4. VAD 분석
            speech_regions = []
            if audio_ok and os.path.exists(audio_path):
                t_vad = time.time()
                progress("vad", int(file_percent_base + file_range * 0.15), f"[{fi+1}/{total_files}] {file_name} VAD 분석...")
                try:
                    speech_regions = detect_speech_regions(audio_path)
                    log(f"VAD 완료: {len(speech_regions)}개 음성 구간 ({time.time()-t_vad:.1f}s)")
                except Exception as e:
                    log(f"VAD 실패: {e}")
                    speech_regions = []

            # 4.5 STT 분석 (음성→텍스트)
            if speech_regions and audio_ok and os.path.exists(audio_path):
                t_stt = time.time()
                progress("stt", int(file_percent_base + file_range * 0.18), f"[{fi+1}/{total_files}] {file_name} STT 분석...")
                try:
                    transcripts = transcribe_speech_regions(audio_path, speech_regions)
                    if transcripts:
                        map_transcripts_to_windows(transcripts, windows, window_duration)
                    log(f"STT 완료: {len(transcripts)}개 세그먼트 ({time.time()-t_stt:.1f}s)")
                except Exception as e:
                    log(f"STT 실패 (계속 진행): {e}")

            # 오디오 파일 삭제 (VAD+STT 완료 후)
            if os.path.exists(audio_path):
                os.unlink(audio_path)

            # 5. 모든 윈도우 처리 (태깅 + 스토리보드 데이터 수집)
            # Claude 배치 비전 태깅 사전 처리
            claude_tag_results = {}
            if ai_engine == "claude" and not skip_vision:
                vision_batch = []
                vision_wi_map = {}
                for _wi, _w in enumerate(windows):
                    if (fi, _w["start"]) in cached_results:
                        continue
                    _w_start, _w_end = _w["start"], _w["end"]
                    _w_dur = _w_end - _w_start
                    _s1 = _w["score"]
                    _has_sp = any(
                        max(r["start"], _w_start) < min(r["end"], _w_end)
                        for r in speech_regions
                    )
                    _vad_tol = _w_dur * 0.2
                    _fully_vad = any(
                        r["start"] <= _w_start + _vad_tol and r["end"] >= _w_end - _vad_tol
                        for r in speech_regions
                    )
                    _has_speech_w = _fully_vad or _w.get("has_speech", _has_sp)
                    if not _has_speech_w and _s1 < 1 and _w.get("brightness", 0) < 0.02:
                        continue
                    idx = len(vision_batch)
                    vision_batch.append({"start": _w_start, "end": _w_end})
                    vision_wi_map[idx] = _wi

                if vision_batch:
                    _s2_base = int(file_percent_base + file_range * 0.25)
                    _s2_end = int(file_percent_base + file_range)

                    def _claude_progress(msg, pct):
                        scaled = _s2_base + int((_s2_end - _s2_base) * pct / 100)
                        progress("stage2_vision", scaled, msg)

                    batch_results = tag_windows_batch_claude(
                        video_path, vision_batch, batch_size=10,
                        progress_callback=_claude_progress,
                    )
                    for bidx, tag in batch_results.items():
                        claude_tag_results[vision_wi_map[bidx]] = tag
                    log(f"Claude 배치 태깅 완료: {len(claude_tag_results)}/{len(vision_batch)}개")

            stage2_count = 0
            for wi, w in enumerate(windows):
                w_start = w["start"]
                w_end = w["end"]
                s1_score = w["score"]

                # VAD 겹침 확인
                has_speech = any(
                    max(region["start"], w_start) < min(region["end"], w_end)
                    for region in speech_regions
                )

                # 완전 VAD 커버 확인 (Stage 2 스킵 조건)
                # 단일 음성 구간이 윈도우의 양 끝 20% 이내에서 시작·끝나면 완전 VAD로 판정
                w_dur = w_end - w_start
                vad_tolerance = w_dur * 0.2
                is_fully_vad = any(
                    r["start"] <= w_start + vad_tolerance and r["end"] >= w_end - vad_tolerance
                    for r in speech_regions
                )

                # 캐시 확인
                cache_key = (fi, w_start)
                if cache_key in cached_results:
                    cached = cached_results[cache_key]
                    print(json.dumps(cached, ensure_ascii=False), flush=True)
                    all_window_data.append({
                        "globalStart": cached["globalStart"],
                        "globalEnd": cached["globalEnd"],
                        "label": cached.get("label", ""),
                        "shot": cached.get("shot", ""),
                        "desc": cached.get("desc", ""),
                        "source": cached.get("source", ""),
                        "s1_score": cached.get("s1_score", 0),
                        "has_speech": cached.get("has_speech", False),
                        "usable": cached.get("usable", "unknown"),
                        "usable_reason": cached.get("usable_reason", ""),
                        "motion": cached.get("motion", 0),
                        "brightness": cached.get("brightness", 0.5),
                        "transcript": cached.get("transcript", ""),
                    })
                    wid = cached.get("windowId", -1)
                    if wid >= 0:
                        global_window_id = max(global_window_id, wid + 1)
                    continue

                # 윈도우 분류
                label = ""
                shot = ""
                desc = ""
                source = ""

                usable = "unknown"
                usable_reason = ""

                has_speech_w = is_fully_vad or w.get("has_speech", has_speech)
                if not has_speech_w and s1_score < 1 and w.get("brightness", 0) < 0.02:
                    label, desc = _classify_low_quality(w)
                    source = "s1_filtered"
                    usable = "no"
                    usable_reason = desc
                elif not skip_vision:
                    # Stage 2 비전 태깅
                    stage2_count += 1
                    if ai_engine == "claude":
                        # Claude 배치 결과 조회
                        if wi in claude_tag_results:
                            label, shot, desc, usable, usable_reason = claude_tag_results[wi]
                        else:
                            label, shot, desc, usable, usable_reason = "unknown", "", "", "yes", "Claude 태깅 실패"
                        log(f"  Stage 2 [{w_start:.0f}~{w_end:.0f}s]: {label} usable={usable} (claude)")
                    else:
                        # Ollama 개별 태깅
                        stage2_pct = int(file_percent_base + file_range * (0.25 + 0.75 * wi / max(len(windows), 1)))
                        progress("stage2_vision", stage2_pct,
                                 f"[{fi+1}/{total_files}] {file_name} 비전 태깅 ({stage2_count})...")
                        t_s2 = time.time()
                        prev_win = (windows[wi-1]["start"], windows[wi-1]["end"]) if wi > 0 else None
                        next_win = (windows[wi+1]["start"], windows[wi+1]["end"]) if wi < len(windows) - 1 else None
                        label, shot, desc, usable, usable_reason = tag_window_context(
                            video_path, prev_win, (w_start, w_end), next_win
                        )
                        log(f"  Stage 2 [{w_start:.0f}~{w_end:.0f}s]: {label} usable={usable} ({time.time()-t_s2:.1f}s)")
                    source = "vad+vision" if is_fully_vad else "vision"
                else:
                    label = "unknown"
                    source = "vad" if is_fully_vad else "skip_vision"
                    if is_fully_vad:
                        usable = "yes"

                wd = {
                    "globalStart": file_offset + w_start,
                    "globalEnd": file_offset + w_end,
                    "label": label,
                    "shot": shot,
                    "desc": desc,
                    "source": source,
                    "s1_score": s1_score,
                    "has_speech": has_speech,
                    "usable": usable,
                    "usable_reason": usable_reason,
                    "motion": w.get("motion_score", 0),
                    "brightness": w.get("brightness", 0.5),
                    "transcript": w.get("transcript", ""),
                }
                all_window_data.append(wd)

                result = {
                    "type": "window_result",
                    "windowId": global_window_id,
                    "fileIndex": fi,
                    "start": w_start,
                    "end": w_end,
                    "globalStart": wd["globalStart"],
                    "globalEnd": wd["globalEnd"],
                    "decision": "pending",
                    "label": label,
                    "shot": shot,
                    "desc": desc,
                    "source": source,
                    "s1_score": s1_score,
                    "has_speech": has_speech,
                    "score": s1_score,
                    "usable": usable,
                    "usable_reason": usable_reason,
                    "motion": wd["motion"],
                    "brightness": wd["brightness"],
                    "transcript": wd.get("transcript", ""),
                }
                emit(result)
                global_window_id += 1

            file_s1_filtered = len(windows) - stage2_count
            file_vad = sum(1 for w in windows if any(
                r["start"] <= w["start"] + (w["end"] - w["start"]) * 0.2
                and r["end"] >= w["end"] - (w["end"] - w["start"]) * 0.2
                for r in speech_regions
            ))
            log(f"파일 완료: vision={stage2_count}, vad={file_vad}, filtered={file_s1_filtered}, total={len(windows)} ({time.time()-t_file:.1f}s)")
            emit({
                "type": "file_complete",
                "fileIndex": fi,
                "filePath": video_path,
                "vadCount": file_vad,
                "keepCount": stage2_count,
                "dropCount": file_s1_filtered,
            })
            if _progress_file:
                os.fsync(_progress_file.fileno())

        # 6. Phase B: 교차검증 + 장면 그룹핑 + NG 필터
        scenes, usable_scenes = _run_phase_b(all_window_data)

        # 분석 캐시 저장 (윈도우 + scenes 포함)
        save_analysis_cache_with_scenes(folder_path, files, options, all_window_data, total_duration, scenes)

        # 7. Phase C: 내러티브 편집
        log(f"내러티브 편집 시작: {len(usable_scenes)}개 장면, {len(all_window_data)}개 윈도우 (engine={ai_engine})")
        if ai_engine == "claude":
            keep_segments = run_narrative_editing_claude(
                usable_scenes, all_window_data, total_duration,
                progress_callback=progress,
                editing_comment=editing_comment,
            )
        else:
            keep_segments = run_narrative_editing(
                usable_scenes, all_window_data, total_duration,
                progress_callback=progress,
                editing_comment=editing_comment,
            )
        log(f"내러티브 편집 완료: {len(keep_segments)}개 KEEP")

        # 7. 후처리: 병합 + 검증
        progress("merging", 95, "KEEP 구간 병합 중...")
        merged = merge_adjacent_segments(keep_segments)
        log(f"병합 후: {len(merged)}개")
        validated = validate_segments(merged)
        log(f"검증 후: {len(validated)}개")

        # 편집기 시작 위치 기준용 빈 자막 삽입
        anchor = {
            "id": 0,
            "globalStart": 0.0,
            "globalEnd": 1.0,
            "label": "start",
            "score": 0,
            "reason": "편집 시작점",
        }
        validated = [anchor] + validated

        for i, seg in enumerate(validated):
            seg["id"] = i

        # 8. SRT 생성
        progress("generating_srt", 97, "SRT 파일 생성 중...")
        autocut_dir = get_autocut_dir(folder_path)
        folder_name = os.path.basename(os.path.normpath(folder_path))
        srt_path = os.path.join(autocut_dir, f"{folder_name}.srt")
        if os.path.exists(srt_path):
            n = 2
            while os.path.exists(os.path.join(autocut_dir, f"{folder_name}_{n}.srt")):
                n += 1
            srt_path = os.path.join(autocut_dir, f"{folder_name}_{n}.srt")
        write_srt(validated, srt_path)

        # EDL 생성
        edl_path = srt_path.replace(".srt", ".edl")
        edl_content = generate_edl(validated, files)
        if edl_content:
            with open(edl_path, "w", encoding="utf-8") as ef:
                ef.write(edl_content)
            log(f"EDL 생성: {edl_path}")

        # 완료
        elapsed = time.time() - t_start
        log(f"전체 완료: {len(validated)}개 KEEP, SRT={srt_path}, 총 {elapsed:.1f}s 소요")
        emit({
            "type": "complete",
            "keepSegments": validated,
            "srtPath": srt_path,
            "edlPath": edl_path,
            "totalKeep": len(validated),
            "totalDuration": total_duration,
        })

        if os.path.exists(progress_path):
            os.remove(progress_path)
            log("진행 파일 삭제 완료")
    finally:
        if _progress_file:
            try:
                _progress_file.flush()
            finally:
                _progress_file.close()
                _progress_file = None


def format_srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[dict], output_path: str):
    """KEEP 세그먼트를 SRT 파일로 저장 (힌트 태그 포함)"""
    lines = []
    for i, seg in enumerate(segments):
        start = format_srt_time(seg["globalStart"])
        end = format_srt_time(seg["globalEnd"])
        text = format_srt_label(seg)
        lines.append(f"{i + 1}")
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
