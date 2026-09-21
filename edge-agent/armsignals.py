# -*- coding: utf-8 -*-
"""armsignals.py — schema/arm_signals.json 을 런타임에 읽는다 (stdlib).

팔별 가속기 산출(tiles_scored · tile_mask)과 value_score 는 frames.bin 에 없고,
rev.9 에서 numpy 로만 재현된다. `schema/gen_arm_signals.py` 가 그것을 스냅샷으로 굳혔고
(두 팔 × 3시드 선별 재현을 자기검증), 재생기는 이 파일만 읽는다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.abspath(os.path.join(_HERE, "..", "schema", "arm_signals.json"))


@dataclass(frozen=True, slots=True)
class FrameSignal:
    value_score: int
    tiles_scored: int
    tile_mask: int


class ArmSignals:
    """한 팔의 프레임별 신호. `pool` 순서(= 전역 인덱스 오름차순)가 재생 순서다."""

    def __init__(self, doc: dict, arm: str):
        if arm not in doc["arms"]:
            raise ValueError(f"스냅샷에 팔 {arm} 이 없다 (있는 것 {sorted(doc['arms'])})")
        if not doc.get("_selfcheck", {}).get("passed"):
            raise ValueError("arm_signals.json 자기검증이 통과하지 않은 스냅샷이다")
        a = doc["arms"][arm]
        self.arm = arm
        self.reclaim_mode: str = a["reclaim_mode"]
        self.rtl_build: str = a["rtl_build"]
        self.selection_file: str = a["selection_file"]
        self.selection_sha16: str = a["selection_sha16"]
        self.profile_sha: str = doc["profile"]["sha"]
        self.pool: list[int] = doc["pool_frame"]
        vs, ts, tm = doc["value_score"], doc["tiles_scored"][arm], doc["tile_mask"][arm]
        if not len(self.pool) == len(vs) == len(ts) == len(tm):
            raise ValueError("arm_signals.json 배열 길이가 서로 다르다")
        self._by_frame = {f: FrameSignal(v, t, m) for f, v, t, m in zip(self.pool, vs, ts, tm)}

    def __getitem__(self, frame: int) -> FrameSignal:
        return self._by_frame[frame]

    def __contains__(self, frame: int) -> bool:
        return frame in self._by_frame


def load(arm: str, path: str = DEFAULT_PATH) -> ArmSignals:
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} 가 없다. rev.9 venv 로 "
                                f"`schema/gen_arm_signals.py` 를 돌려 생성하라.")
    with open(path, encoding="utf-8") as f:
        return ArmSignals(json.load(f), arm)
