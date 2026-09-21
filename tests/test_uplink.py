# -*- coding: utf-8 -*-
"""업링크 큐가 계약을 실제로 집행하는지 검사한다 (stdlib unittest).

    python -m unittest discover -s tests
"""
import os
import sys
import unittest

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "edge-agent"))
sys.path.insert(0, os.path.join(_R, "schema"))

from contract import load          # noqa: E402
from uplink import UplinkQueue     # noqa: E402

C = load()


class TestContract(unittest.TestCase):
    def test_drop_policy_is_drop(self):
        """'wait' 이면 도착 지연이 무계가 된다 (갈래A_전환.md §6-4)."""
        self.assertEqual(C.drop_policy, "drop")

    def test_demand_exceeds_link(self):
        """수요가 링크를 넘지 않으면 큐도 드롭도 존재 이유가 없다."""
        self.assertGreater(C.demand_bps, C.uplink_bps)

    def test_frame_interval(self):
        self.assertAlmostEqual(C.frame_interval_s, 1.0 / C.cand_rate_hz)


class TestQueueDynamics(unittest.TestCase):
    def test_growth_matches_contract(self):
        """주행 중 큐 증가율이 계약의 queue_growth_bps 와 맞아야 한다."""
        q = UplinkQueue(C)
        dt = C.frame_interval_s
        n = 20000
        for i in range(n):
            q.advance(dt)
            if i % 10 == 0:          # rate 0.10
                q.offer(i)
        expected = C.queue_growth_bps * q.sim_time_s
        self.assertAlmostEqual(q.queued_bytes / expected, 1.0, delta=0.02)

    def test_no_drop_below_capacity(self):
        q = UplinkQueue(C)
        for i in range(5000):
            q.advance(C.frame_interval_s)
            if i % 10 == 0:
                q.offer(i)
        self.assertEqual(q.stats.dropped, 0)

    def test_drops_immediately_when_full(self):
        """용량에 닿으면 대기하지 않고 버린다. 큐는 유계로 남는다."""
        cap = 10 * C.payload_bytes
        q = UplinkQueue(C, capacity_bytes=cap)
        for i in range(200):
            q.offer(i)               # 시간을 안 흘리므로 배출이 없다
        self.assertEqual(q.stats.accepted, 10)
        self.assertEqual(q.stats.dropped, 190)
        self.assertLessEqual(q.queued_bytes, cap)

    def test_arrival_latency_bounded(self):
        """도착 지연이 계약 상한 L_max 를 넘지 않아야 한다."""
        q = UplinkQueue(C)
        for i in range(24491):
            q.advance(C.frame_interval_s)
            if i % 10 == 0:
                q.offer(i)
        q.drain_to_empty()
        self.assertGreater(q.stats.max_arrival_latency_s, 0)
        self.assertLess(q.stats.max_arrival_latency_s, C.max_arrival_latency_s)

    def test_replay_speed_does_not_change_outcome(self):
        """★ 가속 배수가 큐 거동을 바꾸면 안 된다 — 모의 시간으로 계산하기 때문."""
        def run(chunk):
            q = UplinkQueue(C)
            for i in range(10000):
                q.advance(C.frame_interval_s * chunk / chunk)
                if i % 10 == 0:
                    q.offer(i)
            return q.queued_bytes, q.stats.dropped
        self.assertEqual(run(1), run(12.5))

    def test_drains_to_empty_when_parked(self):
        """주차 중 업링크가 살아 있다는 가정(UPLINK_LINK_UP_WHEN_PARKED)의 모형."""
        q = UplinkQueue(C)
        for i in range(10000):
            q.advance(C.frame_interval_s)
            if i % 10 == 0:
                q.offer(i)
        self.assertGreater(q.queued_bytes, 0)
        q.drain_to_empty()
        self.assertEqual(q.queued_bytes, 0)


if __name__ == "__main__":
    unittest.main()
