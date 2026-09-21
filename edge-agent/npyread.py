# -*- coding: utf-8 -*-
"""npyread.py — rev.9 선별 배열(.npy)을 표준 라이브러리만으로 읽는다.

왜 numpy 를 안 쓰는가
-------------------------------------------------------------------------------
읽어야 하는 것은 `y4_select_axis2.py` 가 `np.save` 한 **1차원 int64 인덱스 배열**
하나뿐이다(정렬된 전역 프레임 인덱스, 약 2,449개). 이것 하나 때문에 차량
시뮬레이터 전체에 numpy 를 매다는 것은 과하다 — 재생기는 엣지에서 도는 코드의
대역(代役)이므로 의존성이 가벼울수록 좋다.

대신 **아는 형태만 읽고 나머지는 큰 소리로 실패한다.** 조용히 잘못 읽는 것이
읽지 못하는 것보다 나쁘다.

.npy 형식 (numpy 문서 NEP 1)
-------------------------------------------------------------------------------
    \\x93NUMPY | major u8 | minor u8 | hlen (v1: u16 LE, v2+: u32 LE) | header ascii
    header 는 파이썬 dict 리터럴: {'descr': '<i8', 'fortran_order': False, 'shape': (2449,)}
    이후 헤더 끝이 64 바이트 정렬되도록 공백 패딩, 그다음 raw data.
"""
from __future__ import annotations

import ast
import struct
from typing import Sequence

MAGIC = b"\x93NUMPY"

#: 이 모듈이 다루는 dtype 만. 다른 것이 오면 실패한다.
_SUPPORTED = {
    "<i8": ("q", 8),
    "<i4": ("i", 4),
    "<u4": ("I", 4),
    "|b1": ("?", 1),
}


def read_npy_1d(path: str) -> list[int]:
    """1차원 배열을 파이썬 리스트로 읽는다. 아는 형태가 아니면 ValueError."""
    with open(path, "rb") as f:
        if f.read(6) != MAGIC:
            raise ValueError(f"{path}: .npy 매직이 아니다")
        major, _minor = struct.unpack("<BB", f.read(2))
        if major == 1:
            (hlen,) = struct.unpack("<H", f.read(2))
        elif major in (2, 3):
            (hlen,) = struct.unpack("<I", f.read(4))
        else:
            raise ValueError(f"{path}: 모르는 .npy 버전 {major}")

        header = ast.literal_eval(f.read(hlen).decode("latin1").strip())
        descr = header["descr"]
        shape = header["shape"]

        if header.get("fortran_order"):
            raise ValueError(f"{path}: fortran_order 는 다루지 않는다")
        if len(shape) != 1:
            raise ValueError(f"{path}: 1차원만 다룬다 (shape={shape})")
        if descr not in _SUPPORTED:
            raise ValueError(f"{path}: 모르는 dtype {descr!r} "
                             f"(아는 것 {sorted(_SUPPORTED)})")

        code, size = _SUPPORTED[descr]
        n = shape[0]
        raw = f.read(n * size)
        if len(raw) != n * size:
            raise ValueError(f"{path}: 데이터가 잘렸다 "
                             f"({len(raw)} B, {n * size} B 기대)")
        if f.read(1):
            raise ValueError(f"{path}: 데이터 뒤에 남은 바이트가 있다")
        return list(struct.unpack(f"<{n}{code}", raw))


def load_selection(path: str, n_frames: int) -> list[int]:
    """선별 인덱스 배열을 읽고 불변식을 검사한다.

    `y4_select_axis2.py:select()` 가 `np.sort` 로 반환하므로 정렬돼 있어야 하고,
    중복이 없어야 하며, 전역 프레임 범위 안에 있어야 한다. 하나라도 어긋나면
    읽은 파일이 기대한 것이 아니라는 뜻이다.
    """
    idx = read_npy_1d(path)
    if not idx:
        raise ValueError(f"{path}: 선별이 비었다")
    if idx != sorted(idx):
        raise ValueError(f"{path}: 정렬돼 있지 않다 — rev.9 가 np.sort 로 저장한다")
    if len(set(idx)) != len(idx):
        raise ValueError(f"{path}: 중복 인덱스가 있다")
    if idx[0] < 0 or idx[-1] >= n_frames:
        raise ValueError(f"{path}: 인덱스가 범위를 벗어났다 "
                         f"[{idx[0]}, {idx[-1]}] vs 프레임 {n_frames}")
    return idx


def selection_mask(idx: Sequence[int], n_frames: int) -> bytearray:
    """인덱스 배열 → 프레임당 1바이트 마스크. 재생 중 O(1) 조회용."""
    m = bytearray(n_frames)
    for i in idx:
        m[i] = 1
    return m


def load_canonical_selection(base_dir: str, arm: str, n_frames: int,
                             seeds: Sequence[int] = (1, 2, 3),
                             rate: float = 0.10) -> list[int]:
    """팔의 정본 선별을 읽고, **세 시드가 실제로 동일한지 검사한다.**

    rev.9 는 `g = 1.0` 이라 pool 이 전부 채점되고, 따라서
    `y4_select_axis2.select()` 의 무작위 채움(`take < budget`)을 타지 않아
    선별이 시드와 무관하게 결정적이다. 이 프로젝트의 런 모델(재생 2회 · 학습 6런)이
    **그 사실 위에 서 있으므로**, 사실이 아니게 되는 순간 큰 소리로 실패해야 한다.

    `g < 1` 인 조건으로 바꾸면 여기서 걸린다.
    """
    import os
    ref, ref_seed = None, None
    for s in seeds:
        p = os.path.join(base_dir, f"{arm}_mc_rate{rate:.2f}_s{s}.npy")
        idx = load_selection(p, n_frames)
        if ref is None:
            ref, ref_seed = idx, s
        elif idx != ref:
            raise ValueError(
                f"{arm}: 시드 {ref_seed} 와 {s} 의 선별이 다르다. "
                f"런 모델(재생 2회·학습 6런)이 시드 독립 선별을 전제한다 — "
                f"g < 1 조건으로 바뀌었는지 확인하라")
    return ref
