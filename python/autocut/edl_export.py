"""EDL (Edit Decision List) 생성 모듈 — CMX 3600 포맷

DaVinci Resolve에서 File > Import > Timeline > Pre-Conformed EDL로 불러와
타임라인에 KEEP 구간만 자동 배치.

다빈치의 EDL conform은 **reel name + source timecode** 두 가지로만
미디어 풀 클립을 찾는다(파일 경로 매칭은 XML/AAF 전용). 따라서 이 모듈의
출력은 미디어에 기록된 실제 타임코드와 프레임 단위로 일치해야 한다.
"""

import bisect
import os
from collections import Counter


# 타임라인에서 첫 클립이 시작할 record 타임코드(HH:MM:SS). 다빈치 프로젝트의
# 타임라인 시작 TC와 맞아야 앞쪽에 빈 구간이 생기지 않는다.
RECORD_START_TC = "00:00:00"


def resolve_tc_base(fps: float) -> tuple[int, float]:
    """실수 fps → (타임코드 base 정수 fps, 실제 fps)

    NTSC 계열(23.976/29.97/59.94)은 타임코드 base가 정수(24/30/60)이고
    실제 프레임 레이트는 1000/1001 배다. 이 둘을 섞으면 1시간당 3.6초가
    어긋나므로 항상 구분해서 다룬다.

    tolerance는 0.01로 좁게 잡는다. 0.05로 넓으면 정확히 30.000fps 영상
    (일부 폰/화면녹화)이 29.97(|30-29.97|=0.03)로 오분류된다.

    한계: 프레임 필드가 2자리인 CMX 3600 특성상 100fps 이상은 표현할 수
    없다(`warn_edl_risks`가 경고한다).
    """
    # NTSC 계열은 예외 없이 (정수 base) × 1000/1001 이다. 24/30/60만 테이블로
    # 두면 47.952(48000/1001)·119.88(120000/1001) 같은 레이트가 정수로 떨어져
    # 0.1%씩 어긋난다 — time-of-day TC에서는 이게 그대로 offline이 된다.
    # 허용오차가 절대값(0.01)이라 상·하한이 모두 필요하다. 상한이 없으면
    # 1000의 배수 부근이 NTSC로 잡히고(1000.0 → base 1001), 하한이 없으면
    # 저레이트 정수가 전부 NTSC가 된다(10fps → actual 9.99 로 0.1% 어긋나
    # 타임랩스 소스의 source TC가 시간당 3.6초씩 밀린다).
    # 실재하는 NTSC 계열의 base는 24/30/48/60/120뿐이다.
    ntsc_base = int(round(fps * 1001 / 1000))
    if 24 <= ntsc_base <= 120 and abs(fps - ntsc_base * 1000 / 1001) < 0.01:
        return ntsc_base, ntsc_base * 1000 / 1001
    base = int(round(fps))
    return base, float(base)


def resolve_edl_rate(files: list[dict], fallback: float = 24.0) -> float:
    """파일 목록에서 EDL 전체에 적용할 단일 레이트를 결정.

    CMX 3600 EDL은 FCM 헤더가 하나뿐이라 timeline 레이트가 단일해야 한다.
    파일들의 실제 fps 중 최빈값을 사용하고(첫 파일이 우연히 다른 레이트여도
    전체가 휘둘리지 않도록), fps를 못 읽은(0.0) 파일은 제외한다.
    유효 fps가 하나도 없으면 fallback을 반환한다(감지 실패).
    """
    rates = [round(f["fps"], 3) for f in files if f.get("fps")]
    if not rates:
        return fallback
    return Counter(rates).most_common(1)[0][0]


def resolve_edl_drop_frame(files: list[dict], fps_int: int, actual_fps: float) -> bool:
    """EDL 전체에 적용할 drop frame 여부를 결정.

    drop frame은 fps로 추정할 수 없다. 29.97fps 영상의 타임코드는 NDF인
    경우가 훨씬 흔하고(소니 XAVC, 대부분의 폰·액션캠), DF/NDF는 오직
    미디어에 기록된 타임코드 표기(';' = DF, ':' = NDF)로만 정해진다.
    fps만 보고 DF를 강제하면 13시간대 TC 기준 약 47초(1400여 프레임)가
    어긋나 클립 범위를 완전히 벗어나고, 다빈치에서 전부 offline이 된다.

    drop frame은 NTSC 30/60 base에만 존재한다. 23.976(24 base)과 정수
    레이트(25/30/50/60)에는 개념 자체가 없으므로 항상 False.
    """
    if fps_int not in (30, 60) or abs(actual_fps - fps_int) < 0.001:
        return False
    # 타임코드가 없는 파일(`tc_drop_frame is None`)은 투표에서 뺀다. 없는 TC를
    # "NDF 한 표"로 세면 TC 없는 파일이 다수인 폴더에서 진짜 DF 소스가 NDF로
    # 뒤집히고, 그 클립들의 source TC가 통째로 어긋난다.
    flags = [f["tc_drop_frame"] for f in files
             if f.get("tc_drop_frame") is not None]
    if not flags:
        return False
    return Counter(flags).most_common(1)[0][0]


def frames_to_timecode(frame_number: int, fps_int: int, drop_frame: bool = False) -> str:
    """프레임 번호 → HH:MM:SS:FF 타임코드

    drop_frame=True면 NTSC drop frame timecode(SMPTE):
      - 매 분의 처음 2프레임(60 base는 4프레임)을 건너뛴다. 단 10분 배수 제외
      - 표기 구분자는 ';' (NDF는 ':')
    """
    if frame_number < 0:
        frame_number = 0

    if drop_frame:
        drop = 2 if fps_int == 30 else 4
        frames_per_10min = fps_int * 600 - drop * 9
        frames_per_min = fps_int * 60 - drop

        d = frame_number // frames_per_10min
        m = frame_number % frames_per_10min
        if m > drop:
            frame_number += drop * 9 * d + drop * ((m - drop) // frames_per_min)
        else:
            frame_number += drop * 9 * d
        sep = ";"
    else:
        sep = ":"

    ff = frame_number % fps_int
    total_seconds = frame_number // fps_int
    ss = total_seconds % 60
    mm = (total_seconds // 60) % 60
    # 24시간에서 wrap하지 않는다. 임베디드 TC가 23:58인 야간 소스에서 자정을
    # 넘는 구간을 뽑으면 src_out(00:03)이 src_in(23:58)보다 작아져 이벤트가
    # 깨진다. 24:03 으로 넘겨두면 단조성이 유지되고, mod-24 로 해석하는
    # 파서에서는 미디어 TC와도 일치한다.
    # 대신 시간 필드는 2자리를 지켜야 컬럼이 밀리지 않으므로 100에서 wrap한다
    # (소스 TC가 100시간을 넘는 경우는 없고, 자정 넘김은 24~47시간이라 안전).
    hh = (total_seconds // 3600) % 100
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ff:02d}"


def timecode_to_frames(tc: str, fps_int: int) -> tuple[int, bool]:
    """타임코드 문자열 → (프레임 번호, drop_frame 여부)

    ';' 구분자가 있으면 drop frame으로 판정하고 drop된 프레임을 빼서
    실제 프레임 번호를 구한다. `frames_to_timecode`의 역함수.
    """
    # drop frame은 NTSC 30/60 base에만 존재한다. 24/25/50 base에 ';'가
    # 섞여 들어와도 drop 보정을 적용하면 안 된다(24 base에서 13시간대 TC
    # 기준 약 2900프레임이 어긋난다).
    drop = ";" in tc and fps_int in (30, 60)
    parts = tc.replace(";", ":").split(":")
    if len(parts) != 4:
        raise ValueError(f"타임코드 형식 오류: {tc!r}")
    h, m, s, f = (int(x) for x in parts)

    frames = (h * 3600 + m * 60 + s) * fps_int + f
    if drop:
        drop_count = 2 if fps_int == 30 else 4
        total_min = h * 60 + m
        frames -= drop_count * (total_min - total_min // 10)
    return frames, drop


def seconds_to_frames(seconds: float, actual_fps: float) -> int:
    """실시간 초 → 프레임 번호 (실제 레이트 기준)"""
    if seconds < 0:
        seconds = 0
    return round(seconds * actual_fps)


def _ascii_stem(name: str) -> str:
    """파일명 → reel 필드에 쓸 수 있는 ASCII 문자열(대문자)

    str.isalnum()은 유니코드 문자 전체에 True라 그대로 쓰면 reel 필드에
    한글이 들어가 고정폭 ASCII 필드가 깨진다. 반대로 영숫자만 남기면
    `IMG_1234` → `IMG1234` 가 되어, 다빈치가 파일명에서 뽑은 reel name
    (`IMG_1234`)과 어긋난다. `_` 와 `-` 는 보존한다.
    """
    stem = os.path.splitext(name)[0]
    return "".join(c for c in stem
                   if c.isascii() and (c.isalnum() or c in "_-")).upper()


def assign_reel_names(files: list[dict]) -> list[str]:
    """파일 목록 → 파일별 CMX 3600 reel name (ASCII 8자, 서로 유일)

    다빈치는 미디어 풀 클립의 reel name을 임베디드 메타데이터 또는 파일명에서
    가져온다(Project Settings > General Options > Conform Options >
    "Assist using reel names from"). 인덱스 기반 이름(R001 등)은 어떤 클립과도
    일치할 수 없으므로 실제 파일명에서 뽑는 것이 기본이다.

    다만 reel 필드는 8자뿐이라 `20260319_115649` / `20260319_124939` 처럼 앞
    8자가 같은 파일들은 구분되지 않는다. 이때 두 클립의 source TC 범위까지
    겹치면(임베디드 TC가 없어 둘 다 00:00:00:00부터 시작하는 경우가 대표적)
    다빈치가 둘을 구분할 방법이 없어 **조용히 잘못된 클립에 conform** 된다.
    그래서 앞 8자가 겹치는 파일들만 인덱스 접미사로 유일화한다.

    유일화된 reel은 실제 파일명과 다르므로, 매칭은 `* FROM CLIP NAME:` 의
    전체 파일명과 다빈치의 "Extract reel names from EDL comments" 옵션이
    담당한다(이 옵션이 켜지면 reel 필드 자체는 쓰이지 않는다).
    """
    bases = [_ascii_stem(f["name"]) for f in files]
    # 충돌 판정은 **8자로 자른 뒤** 기준이어야 한다. 전체 stem으로 세면
    # `20260319115649` / `20260319124939` 는 서로 다르다고 판단되지만 8자로
    # 자르면 둘 다 `20260319` 가 되어 충돌이 그대로 남는다.
    keys = [b[:8] for b in bases]
    counts = Counter(keys)
    width = max(3, len(str(len(files))))

    # 접미사를 붙인 reel이 다른 파일의 원래 reel과 또 겹칠 수 있으므로
    # (`20260319_1…`/`20260319_2…` 와 `20260002.mp4` → 모두 `20260002`),
    # 이미 쓴 이름을 추적하며 비어 있는 번호를 찾는다.
    # 합성 reel은 "이미 배정한 이름"만 피해서는 부족하다. 다빈치는 미디어
    # 파일명에서도 reel을 도출하므로(Assist using reel names from: Source clip
    # file name), 합성한 이름이 같은 폴더의 **다른 실제 파일** 이름과 겹치면
    # 그 클립에 컷이 붙는다. 예: `20260319_124939` 에 `20260002` 를 합성했는데
    # 폴더에 `20260002.MP4` 가 있는 경우. 파일명에서 나올 수 있는 값은 전부
    # 예약해 두고 피한다.
    reserved = {k for k in keys if k}

    used: set[str] = set()
    reels = []
    for i, base in enumerate(bases):
        reel = keys[i] if base and counts[keys[i]] == 1 else ""
        if not reel or reel in used:
            prefix = base[: max(0, 8 - width)]
            n = i + 1
            reel = f"{prefix}{n:0{width}d}"[:8]
            while reel in used or reel in reserved:
                n += 1
                reel = f"{prefix}{n:0{width}d}"[:8]
        used.add(reel)
        reels.append(reel.ljust(8))
    return reels


def track_code(audio_channels: int) -> str:
    """오디오 채널 수 → CMX 3600 트랙 코드

    `V`는 비디오 전용이라 그대로 내보내면 다빈치에 영상만 들어오고 소리가
    빠진다. 표준 코드는 다음과 같다.

      V     비디오만
      B     비디오 + 오디오 1채널
      AA/V  비디오 + 오디오 2채널(스테레오)
    """
    if audio_channels >= 2:
        return "AA/V"
    if audio_channels == 1:
        return "B"
    return "V"


def generate_edl(
    segments: list[dict],
    files: list[dict],
    title: str = "Video Studio",
    fps: float = 24.0,
) -> str:
    """KEEP 세그먼트와 파일 목록으로 CMX 3600 EDL 생성

    Args:
        segments: [{"globalStart": float, "globalEnd": float, "label": str, ...}]
        files: [{"name", "duration", "offset", "path", "fps", "tc_seconds",
                 "tc_drop_frame"}]
        title: EDL 타이틀
        fps: EDL 레이트 fallback. 파일에 `fps`가 있으면 그 값을 우선 사용

    Returns:
        EDL 문자열

    Note:
        모든 TC 계산은 초가 아니라 **프레임 번호** 기준이다. 초로 계산하면
        source와 record의 반올림이 따로 일어나 길이가 1프레임 어긋날 수
        있는데, CMX 3600은 (src_out - src_in) == (rec_out - rec_in)을
        요구한다.

        source TC는 미디어의 임베디드 시작 TC(`tc_seconds`)를 기준으로 한다.
        카메라가 time-of-day TC(예: 13:33:04:28)를 기록한 영상을 0 기준으로
        내보내면 다빈치가 존재하지 않는 TC를 찾게 되어 offline 처리된다.

        drop frame은 fps가 아니라 미디어 TC 표기에서 판정한다
        (`resolve_edl_drop_frame` 참조).

        한계: 레이트가 섞인 소스는 지원하지 않는다. CMX 3600은 FCM 헤더가
        하나뿐이라 최빈 레이트 하나로 통일되고, 다른 레이트의 파일은 source
        TC가 어긋난다. 그런 소스는 FCPXML로 내보내야 한다. 100fps 이상은
        2자리 프레임 필드로 표현할 수 없어 역시 지원하지 않는다.
    """
    if not segments or not files:
        return ""

    # EDL 전체 레이트: 파일들의 실제 fps 최빈값(없으면 fps 인자 fallback).
    edl_rate = resolve_edl_rate(files, fps)
    fps_int, actual_fps = resolve_tc_base(edl_rate)
    # 프레임 필드가 2자리뿐이라 100fps 이상은 표현할 수 없다. 컬럼이 밀린
    # EDL을 쓰느니 만들지 않는다(호출부가 빈 문자열이면 파일을 만들지 않고,
    # `warn_edl_risks`가 이유를 로그에 남긴다). FCPXML은 정상 생성된다.
    if fps_int >= 100:
        return ""
    drop_frame = resolve_edl_drop_frame(files, fps_int, actual_fps)
    fcm = "DROP FRAME" if drop_frame else "NON-DROP FRAME"

    # 첫 클립이 놓일 record TC. 다빈치는 EDL의 record TC를 타임라인의 절대
    # TC로 해석하므로, 타임라인 시작 TC와 다르면 그 차이만큼 앞이 빈다.
    # (타임라인이 00:00:00:00 시작인데 01:00:00:00으로 내보내면 앞 1시간이
    #  통째로 갭이 된다 — 실제로 관측된 증상이다.)
    # 타임라인을 01:00:00:00에서 시작하는 프로젝트에 맞추려면 이 상수만
    # "01:00:00" 으로 바꾸면 된다.
    record_start_frame = timecode_to_frames(
        RECORD_START_TC + (";00" if drop_frame else ":00"), fps_int
    )[0]

    # 파일별 누적 오프셋 배열 (bisect용)
    file_offsets = [f["offset"] for f in files]
    file_reels = assign_reel_names(files)

    lines = [
        f"TITLE: {title}",
        f"FCM: {fcm}",
        "",
    ]

    event_num = 1
    record_frames = 0  # 타임라인상 누적 위치(프레임)

    for seg in segments:
        g_start = seg["globalStart"]
        g_end = seg["globalEnd"]

        if g_end <= g_start:
            continue

        # 세그먼트가 걸치는 파일 범위 찾기
        fi_start = max(0, bisect.bisect_right(file_offsets, g_start) - 1)
        fi_end = max(0, bisect.bisect_right(file_offsets, g_end - 0.001) - 1)

        # 파일 경계에서 분할
        for fi in range(fi_start, fi_end + 1):
            f = files[fi]
            file_offset = f["offset"]
            file_end = file_offset + f["duration"]

            # 이 파일 내에서의 클립 범위
            clip_start = max(g_start, file_offset)
            clip_end = min(g_end, file_end)

            if clip_end <= clip_start:
                continue

            # 소스 프레임 = 미디어 임베디드 시작 TC + 파일 내 오프셋.
            # (임베디드 TC가 없는 파일은 tc0=0 → 0-based, 다빈치가 TC 없는
            #  클립에 부여하는 00:00:00:00과 일치)
            tc0_frames = seconds_to_frames(f.get("tc_seconds") or 0.0, actual_fps)
            src_in_frame = tc0_frames + seconds_to_frames(clip_start - file_offset, actual_fps)
            src_out_frame = tc0_frames + seconds_to_frames(clip_end - file_offset, actual_fps)

            clip_frames = src_out_frame - src_in_frame
            if clip_frames <= 0:
                continue

            src_in = frames_to_timecode(src_in_frame, fps_int, drop_frame)
            src_out = frames_to_timecode(src_out_frame, fps_int, drop_frame)

            # record 길이는 source 길이와 반드시 동일해야 한다(CMX 3600 요구).
            rec_in_frame = record_start_frame + record_frames
            rec_out_frame = rec_in_frame + clip_frames
            rec_in = frames_to_timecode(rec_in_frame, fps_int, drop_frame)
            rec_out = frames_to_timecode(rec_out_frame, fps_int, drop_frame)

            reel = file_reels[fi]

            # 트랙 코드는 파일별 오디오 구성에 맞춘다(키가 없으면 스테레오로 간주).
            ch = f.get("audio_channels")
            track = track_code(2 if ch is None else ch)
            lines.append(
                f"{event_num:03d}  {reel}  {track:<6}C        "
                f"{src_in} {src_out} {rec_in} {rec_out}"
            )
            # 전체 파일명(확장자 제외) — 다빈치가 8자를 넘는 reel name을
            # 이 코멘트에서 추출한다.
            lines.append(f"* FROM CLIP NAME: {os.path.splitext(f['name'])[0]}")

            if f.get("path"):
                lines.append(f"* SOURCE FILE: {f['path']}")

            # 라벨/이유가 있으면 코멘트 추가
            label = seg.get("label", "")
            reason = seg.get("reason", "")
            if label:
                comment = f"[{label}]"
                if reason:
                    comment += f" {reason}"
                lines.append(f"* COMMENT: {comment}")

            lines.append("")

            record_frames += clip_frames
            event_num += 1

    return "\n".join(lines)
