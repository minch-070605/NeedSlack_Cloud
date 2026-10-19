# -*- coding: utf-8 -*-
"""contract.py — schema/edge_contract.json 을 런타임에 읽는다.

docs/작업규칙.md §2: 계약값을 코드에 하드코딩하지 않는다. `1_000_000`, `144_500`,
`13.62` 를 소스에 적으면 rev.9 가 바뀌었을 때 조용히 갈라진다.

환경변수로 덮어쓰지 않는다 — 계약이지 설정이 아니다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.abspath(
    os.path.join(_HERE, "..", "schema", "edge_contract.json"))


@dataclass(frozen=True, slots=True)
class EdgeContract:
    """엣지가 클라우드에 넘기는 계약. 전부 rev.9/config.py 에서 유도된 값이다."""
    uplink_bps: int
    payload_bytes: int
    cand_rate_hz: float
    select_rate: float
    t_frame_ms: float
    drop_policy: str
    duty_period_s: float
    # 유도값
    demand_bps: float
    sustainable_rate: float
    max_drive_duty: float
    queue_growth_bps: float
    max_arrival_latency_s: float
    queue_min_bytes: float
    source_sha: str

    # ── 재생기가 쓰는 파생량 ──
    @property
    def frame_interval_s(self) -> float:
        """후보 한 장 사이의 실시간 간격. 20 Hz → 0.05 s"""
        return 1.0 / self.cand_rate_hz

    @property
    def link_bytes_per_s(self) -> float:
        """링크가 초당 빼낼 수 있는 바이트. 토큰버킷의 충전율."""
        return self.uplink_bps / 8.0

    def rederive(self, payload_bytes: float) -> dict[str, float]:
        """페이로드 크기만 바꿔 유도값을 다시 낸다. 식은 rev.9 `config.py` 의 `uplink_*` 함수와 같다.

        계약은 동결이라 고치지 않는다 — 실측 페이로드가 계약 `S` 와 다를 때 그 차이가 상한을
        얼마나 옮기는지 **발견으로 병기**하는 데만 쓴다. `rederive(self.payload_bytes)` 는 계약 JSON 의
        유도값을 그대로 내야 한다(tests/test_uplink.py 가 검사).
        """
        demand = self.select_rate * self.cand_rate_hz * payload_bytes * 8.0      # uplink_demand_bps
        sustainable = self.uplink_bps / (8.0 * payload_bytes * self.cand_rate_hz)
        duty = sustainable / self.select_rate                                    # uplink_max_drive_duty
        latency = max(0.0, 1.0 - duty) * self.duty_period_s                      # uplink_max_arrival_latency_s
        return {"payload_bytes": float(payload_bytes), "demand_bps": demand,
                "sustainable_rate": sustainable, "max_drive_duty": duty,
                "drive_hours_per_day": duty * self.duty_period_s / 3600.0,
                "queue_growth_bps": max(0.0, (demand - self.uplink_bps) / 8.0),
                "max_arrival_latency_s": latency,
                "queue_min_bytes": self.uplink_bps / 8.0 * latency}             # uplink_queue_min_bytes

    def describe(self) -> str:
        return (f"링크 {self.uplink_bps/1e6:.3f} Mbps · 수요 {self.demand_bps/1e6:.3f} Mbps · "
                f"듀티상한 {self.max_drive_duty:.4f} · "
                f"도착지연 ≤ {self.max_arrival_latency_s/3600:.2f} h · "
                f"큐 {self.queue_min_bytes/1e9:.2f} GB · 정책 {self.drop_policy}")


def load(path: str = DEFAULT_PATH) -> EdgeContract:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} 가 없다. `python schema/gen_edge_contract.py` 로 생성하라.")
    d = json.load(open(path, encoding="utf-8"))
    c, g = d["constants"], d["derived"]

    def cv(k):
        return c[k]["value"]

    def gv(k):
        return g[k]["value"]

    ct = EdgeContract(
        uplink_bps=int(cv("UPLINK_BPS")),
        payload_bytes=int(cv("UPLINK_PAYLOAD_BYTES")),
        cand_rate_hz=float(cv("CAND_RATE_HZ")),
        select_rate=float(d["select_rate_inst"]),
        t_frame_ms=float(cv("T_FRAME_MS")),
        drop_policy=str(cv("UPLINK_DROP_POLICY")),
        duty_period_s=float(cv("UPLINK_DUTY_PERIOD_S")),
        demand_bps=float(gv("uplink_demand_bps")),
        sustainable_rate=float(gv("uplink_sustainable_rate")),
        max_drive_duty=float(gv("uplink_max_drive_duty")),
        queue_growth_bps=float(gv("uplink_queue_growth_bps")),
        max_arrival_latency_s=float(gv("uplink_max_arrival_latency_s")),
        queue_min_bytes=float(gv("uplink_queue_min_bytes")),
        source_sha=str(d["_source"]["sha256_16"]),
    )
    # 계약 내부 정합성. gen_edge_contract.py 의 자기검증을 로드 시점에도 한 번 건다.
    if ct.drop_policy != "drop":
        raise ValueError(f"드롭 정책이 'drop' 이 아니다: {ct.drop_policy!r}. "
                         f"'wait' 이면 도착 지연이 무계가 된다 (갈래A_전환.md §6-4)")
    if not 0 < ct.max_drive_duty <= 1:
        raise ValueError(f"듀티 상한이 범위를 벗어났다: {ct.max_drive_duty}")
    return ct
