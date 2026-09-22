# -*- coding: utf-8 -*-
"""수집기가 재생기의 출력을 계약대로 적재하는지 검사한다 (stdlib unittest).

입력은 재생기의 실제 출력(합성 프레임 + MemorySink — test_replay 와 같은 조립)이고,
저장소는 MemoryTable 이다. 스택이 필요 없다. 같은 Store 를 DynamoDB Local 에 돌리는 것은
test_store_dynamo.py 다.

수용 기준 (구현설계_MVP.md §6)
    5  멱등성 — 같은 배치 3회 → 아이템 수 불변
    6  스큐   — 페이로드가 판정보다 늦게 / 먼저 / 아예 안 오는 3경우

    python -m unittest discover -s tests
"""
import json
import os
import sys
import unittest

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "ingest"))
sys.path.insert(0, os.path.join(_R, "schema"))

import collector as CO              # noqa: E402
import store as S                   # noqa: E402
import test_replay as TR            # noqa: E402
import verdict as V                 # noqa: E402

NOW = "2026-09-22T12:00:00.000Z"
BUCKET = "ns-raw-payload"            # 재생기 ReplayConfig 기본값과 같다
PENDING, UPLOADED, DROPPED = (V.FrameState.PENDING.value, V.FrameState.UPLOADED.value,
                              V.FrameState.DROPPED.value)


def fresh():
    t = S.MemoryTable()
    st = S.Store(t)
    return t, st, CO.Collector(st, BUCKET, now=lambda: NOW)


def feed(col, sink, kinds=("verdict", "telemetry")):
    for topic, body in sink.published:
        if topic.rsplit("/", 1)[1] in kinds:
            col.handle(topic, body.encode("utf-8"))


def frames(t):
    return {k[0]: v for k, v in t.items.items() if k[0].startswith("FRAME#")}


def bundles(t):
    return {v["key"]: v for k, v in t.items.items() if k[0].startswith("BUNDLE#")}


def arrive_all(st, sink):
    """조정기가 할 일(번들 이벤트 → 도착 기록 → 승격)을 Store 로 흉내 낸다."""
    moved = 0
    for key, (_, meta) in sink.blobs.items():
        bd = st.bundle_mark_arrived(key, "ns-raw-payload", {
            "vin": meta["vin"], "t_put_utc": meta["t-put-utc"],
            "n_frames": int(meta["n-frames"])}, NOW)
        moved += st.promote_uploaded(bd, bd.get("frame_uuids", set()), NOW)
    return moved


class TestLanding(unittest.TestCase):
    def test_every_verdict_lands_pending_under_its_vin(self):
        r, sink, _, _ = TR.run(arm="DYN_B")
        t, _, col = fresh()
        feed(col, sink)
        fr = frames(t)
        self.assertEqual(len(fr), r.accepted)
        for it in fr.values():
            self.assertEqual(it["state"], PENDING)
            self.assertEqual(it["GSI1PK"], "VIN#sim-DYN_B#STATE#PENDING")
            self.assertEqual(it["PK"], "FRAME#" + it["frame_uuid"])
            self.assertEqual(it["t_ingest_utc"], NOW)
        self.assertEqual(col.n["verdict_new"], r.accepted)

    def test_bundle_items_list_exactly_their_frames(self):
        """번들 → 판정 목록이 번들 객체의 프레임 수와 맞는다 (조정 단위)."""
        r, sink, _, _ = TR.run(n=1000, every=2)
        t, _, col = fresh()
        feed(col, sink)
        bd = bundles(t)
        self.assertEqual(set(bd), set(sink.blobs))
        for key, item in bd.items():
            self.assertEqual(len(item["frame_uuids"]), int(sink.blobs[key][1]["n-frames"]))
            self.assertNotIn("GSI1PK", item)          # 희소 인덱스 — GSI1 에 들어가지 않는다
        self.assertEqual(sum(len(i["frame_uuids"]) for i in bd.values()), r.accepted)

    def test_telemetry_stored_per_vin(self):
        r, sink, _, _ = TR.run(arm="STA_B")
        t, _, col = fresh()
        feed(col, sink)
        tel = [v for k, v in t.items.items() if k[0] == "VIN#sim-STA_B"]
        self.assertEqual(len(tel), r.telemetry_messages)
        self.assertTrue(all(k[1].startswith("TEL#") for k in t.items if k[0] == "VIN#sim-STA_B"))
        self.assertEqual(sum(1 for x in tel if x["final"]), 1)


class TestIdempotency(unittest.TestCase):
    def test_three_deliveries_leave_item_count_unchanged(self):
        """수용 기준 5. at-least-once 재전송을 조건부 쓰기와 집합 ADD 가 흡수한다."""
        r, sink, _, _ = TR.run(n=200, every=1, queue_capacity=40_000)   # 드롭 포함
        t, _, col = fresh()
        feed(col, sink)
        snapshot = {k: dict(v) for k, v in t.items.items()}
        feed(col, sink)
        feed(col, sink)
        self.assertEqual(set(t.items), set(snapshot))
        for k, v in t.items.items():
            self.assertEqual(v, snapshot[k], k)
        self.assertEqual(col.n["verdict_new"], r.accepted)
        self.assertEqual(col.n["verdict_dup"], 2 * r.accepted)
        self.assertEqual(col.n["dropped_new"], r.dropped)


class TestSkew(unittest.TestCase):
    """수용 기준 6. 어느 순서로 와도 상태기계가 수용한다."""

    def test_payload_after_verdicts(self):
        r, sink, _, _ = TR.run()
        t, st, col = fresh()
        feed(col, sink)
        self.assertEqual(arrive_all(st, sink), r.accepted)
        for it in frames(t).values():
            self.assertEqual(it["state"], UPLOADED)
            self.assertEqual(it["GSI1PK"], "VIN#sim-DYN_B#STATE#UPLOADED")
            self.assertIn("t_put_utc", it)
            self.assertEqual(it["t_uploaded_utc"], NOW)

    def test_payload_before_verdicts(self):
        r, sink, _, _ = TR.run()
        t, st, col = fresh()
        self.assertEqual(arrive_all(st, sink), 0)          # 아직 판정이 없다
        feed(col, sink)
        self.assertEqual(col.n["uploaded_on_verdict"], r.accepted)
        self.assertTrue(all(it["state"] == UPLOADED for it in frames(t).values()))

    def test_payload_never_arrives(self):
        """PENDING 은 정상 상태다 — 번들이 안 와도 판정은 그대로 남는다."""
        r, sink, _, _ = TR.run()
        t, _, col = fresh()
        feed(col, sink)
        self.assertTrue(all(it["state"] == PENDING for it in frames(t).values()))
        self.assertTrue(all("t_arrived_utc" not in b for b in bundles(t).values()))

    def test_promotion_happens_once(self):
        r, sink, _, _ = TR.run()
        _, st, col = fresh()
        feed(col, sink)
        self.assertEqual(arrive_all(st, sink), r.accepted)
        self.assertEqual(arrive_all(st, sink), 0)          # 재전송된 이벤트
        feed(col, sink)                                    # 재전송된 판정
        self.assertEqual(col.n["uploaded_on_verdict"], 0)

    def test_redelivered_arrival_keeps_first_time(self):
        _, st, _ = fresh()
        st.bundle_mark_arrived("k", "b", {"vin": "sim-DYN_B"}, "T1")
        bd = st.bundle_mark_arrived("k", "b", {"vin": "sim-DYN_B"}, "T2")
        self.assertEqual(bd["t_arrived_utc"], "T1")


class TestDrops(unittest.TestCase):
    def test_dropped_frames_become_dropped_items(self):
        r, sink, _, _ = TR.run(n=200, every=1, queue_capacity=40_000)
        self.assertGreater(r.dropped, 0)
        t, _, col = fresh()
        feed(col, sink)
        dr = [v for v in frames(t).values() if v["state"] == DROPPED]
        self.assertEqual(len(dr), r.dropped)
        self.assertTrue(all(v["GSI1PK"] == "VIN#sim-DYN_B#STATE#DROPPED" for v in dr))
        self.assertEqual(len(frames(t)), r.accepted + r.dropped)

    def test_dropped_cannot_be_promoted(self):
        _, st, _ = fresh()
        st.put_dropped("u1", "sim-DYN_B", "b", 1, NOW)
        n = st.promote_uploaded({"vin": "sim-DYN_B", "t_put_utc": NOW}, ["u1"], NOW)
        self.assertEqual(n, 0)
        self.assertEqual(st.get_frame("u1")["state"], DROPPED)


class TestRejects(unittest.TestCase):
    def setUp(self):
        _, self.sink, _, _ = TR.run()
        self.topic, self.body = next((t, b) for t, b in self.sink.published
                                     if t.endswith("/verdict"))
        self.t, _, self.col = fresh()

    def test_topic_vin_must_match_body(self):
        """M4 격리 — 정책이 지키는 것은 토픽이다. 본문 vin 을 믿지 않는다."""
        with self.assertRaises(CO.Rejected):
            self.col.handle(V.topic_verdict("sim-STA_B"), self.body.encode())
        self.assertEqual(self.t.items, {})

    def test_verdict_vin_must_match_envelope(self):
        d = json.loads(self.body)
        d["verdicts"][0]["vin"] = "sim-STA_B"
        with self.assertRaises(CO.Rejected):
            self.col.handle(self.topic, json.dumps(d).encode())
        self.assertEqual(self.t.items, {})

    def test_malformed_and_unknown(self):
        for topic, body in [(self.topic, b"{not json"),
                            (self.topic, b'{"schema_version": 2, "vin": "sim-DYN_B"}'),
                            ("ns/v1/sim-DYN_B/curation/other", self.body.encode()),
                            ("elsewhere", self.body.encode())]:
            with self.subTest(topic=topic, body=body[:20]):
                with self.assertRaises(CO.Rejected):
                    self.col.handle(topic, body)

    def test_foreign_bucket_rejected(self):
        """점검용 임시 버킷을 가리키는 판정이 운영 테이블에 들어오지 않는다."""
        d = json.loads(self.body)
        d["verdicts"][0]["payload"]["bucket"] = "ns-live-check"
        with self.assertRaises(CO.Rejected):
            self.col.handle(self.topic, json.dumps(d).encode())
        self.assertEqual(self.t.items, {})

    def test_unselected_verdict_rejected(self):
        d = json.loads(self.body)
        d["verdicts"][0]["selected"] = False
        d["verdicts"][0]["payload"] = None
        with self.assertRaises(CO.Rejected):
            self.col.handle(self.topic, json.dumps(d).encode())

    def test_backward_transition_refused(self):
        _, st, _ = fresh()
        with self.assertRaises(ValueError):
            st.transition("u", "sim-DYN_B", V.FrameState.UPLOADED, V.FrameState.PENDING, NOW)


if __name__ == "__main__":
    unittest.main()
