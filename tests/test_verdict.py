# -*- coding: utf-8 -*-
"""S1 와이어 계약 검사.

표준 라이브러리 `unittest` 만 쓴다 — pytest 가 아직 승인되지 않았고, 계약 검사는
의존성 없이 돌 수 있어야 한다.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "schema"))
import verdict as V  # noqa: E402


def make(**kw):
    base = dict(
        vin=V.vin_of("DYN_B"), boot_id="boot-abc", drive_session=412,
        frame_id=1048322, t_frame_mono_ms=30294811,
        t_frame_utc="2026-09-21T14:03:09.881Z",
        conf_q8=43, mean_p=96, kinematic_u8=211, det_count=2,
        value_score=231, tile_mask=0b01000010, tiles_scored=2,
        reclaim_mode=V.ReclaimMode.PREEMPT_RESUME, rtl_build="rev10-dyn",
        profile_sha="b4d89de1a81539e8",
    )
    base.update(kw)
    return V.Verdict(**base)


class TestRunModel(unittest.TestCase):
    def test_run_id_and_vin(self):
        self.assertEqual(V.run_id("DYN_B", 1), "DYN_B-s1")
        self.assertEqual(V.dataset_id("DYN_B"), "ds-DYN_B")

    def test_vin_has_no_seed(self):
        """선별이 시드 독립이므로 재생은 팔당 1회. vin 에 시드가 들어가면 안 된다."""
        self.assertEqual(V.vin_of("STA_B"), "sim-STA_B")
        self.assertEqual(len({V.vin_of(a) for a in V.ARMS}), 2)

    def test_rejects_unknown_arm_or_seed(self):
        with self.assertRaises(ValueError):
            V.run_id("DYN_A", 1)          # 축 2 의 다른 조건. 이 프로젝트 범위 밖
        with self.assertRaises(ValueError):
            V.run_id("DYN_B", 4)

    def test_six_training_runs_two_datasets(self):
        """학습 6런 · 데이터셋 2개. 시드는 학습에만 작용한다."""
        self.assertEqual(len({V.run_id(a, s) for a in V.ARMS for s in V.SEEDS}), 6)
        self.assertEqual(len({V.dataset_id(a) for a in V.ARMS}), 2)

    def test_selection_filename_matches_team(self):
        self.assertEqual(V.selection_filename("STA_B", 3),
                         "STA_B_mc_rate0.10_s3.npy")


class TestFrameUuid(unittest.TestCase):
    def test_is_128_bit_hex(self):
        u = V.frame_uuid("sim-DYN_B-s1", "boot-abc", 412, 1)
        self.assertEqual(len(u), 32)
        int(u, 16)

    def test_idempotent(self):
        a = V.frame_uuid("sim-DYN_B-s1", "boot-abc", 412, 1)
        b = V.frame_uuid("sim-DYN_B-s1", "boot-abc", 412, 1)
        self.assertEqual(a, b)

    def test_frame_id_alone_is_not_unique(self):
        """frame_id 는 u32 로컬 인덱스다. 세션·부팅·차량이 달라지면 달라져야 한다."""
        base = V.frame_uuid("sim-DYN_B-s1", "boot-a", 1, 42)
        self.assertNotEqual(base, V.frame_uuid("sim-STA_B-s1", "boot-a", 1, 42))
        self.assertNotEqual(base, V.frame_uuid("sim-DYN_B-s1", "boot-b", 1, 42))
        self.assertNotEqual(base, V.frame_uuid("sim-DYN_B-s1", "boot-a", 2, 42))

    def test_no_separator_collision(self):
        """구분자 없이 이어붙이면 ('a','bc') 와 ('ab','c') 가 충돌한다."""
        self.assertNotEqual(V.frame_uuid("a", "bc", 1, 1),
                            V.frame_uuid("ab", "c", 1, 1))


class TestStateMachine(unittest.TestCase):
    def test_forward_transitions(self):
        S = V.FrameState
        self.assertTrue(V.can_transition(S.PENDING, S.UPLOADED))
        self.assertTrue(V.can_transition(S.PENDING, S.DROPPED))
        self.assertTrue(V.can_transition(S.UPLOADED, S.INDEXED))
        self.assertTrue(V.can_transition(S.INDEXED, S.IN_DATASET))

    def test_is_monotonic(self):
        """역순 도착은 UPSERT 로 흡수하되, 상태가 뒤로 가서는 안 된다."""
        S = V.FrameState
        self.assertFalse(V.can_transition(S.UPLOADED, S.PENDING))
        self.assertFalse(V.can_transition(S.IN_DATASET, S.INDEXED))

    def test_terminal_states(self):
        S = V.FrameState
        for t in (S.DROPPED, S.IN_DATASET, S.QUARANTINED):
            self.assertEqual(V.ALLOWED_TRANSITIONS[t], frozenset())


class TestVerdict(unittest.TestCase):
    def test_json_round_trip(self):
        v = make()
        b = V.VerdictBatch(vin=v.vin, sent_at="2026-09-21T14:03:11Z", verdicts=[v])
        back = V.VerdictBatch.from_json(b.to_json())
        self.assertEqual(len(back.verdicts), 1)
        self.assertEqual(back.verdicts[0].frame_uuid, v.frame_uuid)
        self.assertEqual(back.verdicts[0].reclaim_mode, V.ReclaimMode.PREEMPT_RESUME)

    def test_payload_round_trip(self):
        p = V.PayloadRef(bucket="ns-raw-payload", key="k", offset=2359296,
                         length=147921, sha256="a" * 64)
        v = make(selected=True, payload=p)
        b = V.VerdictBatch(vin=v.vin, sent_at="t", verdicts=[v])
        back = V.VerdictBatch.from_json(b.to_json()).verdicts[0]
        self.assertEqual(back.payload.offset, 2359296)

    def test_granule_k_none_is_valid(self):
        """헤드라인이 k→∞ 가정 위라 정수로 적을 값이 없다. None 이 정식 표현이다."""
        v = make()
        self.assertIsNone(v.granule_k)
        b = V.VerdictBatch(vin=v.vin, sent_at="t", verdicts=[v])
        self.assertIsNone(V.VerdictBatch.from_json(b.to_json()).verdicts[0].granule_k)

    def test_unknown_schema_version_rejected(self):
        d = make().to_dict()
        d["schema_version"] = 99
        with self.assertRaises(ValueError):
            V.Verdict.from_dict(d)


class TestValidate(unittest.TestCase):
    def test_accepts_wellformed(self):
        make().validate()

    def test_rejects_u8_overflow(self):
        with self.assertRaises(ValueError):
            make(conf_q8=256).validate()

    def test_selected_requires_payload(self):
        with self.assertRaises(ValueError):
            make(selected=True).validate()

    def test_unselected_forbids_payload(self):
        p = V.PayloadRef("b", "k", 0, 1, "s")
        with self.assertRaises(ValueError):
            make(selected=False, payload=p).validate()

    def test_tiles_scored_bounded_by_mask(self):
        with self.assertRaises(ValueError):
            make(tile_mask=0b00000001, tiles_scored=2).validate()


if __name__ == "__main__":
    unittest.main()
