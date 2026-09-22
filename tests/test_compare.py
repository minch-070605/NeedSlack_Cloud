# -*- coding: utf-8 -*-
"""compare.py 가 M3 결과의 출처를 제대로 가려내고, 계보에 한 번만 묶는지 검사한다.

`m3_eval.json` 은 노트북 셀 8 이 쓰는 모양 그대로 합성한다. 검증·대조는 스택 없이,
계보 등록은 PostgreSQL 에서 롤백되는 트랜잭션 안에서 돈다(없으면 건너뜀).

    python -m unittest discover -s tests
"""
import copy
import os
import sys
import unittest
import uuid

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "pipelines"))
sys.path.insert(0, os.path.join(_R, "schema"))

import compare as CP                # noqa: E402

TARS = {"eval-common.tar": "e" * 64, "ds-STA_B.tar": "a" * 64, "ds-DYN_B.tar": "b" * 64}
HELD_SHA, N_HELD = "h" * 64, 2826
SRC = {"yolo_detector.py": "y" * 64, "apeval.py": "p" * 64}
MAP = {"STA_B": (0.300, 0.297, 0.295), "DYN_B": (0.318, 0.325, 0.327)}


def m3():
    args = {k: v for k, v in CP.LOCKED.items()}
    args.update(lr0="0.005", fliplr="0.5")
    runs, cond = {}, {}
    for arm, vals in MAP.items():
        for seed, v in zip((1, 2, 3), vals):
            rid = f"{arm}-s{seed}"
            runs[rid] = {"run_id": rid, "arm": arm, "seed": seed, "train_args_sha": "c" * 16,
                         "train_args": dict(args), "weights_sha256": "w" * 64, "mAP50": v,
                         "weights_uri": f"drive:m3_results/runs/{rid}/last.pt"}
        cond[arm] = {"per_seed": list(vals), "mean": sum(vals) / 3, "sd": 0.003}
    d = cond["DYN_B"]["mean"] - cond["STA_B"]["mean"]
    return {"runs": runs, "conditions": cond, "tar_sha256": dict(TARS), "n_held_out": N_HELD,
            "held_out_ids_sha256": HELD_SHA, "provenance": {"rev9_sources_sha256": dict(SRC)},
            "boot": 400, "boot_seed": 20260901,
            "DYN_B_vs_STA_B": {"delta": d, "ci": [d - 0.012, d + 0.012], "relative_pct": 100 * d / cond["STA_B"]["mean"]},
            "offline": {"STA_B": {"per_seed": [0.3044, 0.2969, 0.2952], "mean": 0.2989, "sd": 0.0049},
                        "DYN_B": {"per_seed": [0.3174, 0.3248, 0.3267], "mean": 0.3230, "sd": 0.0049},
                        "DYN_B_vs_STA_B": {"delta": 0.0241, "ci": [0.0115, 0.0369], "relative_pct": 8.07}}}


def check(d):
    return CP.validate(d, TARS, HELD_SHA, N_HELD, SRC)


class TestValidate(unittest.TestCase):
    def test_clean_result_passes(self):
        self.assertEqual(len(check(m3())), 6)

    def test_each_stop_condition(self):
        def drop_run(d): del d["runs"]["DYN_B-s3"]
        def other_tar(d): d["tar_sha256"]["ds-DYN_B.tar"] = "0" * 64
        def args_differ(d): d["runs"]["STA_B-s2"]["train_args_sha"] = "d" * 16
        def not_locked(d):
            for r in d["runs"].values():
                r["train_args"]["mosaic"] = "1.0"
        def other_held(d): d["held_out_ids_sha256"] = "0" * 64
        def other_code(d): d["provenance"]["rev9_sources_sha256"]["apeval.py"] = "0" * 64
        def wrong_mean(d): d["conditions"]["STA_B"]["mean"] += 0.01
        def misnamed(d): d["runs"]["STA_B-s1"]["seed"] = 2
        for f in (drop_run, other_tar, args_differ, not_locked, other_held, other_code, wrong_mean, misnamed):
            with self.subTest(f.__name__):
                d = m3()
                f(d)
                with self.assertRaises(CP.CompareError):
                    check(d)


class TestSummarize(unittest.TestCase):
    def test_facts_not_verdicts(self):
        s = CP.summarize(m3())
        f = s["facts"]
        self.assertTrue(f["same_sign"])
        self.assertTrue(f["pipeline_ci_excludes_zero"])
        self.assertTrue(f["cis_overlap"])
        self.assertAlmostEqual(s["arms"]["STA_B"]["per_seed_diff"][0], 0.300 - 0.3044)
        self.assertIn("Δ mAP50", CP.render(s, check(m3()), "new"))

    def test_opposite_sign_is_reported_not_raised(self):
        """PRD — 숫자가 달라도 결과다. 부호가 뒤집혀도 멈추지 않고 사실로 적는다."""
        d = m3()
        d["DYN_B_vs_STA_B"] = {"delta": -0.01, "ci": [-0.02, 0.0], "relative_pct": -3.0}
        f = CP.summarize(d)["facts"]
        self.assertFalse(f["same_sign"])
        self.assertFalse(f["cis_overlap"])


class TestRegister(unittest.TestCase):
    def setUp(self):
        import test_lineage as TL
        import test_snapshot as TS
        import snapshot as SN
        self.conn = TL._conn_or_skip()
        tag = uuid.uuid4().hex[:8]
        frames, sel, _, _, _ = TS.indexed()
        rows = SN.plan("DYN_B", frames, sel, TS.splits_for(frames), None, TS.PROFILE)
        self.ds = {}
        for arm in ("STA_B", "DYN_B"):
            prof = {"sha": uuid.uuid4().hex[:16], "weights": {"VALUE_TH": 0}}
            self.ds[arm] = f"ds-test-{arm}-{tag}"
            SN.register_pg(self.conn, arm, rows, "s3://x/m.jsonl", prof, 0.1, 20260827, True,
                           ds_id=self.ds[arm], commit=False)
        self.prefix = f"test-{tag}-"

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def reg(self, d):
        return CP.register(self.conn, d, ds_ids=self.ds, run_prefix=self.prefix, commit=False)

    def test_register_once_then_same(self):
        self.assertEqual(self.reg(m3()), "new")
        self.assertEqual(self.reg(m3()), "same")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT train_args_sha) FROM model_run WHERE run_id LIKE %s",
                        (self.prefix + "%",))
            self.assertEqual(cur.fetchone(), (6, 1))

    def test_changed_result_refused(self):
        self.reg(m3())
        d = copy.deepcopy(m3())
        d["runs"]["DYN_B-s1"]["mAP50"] = 0.5
        with self.assertRaises(CP.CompareError):
            self.reg(d)


if __name__ == "__main__":
    unittest.main()
