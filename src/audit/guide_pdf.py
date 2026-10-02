"""주요정보통신기반시설 상세가이드 PDF 에서 점검 코드(U-59 등)별 쪽 번호를 찾는다.

본문(조치 방법 등)은 추출하지 않는다: 표의 좌우 열 배치가 항목마다 달라 텍스트 추출로는 원문 경계를
신뢰할 수 없기 때문이다. 쪽 번호만 연결하고 담당자가 PDF 원문을 직접 확인하게 한다.
외부 도구 pdftotext(poppler)가 필요하다. 없으면 오류를 내며 조용히 건너뛰지 않는다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .models import ConfigError

# 항목 첫 쪽의 머리말: "U-59      UNIX > 3. 서비스 관리" 형태(코드 + 넓은 공백 + 분류 경로)
_HEADER = re.compile(r"^[ \t]{1,12}([A-Z]{1,5}-\d{2,3})[ \t]{2,}\S.*>", re.M)
MAX_SPAN = 12
_REF_LINE = re.compile(r"^\((상|중|하)\)\s*\[([^\]]*)\]\s*([A-Z]{1,5}-\d{1,3})\s+(.+?)\s*$", re.M)


def pdftotext_path() -> str | None:
    return shutil.which("pdftotext")


def extract_pages(pdf: Path) -> list[str]:
    exe = pdftotext_path()
    if exe is None:
        raise ConfigError("pdftotext(poppler)를 찾을 수 없어 PDF 쪽 번호를 연결할 수 없음. "
                          "설치하거나 --pdf 를 생략하세요")
    proc = subprocess.run([exe, "-enc", "UTF-8", "-layout", str(pdf), "-"], capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise ConfigError(f"pdftotext 실패: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout.decode("utf-8", "replace").split("\f")


def index_codes(pages: list[str]) -> dict[str, tuple[int, int, str]]:
    """{코드: (시작 쪽, 끝 쪽, 시작 쪽 머리부 텍스트)}. 끝 쪽은 다음 코드가 시작하기 직전 쪽이다."""
    starts: list[tuple[int, str]] = []
    seen = set()
    for i, page in enumerate(pages):
        m = _HEADER.search("\n".join(page.splitlines()[:10]))
        if m and m.group(1) not in seen:  # 한 코드는 한 번만 시작한다. 중복이면 첫 쪽을 유지
            seen.add(m.group(1))
            starts.append((i + 1, m.group(1)))
    out = {}
    for k, (start, code) in enumerate(starts):
        end = starts[k + 1][0] - 1 if k + 1 < len(starts) else len(pages)
        if end - start > MAX_SPAN:  # 장의 마지막 항목 뒤에 부록이 이어지는 경우 등: 범위를 믿을 수 없어 시작 쪽만 쓴다
            end = start
        out[code] = (start, max(start, end), "\n".join(pages[start - 1].splitlines()[:40]))
    return out


def parse_refs(text: str) -> list[tuple[str, str]]:
    """xlsx '관련 주통 평가기준' 셀에서 (코드, 항목명) 목록을 뽑는다."""
    return [(m.group(3), m.group(4)) for m in _REF_LINE.finditer(text)]


def _norm(s: str) -> str:
    s = s.casefold().translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}))
    return re.sub(r"\s+", "", s)


def locate(code: str, title: str, index: dict, pdf_name: str) -> tuple[str | None, str | None]:
    """(위치 문자열, 경고). 코드가 PDF 에 없으면 (None, None) — 다른 문서 체계의 코드일 수 있다."""
    if code not in index:
        return None, None
    start, end, head = index[code]
    pages = f"p.{start}" if start == end else f"p.{start}-{end}"
    warn = None
    if _norm(title) not in _norm(head):
        warn = f"{code}: 코드는 PDF {pages} 와 일치하나 항목명 표기가 xlsx('{title}')와 다름"
    return f"{pdf_name} {pages} — {code} {title}", warn
