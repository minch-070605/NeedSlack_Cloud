# -*- coding: utf-8 -*-
"""frames.py — rev.9 `05_데이터셋/board/frames.bin` 을 표준 라이브러리로 읽는다.

레이아웃 (rev.9 `02_전처리/s7_export_board.py`, version=2)
-------------------------------------------------------------------------------
    헤더 16 B   "<4sHIHH2s"  magic=b"NSLK" · version u16 · frame_count u32 ·
                             img_w u16 · img_h u16 · reserved 2s
    레코드 ×N   "<II4B"      frame_id u32 · timestamp_ms u32 ·
                             conf_q8 u8 · mean_p u8 · kinematic_u8 u8 · det_count u8
                + 픽셀 img_w*img_h B

    총 크기 = 16 + N × (12 + img_w·img_h)

파일이 560 MB 라 **통째로 읽지 않는다.** 레코드 단위로 seek 해서 필요한 것만 꺼낸다.
재생기는 순차로 훑고, 페이로드가 필요한 프레임에서만 픽셀을 읽는다.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Iterator, Optional

HEADER_FMT = "<4sHIHH2s"
HEADER_SIZE = struct.calcsize(HEADER_FMT)      # 16
REC_FMT = "<II4B"
REC_SIZE = struct.calcsize(REC_FMT)            # 12
MAGIC = b"NSLK"
EXPECTED_VERSION = 2


@dataclass(frozen=True, slots=True)
class FrameRecord:
    """12 B 원형 레코드. 판정 레코드의 신호 필드가 여기서 그대로 온다."""
    frame_id: int
    timestamp_ms: int
    conf_q8: int
    mean_p: int
    kinematic_u8: int
    det_count: int


class FramesFile:
    """frames.bin 랜덤 접근 리더. with 문으로 쓴다."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb")
        raw = self._f.read(HEADER_SIZE)
        if len(raw) != HEADER_SIZE:
            raise ValueError(f"{path}: 헤더가 짧다")
        magic, version, count, w, h, _ = struct.unpack(HEADER_FMT, raw)
        if magic != MAGIC:
            raise ValueError(f"{path}: 매직이 {magic!r} 다 ({MAGIC!r} 기대)")
        if version != EXPECTED_VERSION:
            raise ValueError(
                f"{path}: version={version}. v2 만 읽는다 — v1 은 night_flag/rain_flag "
                f"(oracle) 를 담고 있어 필드 의미가 다르다")
        self.version, self.frame_count, self.img_w, self.img_h = version, count, w, h
        self.pixel_size = w * h
        self.stride = REC_SIZE + self.pixel_size

        expected = HEADER_SIZE + count * self.stride
        actual = os.path.getsize(path)
        if actual != expected:
            raise ValueError(f"{path}: 크기 불일치 {actual:,} != {expected:,} "
                             f"(프레임 {count:,} × stride {self.stride:,})")

    # ─────────────────────────────────────────── 접근
    def _seek(self, i: int) -> None:
        if not 0 <= i < self.frame_count:
            raise IndexError(f"프레임 {i} 가 범위를 벗어났다 (0..{self.frame_count-1})")
        self._f.seek(HEADER_SIZE + i * self.stride)

    def record(self, i: int) -> FrameRecord:
        """레코드만 읽는다. 픽셀은 건너뛴다 — 재생 중 대부분의 프레임이 이 경로다."""
        self._seek(i)
        return FrameRecord(*struct.unpack(REC_FMT, self._f.read(REC_SIZE)))

    def pixels(self, i: int) -> bytes:
        """128×128 그레이스케일. 페이로드가 필요한 프레임에서만 부른다."""
        self._seek(i)
        self._f.seek(REC_SIZE, os.SEEK_CUR)
        return self._f.read(self.pixel_size)

    def iter_records(self, start: int = 0, stop: Optional[int] = None) -> Iterator[FrameRecord]:
        """순차 훑기. seek 없이 스트리밍하므로 재생기의 주 경로다."""
        stop = self.frame_count if stop is None else min(stop, self.frame_count)
        self._seek(start)
        for _ in range(stop - start):
            rec = struct.unpack(REC_FMT, self._f.read(REC_SIZE))
            self._f.seek(self.pixel_size, os.SEEK_CUR)
            yield FrameRecord(*rec)

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "FramesFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"<FramesFile {os.path.basename(self.path)} v{self.version} "
                f"{self.frame_count:,} frames {self.img_w}x{self.img_h} "
                f"stride={self.stride:,}B>")


def read_ref_frame(path: str, expect_size: int) -> bytes:
    """ref_frame.bin — 밝기 정규화 SAD 의 참조 프레임 (value_score 계산에 필요)."""
    data = open(path, "rb").read()
    if len(data) != expect_size:
        raise ValueError(f"{path}: {len(data):,} B ({expect_size:,} B 기대)")
    return data
