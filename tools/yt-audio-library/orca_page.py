"""Orca 내장 브라우저에 열린 YouTube Studio 오디오 보관함 탭을 다루는 공용 함수

- 모든 조작은 화면 UI 를 그대로 쓴다 (필터 창 체크박스·Apply, 정렬 머리글, 다음 페이지 버튼).
  보관함 내부 API 를 직접 호출하지 않는다.
- 클릭은 페이지 안 `element.click()` 으로 보낸다. Orca 의 마우스 입력은 사용자가 한글 입력 중이면
  거부되기 때문이다.
"""
import json
import subprocess
import time

GENRES = ["Alternative & Punk", "Ambient", "Children's", "Cinematic", "Classical", "Country & Folk",
          "Dance & Electronic", "Hip-Hop & Rap", "Holiday", "Jazz & Blues", "Pop", "R&B & Soul",
          "Reggae", "Rock"]
MOODS = ["Angry", "Bright", "Calm", "Dark", "Dramatic", "Funky", "Happy", "Inspirational", "Romantic", "Sad"]

# 보관함 목록은 앞에서부터 2,000번째까지만 넘길 수 있고, 곡 수가 근삿값일 때는 그보다 일찍 멈춘다.
PAGE_CAP = 2000

_EXTRACT = r"""(() => {
  const t = document.querySelector('ytmus-library-table');
  if (!t) return JSON.stringify({ lost: true });
  const rows = [...document.querySelectorAll('ytmus-library-row')].map(r => {
    const rc = r.querySelector('#row-container');
    const cells = rc ? [...rc.children] : [];
    const lic = [...r.querySelectorAll('ytmus-license-button p.tooltip-paragraph')].map(p => p.textContent.trim()).filter(Boolean);
    return { track: r.audioTrack, genreLabel: cells[2] ? cells[2].innerText.trim() : null,
             moodLabel: cells[3] ? cells[3].innerText.trim() : null, licenseText: lic };
  });
  return JSON.stringify({ footer: t.footerSpec, filters: t.filters, order: t.request && t.request.order, rows });
})()"""

_STATE = r"""(() => { const c = document.querySelector('ytmus-library-table');
  if (!c) return JSON.stringify({ lost: true });
  const rows = [...document.querySelectorAll('ytmus-library-row')].map(r => {
    const rc = r.querySelector('#row-container'); const cells = rc ? [...rc.children] : [];
    return [cells[2] ? cells[2].innerText.trim() : '', cells[3] ? cells[3].innerText.trim() : ''];
  });
  return JSON.stringify({ filters: c.filters, footer: c.footerSpec, order: c.request && c.request.order,
    n: rows.length, rowLabels: rows, noMatch: c.showNoMatch, status: c.overallStatus,
    chips: [...document.querySelectorAll('ytmus-library-filter ytcp-chip')].map(e => e.innerText.trim()) }) })()"""

_SET_FILTER = r"""((kind, target) => {
  const visible = (e) => e.offsetParent !== null;
  const apply = [...document.querySelectorAll('#apply-button')].find(visible);
  const boxes = [...document.querySelectorAll('ytcp-checkbox-lit')].filter(visible);
  const labels = boxes.map(cb => cb.parentElement.innerText.trim());
  const isKind = kind === 'Genre' ? labels.includes('Rock') : labels.includes('Funky');
  if (!apply || !isKind) {
    if (apply) return 'OTHER_DIALOG';
    const chip = [...document.querySelectorAll('ytmus-library-filter ytcp-chip')].find(e => e.innerText.trim().startsWith(kind + ':'));
    if (chip) { chip.click(); return 'OPENED'; }
    // 칩이 없으면 첫 행의 "이 장르/분위기로 필터" 버튼으로 칩을 만든다
    const row = document.querySelector('ytmus-library-row');
    const btn = row && row.querySelector(kind === 'Genre' ? '#genre-filter' : '#mood-filter');
    if (!btn) return 'NO_CHIP';
    btn.click();
    return 'CHIP_ADDED';
  }
  for (const cb of boxes) {
    const box = cb.querySelector('[role=checkbox]');
    if (!box) continue;
    const want = cb.parentElement.innerText.trim() === target;
    if ((box.getAttribute('aria-checked') === 'true') !== want) box.click();
  }
  apply.click();
  return 'APPLIED';
})(KIND, TARGET)"""


class OrcaError(RuntimeError):
    pass


class PageLost(RuntimeError):
    """탭이 보관함 화면을 벗어났다 (예: Download 가 파일 대신 음원 주소로 이동, 2026-11-16 관측)."""


class LibraryPage:
    def __init__(self, page_id: str):
        self.page_id = page_id
        self.library_url: str | None = None  # 처음 연결했을 때의 보관함 주소 (복구용)

    # ── 기본 ──
    def _orca(self, *args, retries: int = 5):
        last = None
        for _ in range(retries):
            try:
                out = subprocess.run(["orca", *args, "--page", self.page_id, "--json"],
                                     capture_output=True, text=True, timeout=90)
            except subprocess.TimeoutExpired:
                last = f"orca {args[0]} 90초 시간 초과"
                time.sleep(3)
                continue
            try:
                data = json.loads(out.stdout)
            except json.JSONDecodeError:
                last = out.stderr or out.stdout
                time.sleep(3)
                continue
            if data.get("ok"):
                return data["result"]
            last = data.get("error")
            time.sleep(3)
        raise OrcaError(json.dumps(last, ensure_ascii=False))

    def ev(self, expr: str) -> str:
        """페이지에서 식을 실행한다. `orca eval` 은 결과를 늘 문자열로 돌려준다 (true → "true", 30 → "30")."""
        return self._orca("eval", "--expression", expr)["result"]

    def ev_bool(self, expr: str) -> bool:
        return str(self.ev(expr)).strip() == "true"

    def ev_int(self, expr: str) -> int:
        try:
            return int(str(self.ev(expr)).strip())
        except ValueError:
            return 0

    # ── 보관함 화면 유지 ──
    def on_library(self) -> bool:
        try:
            return self.ev_bool("(() => location.hostname === 'studio.youtube.com' && location.pathname.endsWith('/music')"
                                " && !!document.querySelector('ytmus-library-table'))()")
        except OrcaError:
            return False

    def remember_library_url(self):
        url = self.ev("location.href")
        if "studio.youtube.com" in url and "/music" in url:
            self.library_url = url.split("?")[0].split("#")[0]
        if not self.library_url:
            raise OrcaError(f"탭이 오디오 보관함이 아닙니다: {url[:120]}")

    def recover(self):
        """보관함 주소로 돌아가 목록이 뜰 때까지 기다린다. 필터·정렬은 호출한 쪽이 다시 건다."""
        if not self.library_url:
            raise OrcaError("돌아갈 보관함 주소를 모릅니다")
        self._orca("goto", "--url", self.library_url)
        for _ in range(60):
            time.sleep(1)
            if self.on_library() and self.ev_int("document.querySelectorAll('ytmus-library-row').length") > 0:
                time.sleep(2)
                return
        raise OrcaError("보관함으로 돌아갔지만 목록이 뜨지 않습니다")

    def key(self, key: str):
        return self._orca("keypress", "--key", key)

    def extract(self) -> dict:
        d = json.loads(self.ev(_EXTRACT))
        if d.get("lost"):
            raise PageLost()
        return d

    def state(self) -> dict:
        d = json.loads(self.ev(_STATE))
        if d.get("lost"):
            raise PageLost()
        return d

    # ── 필터 · 정렬 · 페이지 ──
    def set_filter(self, kind: str, label: str) -> dict:
        """kind = 'Genre' | 'Mood'. 그 종류의 필터를 label 하나로 맞추고 결과 첫 페이지 상태를 돌려준다."""
        expr = _SET_FILTER.replace("KIND", json.dumps(kind)).replace("TARGET", json.dumps(label))
        for _ in range(8):
            r = self.ev(expr)
            if r == "APPLIED":
                break
            if r == "OTHER_DIALOG":
                self.key("Escape")
            time.sleep(1.5)
        else:
            raise OrcaError(f"{kind} 필터를 바꾸지 못했습니다: {label} ({r})")
        # 끝난 조건: 칩이 "<종류>: <값>" 이고, 목록이 다 불러와졌고, 보이는 행이 그 값이다
        # (중간 필터나 이전 필터의 결과를 끝난 것으로 보지 않으려고 행 내용까지 본다)
        col = 0 if kind == "Genre" else 1
        for _ in range(40):
            time.sleep(1)
            st = self.state()
            if f"{kind}: {label}" not in st["chips"] or st["status"] != "SUCCESS":
                continue
            if st["footer"]["firstElementIndex"] not in (0, 1):
                continue
            if st["n"] == 0:
                if st["noMatch"]:
                    return st
                continue
            hits = sum(1 for r in st["rowLabels"] if r[col] == label)
            if hits >= 0.9 * st["n"]:
                return st
        raise OrcaError(f"필터 결과를 기다리다 멈췄습니다: {kind} {label}")

    def set_filters(self, genre: str, mood: str) -> dict:
        self.set_filter("Genre", genre)
        time.sleep(2)
        return self.set_filter("Mood", mood)

    def click_header(self, name: str):
        self.ev("(() => { [...document.querySelectorAll('ytmus-library-table button.header-name')]"
                ".find(e => e.textContent.trim() === " + json.dumps(name) + ").click(); return true })()")
        time.sleep(4)

    def ensure_date_order(self, direction: str):
        """direction = 'ASC' | 'DESC' (추가일 기준)"""
        for _ in range(3):
            order = self.state()["order"] or {}
            if order.get("orderField", "").endswith("RELEASE_DATE") and order.get("orderDirection", "").endswith(direction):
                return
            self.click_header("Added")
        raise OrcaError(f"정렬을 {direction} 로 바꾸지 못했습니다")

    def next_page(self) -> bool:
        """다음 페이지로 넘긴다. 넘어가지 않으면 False (보관함 넘김 한도)."""
        first = self.state()["footer"]["firstElementIndex"]
        self.ev("(() => { document.querySelector('ytmus-library-table #navigate-after').click(); return true })()")
        for _ in range(40):
            time.sleep(1)
            if self.state()["footer"]["firstElementIndex"] != first:
                return True
        return False

    def close_dialogs(self):
        """CC 곡을 받으면 뜨는 출처 안내 창 등 열린 창을 닫는다 (열린 창은 실제 클릭을 가린다)."""
        self.ev("(() => { if (!document.querySelector('tp-yt-iron-overlay-backdrop.opened')) return 0;"
                " const b = document.querySelector('ytmus-license-dialog #done-button'); if (b) b.click(); return 1 })()")

    def download_to(self, track_id: str, path: str) -> str:
        """그 곡의 Download 버튼을 실제 클릭으로 눌러 path 에 바로 저장한다 (`orca download`).

        돌려주는 값: 'OK' | 'NOTFOUND' | 'FAILED:<이유>'
        """
        if not self.on_library():
            raise PageLost()
        self.close_dialogs()
        r = self.ev("(() => { document.querySelectorAll('[data-ytlib-dl]').forEach(e => e.removeAttribute('data-ytlib-dl'));"
                    " const row = [...document.querySelectorAll('ytmus-library-row')]"
                    ".find(r => r.audioTrack && r.audioTrack.trackId === " + json.dumps(track_id) + ");"
                    " if (!row) return 'NOTFOUND'; row.scrollIntoView({ block: 'center' });"
                    " row.querySelector('#download button').setAttribute('data-ytlib-dl', '1'); return 'OK' })()")
        if r != "OK":
            return r
        time.sleep(0.5)
        try:
            # 같은 클릭을 되풀이하지 않는다 (탭이 이미 이동했으면 재시도는 시간만 쓴다)
            self._orca("download", "--selector", 'button[data-ytlib-dl="1"]', "--path", path, retries=1)
        except OrcaError as e:
            if not self.on_library():
                raise PageLost() from e
            return f"FAILED:{e}"
        finally:
            try:
                self.close_dialogs()
            except OrcaError:
                pass
        return "OK"

    def download_row(self, track_id: str) -> bool:
        """현재 페이지에서 그 곡의 Download 버튼을 누른다. 행이 없으면 False."""
        r = self.ev("(() => { const row = [...document.querySelectorAll('ytmus-library-row')]"
                    ".find(r => r.audioTrack && r.audioTrack.trackId === " + json.dumps(track_id) + ");"
                    " if (!row) return 'NOTFOUND'; row.scrollIntoView({ block: 'center' });"
                    " row.querySelector('#download button').click(); return 'OK' })()")
        return r == "OK"
