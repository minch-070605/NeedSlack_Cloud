# -*- coding: utf-8 -*-
"""verdict.py — 판정 레코드의 단일 소스 (S1 와이어 계약).

재생기·수집기·조정기·스냅샷이 **전부 이 모듈을 import 한다.** 필드를 세 곳에 따로
적으면 반드시 갈라진다.

설계 근거
-------------------------------------------------------------------------------
- 원형 12 B 는 rev.9 `02_전처리/s7_export_board.py` 의 `REC_FMT = "<II4B"` 다.
  frame_id u32 · timestamp_ms u32 · conf_q8 · mean_p · kinematic_u8 · det_count.
- 기전 필드(reclaim_mode/granule_k/...)는 `docs/설계결정-0001` §4 의 요구다.
- 와이어 포맷은 **JSON**. protobuf 가 아니다 — MVP 메시지가 ~122 건이라
  보고서 §1-2 의 건당 과금 논거가 적용되지 않는다. 필드 이름과 의미는 proto 와
  1:1 이 되게 정의해, M4 에서 그대로 옮길 수 있게 한다.

의존성
-------------------------------------------------------------------------------
**표준 라이브러리만 쓴다.** 이 모듈은 모든 컴포넌트가 import 하므로 무겁게 만들지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

SCHEMA_VERSION = 1

# ─────────────────────────────────────────────────────────── 런 모델 (S3)
ARMS = ("STA_B", "DYN_B")
SEEDS = (1, 2, 3)


def run_id(arm: str, seed: int) -> str:
    """학습 런 식별자. 예: DYN_B-s1

    시드는 **학습에만** 작용한다. 선별에는 작용하지 않는다 — 아래 vin_of 주석 참조.
    """
    if arm not in ARMS:
        raise ValueError(f"알 수 없는 팔: {arm}. 가능: {ARMS}")
    if seed not in SEEDS:
        raise ValueError(f"알 수 없는 시드: {seed}. 가능: {SEEDS}")
    return f"{arm}-s{seed}"


def vin_of(arm: str) -> str:
    """팔 하나를 차량 하나로 모델링한다. **시드는 들어가지 않는다.**

    ★ rev.9 실측: `g = 1.0` 이라 pool 프레임이 전부 채점되고, 따라서
      `y4_select_axis2.select()` 의 무작위 채움 경로(`take < budget`)를 타지 않는다.
      그 결과 **선별 배열이 시드와 무관하게 동일하다** — s1 == s2 == s3 (바이트 동일 확인).

      선별 2종 (재생·업로드 2회)   ← vin 이 가르는 것
      학습   6런 (팔 × 시드)        ← run_id 가 가르는 것

      같은 데이터를 3번 올리는 것은 순수한 낭비이므로 재생은 팔당 1회만 한다.

    ⚠️ `g < 1` 인 조건(레짐 A 등)으로 바꾸면 무작위 채움이 살아나 시드별로 선별이
       갈린다. 그때는 이 모델이 깨진다 — `load_canonical_selection()` 이 그것을 잡는다.
    """
    if arm not in ARMS:
        raise ValueError(f"알 수 없는 팔: {arm}. 가능: {ARMS}")
    return f"sim-{arm}"


def dataset_id(arm: str) -> str:
    """데이터셋 스냅샷 식별자. 팔당 하나."""
    if arm not in ARMS:
        raise ValueError(f"알 수 없는 팔: {arm}. 가능: {ARMS}")
    return f"ds-{arm}"


def selection_filename(arm: str, seed: int = 1, rate: float = 0.10) -> str:
    """rev.9 선별 배열 파일명. 내용은 정렬된 int64 전역 프레임 인덱스 배열.

    세 시드 파일의 내용이 동일하므로 s1 을 정본으로 쓴다(위 vin_of 주석).
    """
    return f"{arm}_mc_rate{rate:.2f}_s{seed}.npy"


# ─────────────────────────────────────────────────────────── 열거형
class ReclaimMode(str, Enum):
    PREEMPT_RESUME = "preempt_resume"            # rev.9/07_RTL
    ADMISSION_NONPREEMPT = "admission_nonpreempt"  # Teammate/slack_reclaim_zybo_v5
    STATIC_RESERVE = "static_reserve"


class FrameState(str, Enum):
    PENDING = "PENDING"        # 판정만 도착. ★ 정상 상태다. 오래 머물러도 알람 금지
    UPLOADED = "UPLOADED"      # 페이로드 도착
    INDEXED = "INDEXED"        # 체크섬 검증 통과
    IN_DATASET = "IN_DATASET"  # 데이터셋 스냅샷 편입
    DROPPED = "DROPPED"        # 토큰 고갈로 버림 (config.UPLINK_DROP_POLICY)
    QUARANTINED = "QUARANTINED"  # 검증 실패


#: 허용된 상태 전이. 단조여야 한다 — 역순 도착은 UPSERT 로 흡수한다.
ALLOWED_TRANSITIONS: dict[FrameState, frozenset[FrameState]] = {
    FrameState.PENDING: frozenset({FrameState.UPLOADED, FrameState.DROPPED}),
    FrameState.UPLOADED: frozenset({FrameState.INDEXED, FrameState.QUARANTINED}),
    FrameState.INDEXED: frozenset({FrameState.IN_DATASET}),
    FrameState.IN_DATASET: frozenset(),
    FrameState.DROPPED: frozenset(),
    FrameState.QUARANTINED: frozenset(),
}


def can_transition(src: FrameState, dst: FrameState) -> bool:
    return dst in ALLOWED_TRANSITIONS[src]


# ─────────────────────────────────────────────────────────── 키
def frame_uuid(vin: str, boot_id: str, drive_session: int, frame_id: int) -> str:
    """전역 유일 프레임 키이자 **멱등 키**.

    frame_id 는 u32 로컬 인덱스라 재부팅·세션 경계에서 충돌한다. 셀룰러 재접속 후
    재전송은 확률이 아니라 확실성이므로, 이 키로 DynamoDB 조건부 쓰기에 걸어 중복을 흡수한다.
    """
    raw = f"{vin}\x1f{boot_id}\x1f{drive_session}\x1f{frame_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]   # 128-bit


# ─────────────────────────────────────────────────────────── 레코드
@dataclass(slots=True)
class PayloadRef:
    """번들 객체 안에서 이 프레임이 차지하는 구간. S3 Range GET 으로 꺼낸다."""
    bucket: str
    key: str
    offset: int
    length: int
    sha256: str


@dataclass(slots=True)
class Verdict:
    # ── 식별 ──
    vin: str
    boot_id: str
    drive_session: int
    frame_id: int                       # frames.bin 원형. 전역 유일이 아니다
    t_frame_mono_ms: int                # frames.bin 원형. 차량 단조 시계
    t_frame_utc: str                    # ISO8601

    # ── frames.bin REC_FMT "<II4B" 원형 신호 ──
    conf_q8: int
    mean_p: int
    kinematic_u8: int
    det_count: int

    # ── 가속기 산출 ──
    value_score: int
    tile_mask: int
    tiles_scored: int

    # ── 회수 기전 (설계결정-0001 §4) ──
    reclaim_mode: ReclaimMode
    rtl_build: str
    #: 선점 입도. rev.9 헤드라인이 k→∞(L_pre≈0) 가정 위에 있어(설계결정-0001 §3-3)
    #: 정수로 적을 값이 없다. **None 이 "입도 미지정"의 정식 표현이다.**
    granule_k: Optional[int] = None
    l_pre_cyc: int = 0
    resume_cnt: int = 0

    # ── 선별 ──
    selected: bool = False
    profile_sha: str = ""

    # ── 페이로드 (selected 일 때만) ──
    payload: Optional[PayloadRef] = None

    schema_version: int = SCHEMA_VERSION

    # ─────────────────────────────────────────── 파생
    @property
    def frame_uuid(self) -> str:
        return frame_uuid(self.vin, self.boot_id, self.drive_session, self.frame_id)

    def validate(self) -> None:
        """와이어에 싣기 전 불변식 검사. 어기면 버그이므로 조용히 통과시키지 않는다."""
        for name in ("conf_q8", "mean_p", "kinematic_u8", "det_count", "tile_mask"):
            v = getattr(self, name)
            if not 0 <= v <= 255:
                raise ValueError(f"{name}={v} 가 u8 범위를 벗어났다")
        if self.selected and self.payload is None:
            raise ValueError("selected=True 인데 payload 가 없다")
        if not self.selected and self.payload is not None:
            raise ValueError("selected=False 인데 payload 가 있다")
        if self.tiles_scored > bin(self.tile_mask).count("1"):
            raise ValueError("tiles_scored 가 tile_mask 의 비트 수를 넘었다")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reclaim_mode"] = self.reclaim_mode.value
        d["frame_uuid"] = self.frame_uuid
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Verdict":
        d = dict(d)
        d.pop("frame_uuid", None)          # 파생값. 저장하되 복원 입력으로 쓰지 않는다
        ver = d.pop("schema_version", SCHEMA_VERSION)
        if ver != SCHEMA_VERSION:
            raise ValueError(f"schema_version {ver} 를 이 코드가 모른다 "
                             f"(아는 값 {SCHEMA_VERSION}). 필드 삭제·의미 변경 금지 규약 확인")
        p = d.pop("payload", None)
        d["reclaim_mode"] = ReclaimMode(d["reclaim_mode"])
        v = Verdict(**d, schema_version=ver)
        v.payload = PayloadRef(**p) if p else None
        return v


# ─────────────────────────────────────────────────────────── 배치 봉투
@dataclass(slots=True)
class VerdictBatch:
    """MQTT 메시지 1건에 담기는 판정 묶음."""
    vin: str
    sent_at: str                                   # ISO8601
    verdicts: list[Verdict] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> str:
        return json.dumps({
            "schema_version": self.schema_version,
            "vin": self.vin,
            "sent_at": self.sent_at,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def from_json(s: str) -> "VerdictBatch":
        d = json.loads(s)
        return VerdictBatch(
            vin=d["vin"], sent_at=d["sent_at"],
            verdicts=[Verdict.from_dict(v) for v in d["verdicts"]],
            schema_version=d.get("schema_version", SCHEMA_VERSION))


# ─────────────────────────────────────────────────────────── MQTT 토픽
def topic_verdict(vin: str) -> str:
    return f"ns/v1/{vin}/curation/verdict"


def topic_telemetry(vin: str) -> str:
    return f"ns/v1/{vin}/curation/telemetry"
