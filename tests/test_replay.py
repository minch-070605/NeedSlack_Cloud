# -*- coding: utf-8 -*-
"""재생기가 계약을 실제로 돌리고, 두 평면에 올바른 것을 내보내는지 검사한다 (stdlib unittest).

팀 연구 자산도 로컬 스택도 쓰지 않는다 — 합성 프레임·합성 신호·MemorySink 로 돈다.

    python -m unittest discover -s tests
"""
import hashlib
import json
import os
import sys
import unittest
from datetime import datetime

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "edge-agent"))
sys.path.insert(0, os.path.join(_R, "schema"))

import armsignals                   # noqa: E402
import replay as RP                 # noqa: E402
import verdict as V                 # noqa: E402
from contract import load           # noqa: E402
from frames import FrameRecord      # noqa: E402

C = load()
T0 = datetime(2026, 9, 22, 0, 0, 0)


def make_signals(arm, n, first=100):
    """pool = [first, first+n). DYN 은 타일 2개, STA 는 1개."""
    pool = list(range(first, first + n))
    nt = 2 if arm == "DYN_B" else 1
    doc = {
        "arms": {arm: {"reclaim_mode": "preempt_resume" if arm == "DYN_B" else "static_reserve",
                       "rtl_build": "y4sim-test", "selection_file": "x.npy",
                       "selection_sha16": "0" * 16}},
        "profile": {"sha": "f" * 16},
        "_selfcheck": {"passed": True},
        "pool_frame": pool,
        "value_score": [(f * 7) % 250 for f in pool],
        "tiles_scored": {arm: [nt] * n},
        "tile_mask": {arm: [0b0110 if nt == 2 else 0b0100] * n},
    }
    return armsignals.ArmSignals(doc, arm)


def record_of(f):
    return FrameRecord(f, f * 500, 40, 100, 50, 1)


def payload_of(f):
    """프레임마다 다른 크기·내용(약 8 KB). 결정적이라 재읽기해도 같다.

    20 Hz 로 전부 선별하면 약 165 KB/s — 링크(125 KB/s)를 넘어 큐가 쌓인다."""
    return hashlib.sha256(str(f).encode()).digest() * (250 + f % 13)


def run(arm="DYN_B", n=400, every=10, **cfg):
    sig = make_signals(arm, n)
    selected = set(sig.pool[::every])
    sink = RP.MemorySink()
    rp = RP.Replayer(RP.ReplayConfig(arm=arm, boot_id="boot-test", t0_utc=T0, **cfg),
                     C, sig, selected, record_of, payload_of, sink)
    return rp.run(), sink, selected, sig


def verdicts_of(sink):
    out = []
    for topic, body in sink.published:
        if topic.endswith("/verdict"):
            out.extend(V.VerdictBatch.from_json(body).verdicts)
    return out


def telemetry_of(sink):
    return [V.Telemetry.from_json(b) for t, b in sink.published if t.endswith("/telemetry")]


class TestFlow(unittest.TestCase):
    def test_all_candidates_flow_but_only_selected_are_verdicts(self):
        """후보는 전부 흐르고(계약이 돈다), 판정은 선별분만 나간다 (보고서 §1-2)."""
        r, sink, sel, _ = run()
        self.assertEqual(r.candidates, 400)
        self.assertEqual(r.selected, len(sel))
        vs = verdicts_of(sink)
        self.assertEqual(len(vs), r.accepted)
        self.assertEqual({v.frame_id for v in vs}, sel)
        self.assertTrue(all(v.selected for v in vs))
        tel = telemetry_of(sink)
        self.assertEqual(sum(t.candidates for t in tel), 400)

    def test_verdicts_carry_arm_signals_and_mechanism(self):
        _, sink, _, sig = run(arm="DYN_B")
        for v in verdicts_of(sink):
            v.validate()
            s = sig[v.frame_id]
            self.assertEqual((v.value_score, v.tiles_scored, v.tile_mask),
                             (s.value_score, s.tiles_scored, s.tile_mask))
            self.assertEqual(v.reclaim_mode, V.ReclaimMode.PREEMPT_RESUME)
            self.assertIsNone(v.granule_k)
            self.assertEqual(v.profile_sha, "f" * 16)
            self.assertEqual(v.vin, "sim-DYN_B")

    def test_sta_arm_is_static_reserve(self):
        _, sink, _, _ = run(arm="STA_B")
        self.assertTrue(all(v.reclaim_mode == V.ReclaimMode.STATIC_RESERVE
                            for v in verdicts_of(sink)))

    def test_frame_uuids_unique(self):
        _, sink, _, _ = run()
        uu = [v.frame_uuid for v in verdicts_of(sink)]
        self.assertEqual(len(uu), len(set(uu)))


class TestPayloadPlane(unittest.TestCase):
    def test_range_read_of_bundle_returns_exact_frame(self):
        """판정의 (key, offset, length, sha256) 로 번들을 Range 읽기하면 그 프레임이 나온다."""
        _, sink, _, _ = run()
        for v in verdicts_of(sink):
            p = v.payload
            blob, _ = sink.blobs[p.key]
            chunk = blob[p.offset:p.offset + p.length]
            self.assertEqual(chunk, payload_of(v.frame_id))
            self.assertEqual(hashlib.sha256(chunk).hexdigest(), p.sha256)

    def test_bundles_hold_at_most_35_and_all_are_put(self):
        r, sink, _, _ = run(n=1000, every=2)          # 500 선별 → 15 번들
        self.assertEqual(r.bundles, -(-r.accepted // RP.BUNDLE_FRAMES))
        self.assertEqual(len(sink.blobs), r.bundles)
        for _, meta in sink.blobs.values():
            self.assertLessEqual(int(meta["n-frames"]), RP.BUNDLE_FRAMES)
        self.assertEqual(sum(len(b) for b, _ in sink.blobs.values()), r.bytes_uploaded)

    def test_bundle_keys_are_prefixed_by_vin(self):
        """차량 격리 — 두 팔이 물리적으로 다른 접두사에 쌓인다 (구현설계 §4)."""
        _, sink, _, _ = run(arm="STA_B")
        self.assertTrue(all(k.startswith("vin=sim-STA_B/boot=boot-test/") for k in sink.blobs))


class TestContract(unittest.TestCase):
    def test_full_queue_drops_immediately_and_reports_in_telemetry(self):
        """용량을 넘으면 기다리지 않고 버린다. 드롭은 판정이 아니라 텔레메트리로 보고한다."""
        r, sink, sel, _ = run(n=200, every=1, queue_capacity=40_000)
        self.assertGreater(r.dropped, 0)
        self.assertEqual(r.accepted + r.dropped, len(sel))
        reported = [u for t in telemetry_of(sink) for u in t.dropped_frame_uuids]
        self.assertEqual(len(reported), r.dropped)
        self.assertFalse(set(reported) & {v.frame_uuid for v in verdicts_of(sink)})

    def test_park_phase_drains_queue(self):
        """주행이 끝나도 큐가 남는다(수요 > 링크). 주차 중 전부 비워야 한다."""
        r, sink, _, _ = run(n=400, every=1)
        self.assertGreater(r.t_park_s, 0)
        tel = telemetry_of(sink)
        self.assertTrue(tel[-1].final)
        self.assertEqual(tel[-1].queue_bytes, 0)
        self.assertEqual(tel[-1].phase, "park")

    def test_latency_is_measured_at_bundle_put(self):
        r, _, _, _ = run(n=400, every=1)
        self.assertGreaterEqual(r.max_latency_bundle_s, r.max_latency_frame_s)
        self.assertTrue(r.latency_within_contract)

    def test_verdict_batches_are_one_second_windows(self):
        _, sink, _, _ = run(n=400, every=1)
        sent = [json.loads(b)["sent_at"] for t, b in sink.published if t.endswith("/verdict")]
        self.assertEqual(sent, sorted(sent))
        self.assertEqual(len(sent), len(set(sent)))
        # 20 Hz × 1 s = 배치당 최대 20 판정
        for t, b in sink.published:
            if t.endswith("/verdict"):
                self.assertLessEqual(len(json.loads(b)["verdicts"]), 20)


class TestPacing(unittest.TestCase):
    def test_speed_changes_wall_time_only(self):
        """가속 배수는 벽시계만 바꾼다. 큐 거동은 모의 시간이라 같아야 한다."""
        def run_with(speed):
            now = [0.0]
            sig = make_signals("DYN_B", 200)
            rp = RP.Replayer(
                RP.ReplayConfig(arm="DYN_B", boot_id="b", t0_utc=T0, speed=speed),
                C, sig, set(sig.pool[::5]), record_of, payload_of, RP.NullSink(),
                clock=lambda: now[0], sleep=lambda d: now.__setitem__(0, now[0] + d))
            return rp.run()
        a, b = run_with(1.0), run_with(10.0)
        self.assertEqual((a.accepted, a.bundles, a.peak_queue_bytes, a.max_latency_bundle_s),
                         (b.accepted, b.bundles, b.peak_queue_bytes, b.max_latency_bundle_s))
        total = a.t_drive_s + a.t_park_s
        self.assertAlmostEqual(a.wall_s, total, delta=0.1)
        self.assertAlmostEqual(b.wall_s, total / 10.0, delta=0.1)

    def test_rejects_speed_above_cap(self):
        sig = make_signals("DYN_B", 10)
        with self.assertRaises(ValueError):
            RP.Replayer(RP.ReplayConfig(arm="DYN_B", boot_id="b", t0_utc=T0, speed=13.0),
                        C, sig, set(), record_of, payload_of, RP.NullSink())

    def test_rejects_selection_outside_pool(self):
        sig = make_signals("DYN_B", 10)
        with self.assertRaises(ValueError):
            RP.Replayer(RP.ReplayConfig(arm="DYN_B", boot_id="b", t0_utc=T0),
                        C, sig, {5}, record_of, payload_of, RP.NullSink())


if __name__ == "__main__":
    unittest.main()
