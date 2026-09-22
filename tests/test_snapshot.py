# -*- coding: utf-8 -*-
"""M2 스냅샷의 검증·추출·tar 가 계약대로인지 검사한다 (stdlib unittest, 스택 불필요).

입력은 재생기 → 수집기 → 조정기를 MemoryTable 위에서 끝까지 돌린 INDEXED 프레임이다.
rev.9 선별·split 은 합성한다 — 재생기 테스트와 같은 pool(100…) 위에서.

수용 기준 (구현설계_MVP.md §6)
    3  파이프라인이 대조를 왜곡 안 함 — INDEXED 집합 = rev.9 선별 (데이터 쪽)
    7  평가셋 무오염 — 학습 ∩ held-out = ∅

    python -m unittest discover -s tests
"""
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "pipelines"))
sys.path.insert(0, os.path.join(_R, "ingest"))
sys.path.insert(0, os.path.join(_R, "schema"))

import snapshot as SN               # noqa: E402
import test_collector as TC         # noqa: E402
import test_reconciler as TRC       # noqa: E402
import test_replay as TR            # noqa: E402
import verdict as V                 # noqa: E402

PROFILE = "f" * 16                  # test_replay.make_signals 의 profile sha


def indexed(arm="DYN_B", **kw):
    """재생 → 수집 → 조정까지 돌려 (INDEXED 프레임, 선별 집합, 번들 저장소, store) 를 돌려준다."""
    r, sink, t, st, col, rc, blobs = TRC.setup(arm=arm, **kw)
    TC.feed(col, sink)
    TRC.events(rc, sink)
    frames = st.frames_in_state(V.vin_of(arm), V.FrameState.INDEXED)
    selected = {f["frame_id"] for f in frames}
    return frames, selected, blobs, st, r


def splits_for(frames, extra_held=(), extra_tune=()):
    sp = {f["frame_id"]: "pool" for f in frames}
    for fid in extra_held:
        sp[fid] = "held_out"
    for fid in extra_tune:
        sp[fid] = "tune"
    return sp


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.frames, self.sel, _, _, self.r = indexed()
        self.sp = splits_for(self.frames, extra_held=[5, 6], extra_tune=[1, 2])

    def test_rows_sorted_and_complete(self):
        rows = SN.plan("DYN_B", self.frames, self.sel, self.sp, sorted(self.sel), PROFILE)
        self.assertEqual(len(rows), self.r.accepted)
        self.assertEqual([r["frame_id"] for r in rows], sorted(self.sel))
        self.assertTrue(all(r["split"] == "pool" and r["image"].startswith("images/train/") for r in rows))

    def test_selection_mismatch_stops(self):
        """수용 기준 3 — 한 장이라도 다르면 멈춘다."""
        for sel in (self.sel - {min(self.sel)}, self.sel | {99999}):
            with self.subTest(n=len(sel)):
                with self.assertRaises(SN.SnapshotError):
                    SN.plan("DYN_B", self.frames, sel, self.sp, None, PROFILE)

    def test_trained_list_mismatch_stops(self):
        with self.assertRaises(SN.SnapshotError):
            SN.plan("DYN_B", self.frames, self.sel, self.sp, sorted(self.sel)[1:], PROFILE)

    def test_held_out_overlap_stops(self):
        """수용 기준 7 — 학습 ∩ held-out = ∅."""
        sp = dict(self.sp)
        sp[min(self.sel)] = "held_out"
        with self.assertRaises(SN.SnapshotError):
            SN.plan("DYN_B", self.frames, self.sel, sp, None, PROFILE)

    def test_outside_pool_stops(self):
        sp = dict(self.sp)
        sp[min(self.sel)] = "tune"
        with self.assertRaises(SN.SnapshotError):
            SN.plan("DYN_B", self.frames, self.sel, sp, None, PROFILE)

    def test_mixed_mechanism_stops(self):
        """설계결정-0001 §4 — 기전이 다른 프레임을 한 스냅샷에 섞지 않는다."""
        frames = [dict(f) for f in self.frames]
        frames[0]["reclaim_mode"] = "admission_nonpreempt"
        with self.assertRaises(SN.SnapshotError):
            SN.plan("DYN_B", frames, self.sel, self.sp, None, PROFILE)

    def test_profile_mismatch_stops(self):
        with self.assertRaises(SN.SnapshotError):
            SN.plan("DYN_B", self.frames, self.sel, self.sp, None, "0" * 16)

    def test_duplicate_frame_stops(self):
        with self.assertRaises(SN.SnapshotError):
            SN.plan("DYN_B", self.frames + self.frames[:1], self.sel, self.sp, None, PROFILE)


class TestExtract(unittest.TestCase):
    def setUp(self):
        frames, sel, self.blobs, _, _ = indexed()
        self.rows = SN.plan("DYN_B", frames, sel, splits_for(frames), None, PROFILE)
        self.gets = []

    def get(self, b, k):
        self.gets.append(k)
        return self.blobs.get(b, k)

    def test_bytes_equal_original_and_each_bundle_read_once(self):
        imgs = SN.extract(self.rows, self.get, TR.payload_of)
        self.assertEqual(set(imgs), {r["frame_id"] for r in self.rows})
        self.assertTrue(all(imgs[f] == TR.payload_of(f) for f in imgs))
        self.assertEqual(len(self.gets), len(set(self.gets)))

    def test_tampered_bundle_stops(self):
        r = self.rows[0]
        self.blobs.objs[r["key"]][0][r["offset"]] ^= 0xFF
        with self.assertRaises(SN.SnapshotError):
            SN.extract(self.rows, self.get)

    def test_original_mismatch_stops(self):
        with self.assertRaises(SN.SnapshotError):
            SN.extract(self.rows, self.get, lambda fid: b"not the same")


class TestTar(unittest.TestCase):
    def setUp(self):
        frames, sel, blobs, _, _ = indexed()
        self.rows = SN.plan("DYN_B", frames, sel, splits_for(frames), None, PROFILE)
        self.imgs = SN.extract(self.rows, blobs.get)
        self.tmp = tempfile.mkdtemp()
        self.label = lambda fid: f"0 0.5 0.5 0.1 0.1  # {fid}\n".encode()

    def build(self, name):
        return SN.write_arm_tar(os.path.join(self.tmp, name), "DYN_B", self.rows, self.imgs,
                                self.label, {"split_seed": 1})

    def test_deterministic(self):
        """같은 입력 → 같은 바이트. 동결의 전제다."""
        a, _ = self.build("a.tar")
        b, _ = self.build("b.tar")
        self.assertEqual(a, b)

    def test_layout_and_manifest(self):
        sha, snap = self.build("x.tar")
        with tarfile.open(os.path.join(self.tmp, "x.tar")) as t:
            names = t.getnames()
            man = t.extractfile("ds-DYN_B/manifest.jsonl").read()
            lst = t.extractfile("ds-DYN_B/lists/train.txt").read().decode().split()
            img0 = t.extractfile(f"ds-DYN_B/{self.rows[0]['image']}").read()
        n = len(self.rows)
        self.assertEqual(sum(x.startswith("ds-DYN_B/images/train/") for x in names), n)
        self.assertEqual(sum(x.startswith("ds-DYN_B/labels/train/") for x in names), n)
        self.assertEqual(len(man.decode().splitlines()), n)
        self.assertEqual(snap["manifest_sha256"], SN.sha256(man))
        self.assertEqual(lst, [r["image"] for r in self.rows])
        self.assertEqual(img0, self.imgs[self.rows[0]["frame_id"]])
        self.assertEqual(snap["n_frames"], n)

    def test_common_tar_and_eval_sets(self):
        splits = {i: "pool" for i in range(10)}
        splits.update({10: "tune", 11: "held_out", 12: "tune", 13: "held_out", 14: "tune"})
        val, held = SN.eval_sets(splits, [14, 10, 12])      # 파일 순서를 지킨다
        self.assertEqual((val, held), ([14, 10, 12], [11, 13]))
        img = lambda fid: bytes([fid]) * 10                  # noqa: E731
        p = os.path.join(self.tmp, "c.tar")
        sha, info = SN.write_common_tar(p, val, held, img, self.label, b"weights", {})
        sha2, _ = SN.write_common_tar(p + "2", val, held, img, self.label, b"weights", {})
        self.assertEqual(sha, sha2)
        self.assertEqual((info["n_val"], info["n_held_out"]), (3, 2))
        with tarfile.open(p) as t:
            self.assertEqual(t.extractfile("eval-common/lists/held_out.txt").read().decode().split(),
                             ["images/held_out/00011.jpg", "images/held_out/00013.jpg"])
            self.assertEqual(t.extractfile("eval-common/weights/ybase_last.pt").read(), b"weights")


class TestInDataset(unittest.TestCase):
    def test_indexed_to_in_dataset_once(self):
        frames, sel, _, st, _ = indexed()
        rows = SN.plan("DYN_B", frames, sel, splits_for(frames), None, PROFILE)
        self.assertEqual(SN.mark_in_dataset(st, "DYN_B", rows, TC.NOW), len(rows))
        self.assertEqual(SN.mark_in_dataset(st, "DYN_B", rows, TC.NOW), 0)
        moved = st.frames_in_state("sim-DYN_B", V.FrameState.IN_DATASET)
        self.assertEqual(len(moved), len(rows))
        self.assertTrue(all(f["ds_id"] == "ds-DYN_B" for f in moved))
        # 다시 돌려도 plan 이 받아들인다 — INDEXED + IN_DATASET 를 함께 읽기 때문
        again = SN.plan("DYN_B", moved, sel, splits_for(moved), None, PROFILE)
        self.assertEqual([r["frame_uuid"] for r in again], [r["frame_uuid"] for r in rows])


if __name__ == "__main__":
    unittest.main()
