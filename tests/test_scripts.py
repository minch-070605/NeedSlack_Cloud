# -*- coding: utf-8 -*-
"""저장소의 PowerShell 스크립트가 Windows PowerShell 5.1 에서 파싱되는지 검사한다.

5.1 은 BOM 없는 .ps1 을 시스템 코드페이지(cp949)로 읽는다. 한글이 든 스크립트는
문자열이 깨지고 따옴표 짝이 어긋나 **실행 전에 파싱 오류**로 죽는다.
편집기가 BOM 을 조용히 떼어내는 일이 잦아 테스트로 고정한다.

    python -m unittest discover -s tests
"""
import glob
import os
import unittest

_R = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SKIP = ("팀 연구", ".git", ".venv")


def _ps1_files():
    for p in glob.glob(os.path.join(_R, "**", "*.ps1"), recursive=True):
        rel = os.path.relpath(p, _R)
        if not rel.split(os.sep)[0] in _SKIP:
            yield rel, p


class TestPowerShellEncoding(unittest.TestCase):
    def test_non_ascii_ps1_has_utf8_bom(self):
        files = list(_ps1_files())
        self.assertTrue(files, "검사할 .ps1 이 없다 — 경로가 바뀌었나")
        for rel, p in files:
            with open(p, "rb") as f:
                raw = f.read()
            if all(b < 0x80 for b in raw):
                continue    # ASCII 뿐이면 인코딩과 무관하다
            with self.subTest(file=rel):
                self.assertTrue(raw.startswith(b"\xef\xbb\xbf"),
                                f"{rel}: 비ASCII 문자가 있는데 UTF-8 BOM 이 없다")


if __name__ == "__main__":
    unittest.main()
