# -*- coding: utf-8 -*-
"""uplink.py — 업링크 큐. 토큰버킷 · store&forward · 즉시 드롭.

이 모듈이 계약을 **집행하는** 곳이다. 재생기가 선별분만 올리면 여기가 한 번도
돌지 않고, 그러면 이 프로젝트가 증명하려는 업링크 계약이 검증되지 않는다
(`docs/PRD_쐐기.md` 암묵지 3).

시간 모델 — **모의 시간(sim-time)으로 계산한다**
-------------------------------------------------------------------------------
재생을 12.5× 가속해도 큐 거동이 달라지면 안 된다. 그래서 벽시계가 아니라
**후보 프레임 수 × frame_interval_s** 로 시간을 센다. 가속 배수가 결과에 영향을
주지 않는 것이 요구사항이다.

    배출량 = link_bytes_per_s × Δt_sim
    도착지연 = (큐에서 빠져나간 시각) − (프레임 시각)      ≤ L_max 여야 한다

드롭 정책
-------------------------------------------------------------------------------
`config.UPLINK_DROP_POLICY = "drop"`. 큐가 용량에 닿으면 **새 후보를 즉시 버린다.**
대기(`wait`)하면 도착 지연이 무계가 되고 역압이 선별기로 전파된다
(`갈래A_전환.md §6-4`).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from contract import EdgeContract


@dataclass(slots=True)
class QueuedPayload:
    frame_id: int
    n_bytes: int
    t_enqueue_s: float          # 모의 시간 기준 프레임 시각
    t_depart_s: float = -1.0    # 큐에서 완전히 빠져나간 모의 시각


@dataclass(slots=True)
class UplinkStats:
    offered: int = 0            # 올리려 시도한 프레임 수 (= 선별분)
    accepted: int = 0
    dropped: int = 0
    bytes_accepted: int = 0
    bytes_drained: float = 0.0
    peak_queue_bytes: int = 0
    max_arrival_latency_s: float = 0.0

    @property
    def drop_rate(self) -> float:
        return self.dropped / self.offered if self.offered else 0.0


class UplinkQueue:
    """⑦ NVMe 업로드 큐의 모형.

    용량은 계약의 `Q_min` 을 쓴다 — 고른 값이 아니라 일주기 폐쇄에서 **유도된** 값이다.
    """

    def __init__(self, contract: EdgeContract, capacity_bytes: int | None = None):
        self.c = contract
        self.capacity = int(capacity_bytes if capacity_bytes is not None
                            else contract.queue_min_bytes)
        self.rate = contract.link_bytes_per_s
        self._q: deque[QueuedPayload] = deque()
        self._queued_bytes = 0
        self._head_remaining = 0.0     # 머리 항목의 남은 바이트 (부분 전송)
        self._t_sim = 0.0
        self.stats = UplinkStats()
        self.departed: list[QueuedPayload] = []

    # ─────────────────────────────────────────── 시간 전진
    def advance(self, dt_s: float) -> None:
        """모의 시간을 dt 만큼 흘리고 그동안 링크가 뺀 만큼 배출한다."""
        if dt_s <= 0:
            return
        self._t_sim += dt_s
        budget = self.rate * dt_s
        self.stats.bytes_drained += budget

        while budget > 0 and self._q:
            head = self._q[0]
            if self._head_remaining <= 0:
                self._head_remaining = float(head.n_bytes)
            take = min(budget, self._head_remaining)
            self._head_remaining -= take
            budget -= take
            if self._head_remaining <= 1e-9:
                self._q.popleft()
                self._queued_bytes -= head.n_bytes
                head.t_depart_s = self._t_sim - (budget / self.rate if self.rate else 0)
                lat = head.t_depart_s - head.t_enqueue_s
                if lat > self.stats.max_arrival_latency_s:
                    self.stats.max_arrival_latency_s = lat
                self.departed.append(head)
                self._head_remaining = 0.0

    # ─────────────────────────────────────────── 투입
    def offer(self, frame_id: int, n_bytes: int | None = None) -> bool:
        """선별된 프레임을 큐에 넣는다. 들어가면 True, 즉시 드롭이면 False."""
        n = int(n_bytes if n_bytes is not None else self.c.payload_bytes)
        self.stats.offered += 1
        if self._queued_bytes + n > self.capacity:
            self.stats.dropped += 1          # 즉시 드롭. 대기하지 않는다
            return False
        self._q.append(QueuedPayload(frame_id, n, self._t_sim))
        self._queued_bytes += n
        self.stats.accepted += 1
        self.stats.bytes_accepted += n
        if self._queued_bytes > self.stats.peak_queue_bytes:
            self.stats.peak_queue_bytes = self._queued_bytes
        return True

    # ─────────────────────────────────────────── 상태
    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    @property
    def sim_time_s(self) -> float:
        return self._t_sim

    def drain_to_empty(self) -> float:
        """주차 구간 모형 — 큐가 빌 때까지 시간을 흘린다. 걸린 모의 시간을 반환."""
        if not self._q:
            return 0.0
        need = (self._queued_bytes - (float(self._q[0].n_bytes) - self._head_remaining
                                      if self._head_remaining > 0 else 0.0))
        dt = max(need, self._queued_bytes) / self.rate
        before = self._t_sim
        self.advance(dt * 1.001)          # 부동소수 여유
        return self._t_sim - before

    def report(self) -> str:
        s = self.stats
        return (f"제안 {s.offered:,} · 수락 {s.accepted:,} · 드롭 {s.dropped:,} "
                f"({s.drop_rate*100:.2f}%) · 최대점유 {s.peak_queue_bytes/1e6:.1f} MB "
                f"· 최대도착지연 {s.max_arrival_latency_s/3600:.2f} h "
                f"(상한 {self.c.max_arrival_latency_s/3600:.2f} h)")
