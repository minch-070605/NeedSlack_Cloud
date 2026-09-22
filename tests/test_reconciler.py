# -*- coding: utf-8 -*-
"""조정기가 번들 도착을 받아 PENDING → UPLOADED → INDEXED 로 옮기는지 검사한다 (stdlib unittest).

재생기 출력(MemorySink) → 수집기 → 조정기를 MemoryTable 위에서 잇는다. 스택이 필요 없다.
S3 이벤트는 MinIO 가 실제로 보내는 모양(키 URL 인코딩 · `X-Amz-Meta-*`)으로 만든다 —
구현설계_MVP.md §8 에 적은, 이 스택에서 받은 이벤트 그대로다.

수용 기준 6 (스큐) 의 세 경우를 조정기까지 포함해 끝까지 본다.

    python -m unittest discover -s tests
"""
import json
import os
import sys
import unittest
import urllib.parse

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "ingest"))
sys.path.insert(0, os.path.join(_R, "schema"))

import reconciler as RC             # noqa: E402
import store as S                   # noqa: E402
import test_collector as TC         # noqa: E402
import test_replay as TR            # noqa: E402
import verdict as V                 # noqa: E402

BUCKET = "ns-raw-payload"
INDEXED, QUARANTINED = V.FrameState.INDEXED.value, V.FrameState.QUARANTINED.value


class MemoryBlobs:
    """MemorySink 가 받은 번들을 S3Blobs 처럼 내준다."""

    def __init__(self, sink):
        self.objs = {k: (bytearray(b), m) for k, (b, m) in sink.blobs.items()}

    def get(self, bucket, key):
        return bytes(self.objs[key][0])

    def head(self, bucket, key):
        b, m = self.objs[key]
        return {"size": len(b), "etag": "e", "meta": dict(m)}

    def keys(self, bucket):
        return list(self.objs)


def s3_event(key, blob, meta, name="s3:ObjectCreated:Put"):
    """MinIO notify_mqtt 가 보내는 본문 모양."""
    return json.dumps({"EventName": name, "Key": f"{BUCKET}/{key}", "Records": [{
        "eventName": name,
        "s3": {"bucket": {"name": BUCKET},
               "object": {"key": urllib.parse.quote(key, safe=""), "size": len(blob), "eTag": "e",
                          "userMetadata": {**{f"X-Amz-Meta-{k.title()}": v for k, v in meta.items()},
                                           "content-type": "binary/octet-stream"}}}}]}).encode()


def setup(**run_kw):
    r, sink, _, _ = TR.run(**run_kw)
    t, st, col = TC.fresh()
    blobs = MemoryBlobs(sink)
    rc = RC.Reconciler(st, blobs, BUCKET, [V.vin_of(a) for a in V.ARMS], now=lambda: TC.NOW)
    return r, sink, t, st, col, rc, blobs


def events(rc, sink):
    for key, (blob, meta) in sink.blobs.items():
        rc.handle_event(s3_event(key, blob, meta))


def states(t):
    out = {}
    for it in TC.frames(t).values():
        out[it["state"]] = out.get(it["state"], 0) + 1
    return out


class TestFlow(unittest.TestCase):
    def test_payload_after_verdicts_ends_indexed(self):
        r, sink, t, _, col, rc, _ = setup()
        TC.feed(col, sink)
        events(rc, sink)
        self.assertEqual(states(t), {INDEXED: r.accepted})
        for it in TC.frames(t).values():
            self.assertEqual(it["GSI1PK"], "VIN#sim-DYN_B#STATE#INDEXED")
        self.assertEqual(rc.n["indexed"], r.accepted)

    def test_arrival_latency_matches_replayer(self):
        """차량 시계로 잰 도착 지연의 최대가 재생기 보고(번들 PUT 기준)와 같다."""
        r, sink, t, _, col, rc, _ = setup(n=1000, every=1)       # 큐가 쌓여 지연이 생긴다
        TC.feed(col, sink)
        events(rc, sink)
        lat = [it["arrival_latency_s"] for it in TC.frames(t).values() if it["state"] == INDEXED]
        self.assertEqual(len(lat), r.accepted)
        self.assertTrue(all(x >= 0 for x in lat))
        self.assertAlmostEqual(max(lat), r.max_latency_bundle_s, delta=0.002)   # ISO ms 반올림

    def test_bundle_arrival_recorded_from_metadata(self):
        _, sink, t, _, col, rc, _ = setup()
        TC.feed(col, sink)
        events(rc, sink)
        for key, (blob, meta) in sink.blobs.items():
            bd = TC.bundles(t)[key]
            self.assertEqual(bd["t_put_utc"], meta["t-put-utc"])
            self.assertEqual(bd["n_frames"], int(meta["n-frames"]))
            self.assertEqual(bd["size"], len(blob))
            self.assertEqual(bd["t_arrived_utc"], TC.NOW)
            self.assertEqual(bd["boot_id"], "boot-test")


class TestSkew(unittest.TestCase):
    """수용 기준 6 — 조정기까지 포함한 세 경우."""

    def test_payload_before_verdicts(self):
        """번들 먼저 → 수집기가 UPLOADED 로 옮기고 → 스윕이 INDEXED 로."""
        r, sink, t, _, col, rc, _ = setup()
        events(rc, sink)
        self.assertEqual(states(t), {})                       # 판정이 아직 없다
        TC.feed(col, sink)
        self.assertEqual(states(t), {V.FrameState.UPLOADED.value: r.accepted})
        self.assertEqual(rc.sweep_uploaded(), r.accepted)
        self.assertEqual(states(t), {INDEXED: r.accepted})

    def test_payload_never_arrives(self):
        """버킷이 비어 있으면 스윕도 대조도 할 일이 없다 — PENDING 은 정상 상태다."""
        r, sink, t, _, col, rc, blobs = setup()
        blobs.objs.clear()
        TC.feed(col, sink)
        self.assertEqual(rc.sweep_uploaded(), 0)
        self.assertEqual(rc.scan_bucket(), 0)
        self.assertEqual(states(t), {V.FrameState.PENDING.value: r.accepted})

    def test_lost_events_recovered_by_bucket_scan(self):
        r, sink, t, _, col, rc, _ = setup()
        TC.feed(col, sink)
        self.assertEqual(rc.scan_bucket(), r.bundles)
        self.assertEqual(states(t), {INDEXED: r.accepted})
        self.assertEqual(rc.scan_bucket(), 0)                 # 이미 도착 기록이 있다


class TestIdempotency(unittest.TestCase):
    def test_redelivered_events_change_nothing(self):
        _, sink, t, _, col, rc, _ = setup()
        TC.feed(col, sink)
        events(rc, sink)
        snap = {k: dict(v) for k, v in t.items.items()}
        events(rc, sink)
        events(rc, sink)
        rc.sweep_uploaded()
        rc.scan_bucket()
        self.assertEqual(t.items, snap)


class TestQuarantine(unittest.TestCase):
    def test_corrupted_frame_is_quarantined_others_indexed(self):
        r, sink, t, st, col, rc, blobs = setup()
        TC.feed(col, sink)
        victim = next(it for it in TC.frames(t).values())
        p = victim["payload"]
        blobs.objs[p["key"]][0][p["offset"]] ^= 0xFF              # 1 바이트 변조
        for key, (_, meta) in blobs.objs.items():
            rc.handle_event(s3_event(key, bytes(blobs.objs[key][0]), meta))
        self.assertEqual(states(t), {INDEXED: r.accepted - 1, QUARANTINED: 1})
        q = st.get_frame(victim["frame_uuid"])
        self.assertEqual(q["quarantine_reason"], "sha256 불일치")
        self.assertEqual(q["GSI1PK"], "VIN#sim-DYN_B#STATE#QUARANTINED")

    def test_truncated_bundle_quarantines_out_of_range(self):
        r, sink, t, _, col, rc, blobs = setup()
        TC.feed(col, sink)
        key = next(iter(blobs.objs))
        del blobs.objs[key][0][-10:]
        for k, (_, meta) in blobs.objs.items():
            rc.handle_event(s3_event(k, bytes(blobs.objs[k][0]), meta))
        self.assertEqual(states(t)[QUARANTINED], 1)


class TestRejects(unittest.TestCase):
    def setUp(self):
        _, self.sink, self.t, _, self.col, self.rc, _ = setup()
        self.key, (self.blob, self.meta) = next(iter(self.sink.blobs.items()))

    def test_metadata_vin_must_match_key_prefix(self):
        """M4 격리 — S3 접두사 스코핑이 지키는 것은 키다. 메타데이터 vin 을 믿지 않는다."""
        with self.assertRaises(RC.Rejected):
            self.rc.handle_event(s3_event(self.key, self.blob, {**self.meta, "vin": "sim-STA_B"}))
        self.assertEqual(self.t.items, {})

    def test_non_bundle_keys_and_foreign_buckets(self):
        with self.assertRaises(RC.Rejected):
            self.rc.handle_event(s3_event("_probe/x.bin", b"x", self.meta))
        other = json.loads(s3_event(self.key, self.blob, self.meta))
        other["Records"][0]["s3"]["bucket"]["name"] = "elsewhere"
        with self.assertRaises(RC.Rejected):
            self.rc.handle_event(json.dumps(other).encode())

    def test_malformed(self):
        for body in (b"{not json", b'{"Records": [{"s3": {}}]}', b'{"no": "records"}'):
            with self.subTest(body=body):
                with self.assertRaises(RC.Rejected):
                    self.rc.handle_event(body)

    def test_non_create_events_ignored(self):
        self.rc.handle_event(s3_event(self.key, self.blob, self.meta, name="s3:ObjectRemoved:Delete"))
        self.assertEqual(self.rc.n["event_ignored"], 1)
        self.assertEqual(self.t.items, {})

    def test_url_encoded_key_is_decoded(self):
        self.assertIn("%3D", json.loads(s3_event(self.key, self.blob, self.meta))
                      ["Records"][0]["s3"]["object"]["key"])
        TC.feed(self.col, self.sink)
        self.rc.handle_event(s3_event(self.key, self.blob, self.meta))
        self.assertIn(self.key, TC.bundles(self.t))
        self.assertIn("t_arrived_utc", TC.bundles(self.t)[self.key])

    def test_metadata_forms_agree(self):
        """이벤트(X-Amz-Meta-T-Put-Utc)와 head_object(t-put-utc)가 같은 모양이 된다."""
        self.assertEqual(RC.normalize_meta({"X-Amz-Meta-T-Put-Utc": "a", "X-Amz-Meta-Vin": "v",
                                            "content-type": "x"}),
                         RC.normalize_meta({"t-put-utc": "a", "vin": "v"}))


if __name__ == "__main__":
    unittest.main()
