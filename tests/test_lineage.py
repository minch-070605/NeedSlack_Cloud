# -*- coding: utf-8 -*-
"""계보 역추적이 조인 한 번으로 도는지 실제 PostgreSQL 스키마에서 검사한다 (수용 기준 4).

    eval_result → model_run → dataset_version → dataset_frame → frame_uuid

전부 **롤백되는 트랜잭션** 안에서 한다 — 운영 계보에 아무것도 남기지 않는다. 테스트 전용 캠페인
(가짜 profile_sha)을 쓰므로 운영 `ds-{ARM}` 과 UNIQUE 제약이 부딪치지 않는다.
PostgreSQL 에 닿지 않으면(스택 내려감 · `.venv` 밖) 건너뛴다.
"""
import os
import sys
import unittest
import uuid

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "pipelines"))
sys.path.insert(0, os.path.join(_R, "schema"))

import snapshot as SN               # noqa: E402
import test_snapshot as TS          # noqa: E402

#: schema/pg/001_init.sql 끝의 역추적 질의 그대로
LINEAGE = """
    SELECT e.run_id, e.value AS map50, dv.arm, m.seed, c.profile_sha, df.frame_uuid, df.frame_id
      FROM eval_result   e
      JOIN model_run     m  ON m.run_id  = e.run_id
      JOIN dataset_version dv ON dv.ds_id = m.ds_id
      JOIN campaign      c  ON c.campaign_id = dv.campaign_id
      JOIN dataset_frame df ON df.ds_id = dv.ds_id AND df.split = 'pool'
     WHERE e.run_id = %s AND e.metric = 'mAP50'"""


def _conn_or_skip():
    dsn = os.environ.get("PG_DSN")
    if not dsn:
        raise unittest.SkipTest("PG_DSN 없음 — `python run.py test` 로 실행")
    try:
        import psycopg
    except ImportError:
        raise unittest.SkipTest("psycopg 없음 (.venv 밖)")
    try:
        return psycopg.connect(dsn, connect_timeout=2)
    except Exception as e:
        raise unittest.SkipTest(f"PostgreSQL 에 연결 불가 — 스택이 내려가 있다 ({type(e).__name__})")


class TestLineage(unittest.TestCase):
    def setUp(self):
        self.conn = _conn_or_skip()
        frames, sel, _, _, _ = TS.indexed()
        self.rows = SN.plan("DYN_B", frames, sel, TS.splits_for(frames), None, TS.PROFILE)
        tag = uuid.uuid4().hex[:8]
        self.ds_id, self.run_id = f"ds-test-{tag}", f"test-{tag}"
        self.profile = {"sha": uuid.uuid4().hex[:16], "weights": {"VALUE_TH": 0}}

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def register(self, rows=None, uri="s3://ns-datasets/datasets/test/manifest.jsonl"):
        return SN.register_pg(self.conn, "DYN_B", rows or self.rows, uri, self.profile,
                              0.10, 20260827, True, ds_id=self.ds_id, commit=False)

    def test_one_join_walks_score_back_to_every_frame(self):
        self.assertEqual(self.register(), "new")
        with self.conn.cursor() as cur:
            cur.execute("""INSERT INTO model_run (run_id, ds_id, epochs, batch, imgsz, seed, optimizer,
                                                  mosaic, amp, train_args_sha)
                           VALUES (%s, %s, 16, 32, 640, 1, 'SGD', 0, FALSE, %s)""",
                        (self.run_id, self.ds_id, "a" * 16))
            cur.execute("INSERT INTO eval_result (run_id, metric, value, n_held_out) VALUES (%s, 'mAP50', 0.3, 2826)",
                        (self.run_id,))
            cur.execute(LINEAGE, (self.run_id,))
            got = cur.fetchall()
        self.assertEqual(len(got), len(self.rows))
        self.assertEqual({g[5] for g in got}, {r["frame_uuid"] for r in self.rows})
        self.assertEqual({(g[2], g[4]) for g in got}, {("DYN_B", self.profile["sha"])})

    def test_reregister_same_is_noop_and_different_is_refused(self):
        self.assertEqual(self.register(), "new")
        self.assertEqual(self.register(), "same")
        with self.assertRaises(SN.SnapshotError):
            self.register(rows=self.rows[1:])

    def test_schema_refuses_autotuned_run(self):
        """model_run 제약 — optimizer 'auto' · mosaic > 0 · amp 는 스키마가 막는다."""
        import psycopg
        self.register()
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT s")
            with self.assertRaises(psycopg.errors.CheckViolation):
                cur.execute("""INSERT INTO model_run (run_id, ds_id, epochs, batch, imgsz, seed, optimizer,
                                                      mosaic, amp, train_args_sha)
                               VALUES (%s, %s, 16, 32, 640, 1, 'auto', 0, FALSE, %s)""",
                            (self.run_id, self.ds_id, "a" * 16))
            cur.execute("ROLLBACK TO SAVEPOINT s")


if __name__ == "__main__":
    unittest.main()
