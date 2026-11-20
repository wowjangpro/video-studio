# YouTube 오디오 보관함 → 로컬 음악 라이브러리

BGM 탭의 음악 추천이 쓰는 로컬 라이브러리를 만들고 갱신하는 도구다. 라이브러리 형식은
`python/bgm/music_library.py` 머리말에 있다 (폴더 하나로 완결 · 상대 경로만 저장 · 옮겨도 동작).

## 전제

- Orca 내장 브라우저 탭에 `https://studio.youtube.com/channel/<채널>/music` 이 채널 소유 계정으로 로그인되어 열려 있다.
  탭의 browserPageId 는 `orca tab list --json` 으로 찾는다.
- 실행은 `python/bgm-venv/bin/python` (Python 3.11).
- 같은 탭을 쓰므로 `collect.py` 와 `download.py` 를 동시에 돌리지 않는다. 도는 동안 그 탭은 건드리지 않는다.

## 명령

| 할 일 | 명령 |
| --- | --- |
| 새로 추가된 곡 목록 받기 (정기 갱신) | `collect.py <pageId> <root>` |
| 처음 한 번 전체 목록 받기 | `collect.py <pageId> <root> --full` |
| 음원 없는 곡 내려받기 | `download.py <pageId> <root> [--limit N] [--min-free-gb 15]` |
| 받아 둔 음원 전체 검사 (잘린 파일은 다시 받을 대상으로) | `download.py --verify <root>` |

긴 작업은 대화가 끝나도 돌도록 `nohup caffeinate -i ... > <root>/logs/<이름>.log 2>&1 &` 로 띄운다.
멈추려면 `pkill -f yt-audio-library/download.py`. 다시 실행하면 이어서 받는다.

## 보관함 화면에 대해 알아낸 것 (2026-11-15)

- 음악 7,139곡 (YouTube 라이선스 6,230 · CC BY 4.0 909). 음원은 MP3 320kbps, 전체 약 330시간 · 48GB.
- 목록은 한 번에 2,000번째까지만 넘어간다. 곡 수가 근삿값(APPROXIMATE)이면 그보다 일찍 멈춘다
  → 장르 × 분위기로 나누면 곡 수가 정확(EXACT)해져 끝까지 넘어간다.
- 각 행 요소(`ytmus-library-row`)의 `audioTrack` 에 원본 데이터가 있다: 곡 ID, 제목, 아티스트, 길이,
  장르·분위기·**악기**(화면에는 안 보임), 공개일, 라이선스, 아티스트·곡 링크.
- CC 곡 설명란 문구는 화면 "View details" 와 같은 규칙으로 `library_import.py` 가 만든다
  (Source / Artist 줄은 링크가 있을 때만).
- Download 버튼은 브라우저 다운로드로 `~/Downloads/제목 - 아티스트.mp3` 를 받는다. 본인 확인 창은 뜨지 않았다.
  `orca download --selector … --path …` 를 쓰면 원하는 경로(라이브러리 디스크)에 바로 저장되어 내장 디스크를 쓰지 않는다
  → download.py 의 기본 경로. 이 명령은 실제 클릭이라 사용자가 Orca 에서 한글 입력 중이면 거부된다.
- CC 곡의 Download 를 누르면 출처 안내 창이 열린 채로 남아 다음 실제 클릭을 가린다 → 매 곡 전후로 `#done-button` 을 누른다.
- 받은 파일 검사는 끝까지 디코드한 길이로 한다. `ffprobe` 의 길이는 MP3 머리 정보를 읽어서
  잘린 파일도 원래 길이로 보고한다 (앞 1.5MB 만 남긴 파일: ffprobe 133초, 디코드 37초).
- 장르·분위기가 비어 화면에 `–` 로 보이는 곡 4곡은 필터로 찾을 수 없어 download.py 가 건너뛴다
  (Tuscon Tease, Animal, Black and White, Dragonfly). 이런 곡은 검색으로 찾는다: 검색칸 `#text-input` 에
  `orca type` 으로 실제 키 입력을 한 뒤 Enter (`orca fill` 로 값만 넣으면 검색이 걸리지 않는다) → `Contains '…'` 칩.
- Orca 의 마우스 클릭은 사용자가 한글 입력 중이면 거부된다 → 클릭은 모두 페이지 안 `element.click()` 으로 보낸다.
- Download 를 누른 탭이 파일을 받는 대신 음원 주소(googlevideo.com)로 이동해 버린 적이 있다 (2026-11-16 22:00).
  download.py 는 매 곡 전에 탭이 보관함 화면인지 보고, 벗어났으면 `--library-url`(기본: 시작할 때 탭 주소)로
  돌아가 필터를 다시 걸고 이어 받는다. 탭이 이미 벗어난 상태로 시작할 때는 `--library-url` 을 꼭 준다.
- `orca eval` 은 결과를 늘 문자열로 돌려준다 (true → "true", 30 → "30").
