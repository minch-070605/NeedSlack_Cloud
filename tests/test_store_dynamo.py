# -*- coding: utf-8 -*-
"""같은 수집 시나리오를 MemoryTable 과 DynamoDB Local 에 돌려 **아이템 단위로 같음**을 본다.

test_collector.py 는 MemoryTable 로만 돈다. 그 결과가 실제 DynamoDB 의 조건식·if_not_exists·
집합 ADD 와 같게 나오는지는 여기서만 확인된다. 두 구현이 갈라지면 단위 테스트는 거짓 안심이 된다.

스택이 떠 있지 않으면(`AWS_ENDPOINT_DYNAMODB` 에 연결 불가) **건너뛴다** — `python run.py test`
는 스택 없이도 돌아야 한다. 테스트마다 임시 테이블(`ns-test-…`)을 만들고 지운다.
운영 테이블 `ns-curation-index` 는 건드리지 않는다.
"""
import json
import os
import sys
import unittest
import uuid

_R = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_R, "ingest"))
sys.path.insert(0, os.path.join(_R, "schema"))
sys.path.insert(0, os.path.abspath(_R))

import store as S                   # noqa: E402
import test_collector as TC         # noqa: E402
import test_replay as TR            # noqa: E402


def _client_or_skip():
    ep = os.environ.get("AWS_ENDPOINT_DYNAMODB")
    if not ep:
        raise unittest.SkipTest("AWS_ENDPOINT_DYNAMODB 없음 — `python run.py test` 로 실행")
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise unittest.SkipTest("boto3 없음 (.venv 밖)")
    c = boto3.client("dynamodb", endpoint_url=ep,
                     region_name=os.environ.get("AWS_REGION", "ap-northeast-2"),
                     config=Config(connect_timeout=1, read_timeout=5,
                                   retries={"max_attempts": 1}))
    try:
        c.list_tables(Limit=1)
    except Exception as e:
        raise unittest.SkipTest(f"DynamoDB Local 에 연결 불가 — 스택이 내려가 있다 ({type(e).__name__})")
    return c


def _scan(client, name):
    table = S.DynamoTable(client, name)
    out, kw = {}, {}
    while True:
        r = client.scan(TableName=name, ConsistentRead=True, **kw)
        for raw in r["Items"]:
            it = table._d(raw)
            out[(it["PK"], it["SK"])] = it
        if "LastEvaluatedKey" not in r:
            return out
        kw = {"ExclusiveStartKey": r["LastEvaluatedKey"]}


class TestDynamoMatchesMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = _client_or_skip()

    def setUp(self):
        with open(os.path.join(_R, "schema", "ddb_table.json"), encoding="utf-8") as f:
            spec = json.load(f)
        spec = {k: v for k, v in spec.items() if not k.startswith("_")}
        self.name = spec["TableName"] = f"ns-test-{uuid.uuid4().hex[:8]}"
        self.c.create_table(**spec)
        self.c.get_waiter("table_exists").wait(TableName=self.name)

    def tearDown(self):
        self.c.delete_table(TableName=self.name)

    def _both(self, scenario):
        """같은 시나리오를 두 구현에 돌려 (메모리 아이템, 다이나모 아이템) 을 돌려준다."""
        mem = S.MemoryTable()
        scenario(S.Store(mem))
        scenario(S.Store(S.DynamoTable(self.c, self.name)))
        return mem.items, _scan(self.c, self.name)

    def _assert_same(self, mem, ddb):
        self.assertEqual(set(mem), set(ddb))
        for k in mem:
            self.assertEqual(mem[k], ddb[k], k)

    def test_landing_with_drops_redelivered_three_times(self):
        _, sink, _, _ = TR.run(n=60, every=1, queue_capacity=40_000)

        def sc(st):
            col = TC.CO.Collector(st, TC.BUCKET, now=lambda: TC.NOW)
            for _ in range(3):
                TC.feed(col, sink)
        self._assert_same(*self._both(sc))

    def test_skew_payload_after(self):
        _, sink, _, _ = TR.run(n=150, every=3)

        def sc(st):
            col = TC.CO.Collector(st, TC.BUCKET, now=lambda: TC.NOW)
            TC.feed(col, sink)
            TC.arrive_all(st, sink)
        mem, ddb = self._both(sc)
        self._assert_same(mem, ddb)
        self.assertTrue(all(v["state"] == TC.UPLOADED for k, v in ddb.items()
                            if k[0].startswith("FRAME#")))

    def test_skew_payload_before(self):
        _, sink, _, _ = TR.run(n=150, every=3)

        def sc(st):
            col = TC.CO.Collector(st, TC.BUCKET, now=lambda: TC.NOW)
            TC.arrive_all(st, sink)
            TC.feed(col, sink)
        mem, ddb = self._both(sc)
        self._assert_same(mem, ddb)
        self.assertTrue(all(v["state"] == TC.UPLOADED for k, v in ddb.items()
                            if k[0].startswith("FRAME#")))

    def test_reconciler_both_orders_and_sweep(self):
        """조정기 경로 — 도착 기록 · 승격 · 체크섬 전이 · GSI1 스윕이 두 구현에서 같다."""
        import test_reconciler as TRC
        _, sink, _, _ = TR.run(n=150, every=3)
        half = dict(list(sink.blobs.items())[: len(sink.blobs) // 2])

        def sc(st):
            col = TC.CO.Collector(st, TC.BUCKET, now=lambda: TC.NOW)
            rc = TRC.RC.Reconciler(st, TRC.MemoryBlobs(sink), TRC.BUCKET, ["sim-DYN_B"],
                                   now=lambda: TC.NOW)
            for key, (blob, meta) in half.items():          # 절반은 번들이 먼저
                rc.handle_event(TRC.s3_event(key, blob, meta))
            TC.feed(col, sink)
            rc.sweep_uploaded()
            rc.scan_bucket()                                 # 나머지 절반은 이벤트 유실
        mem, ddb = self._both(sc)
        self._assert_same(mem, ddb)
        self.assertTrue(all(v["state"] == "INDEXED" for k, v in ddb.items()
                            if k[0].startswith("FRAME#")))

    def test_state_gsi_query(self):
        """스냅샷이 쓸 경로 — GSI1 로 한 차량의 한 상태만 뽑힌다."""
        r, sink, _, _ = TR.run(n=60, every=1, queue_capacity=40_000)
        st = S.Store(S.DynamoTable(self.c, self.name))
        TC.feed(TC.CO.Collector(st, TC.BUCKET, now=lambda: TC.NOW), sink)
        for state, want in (("PENDING", r.accepted), ("DROPPED", r.dropped)):
            n, kw = 0, {}
            while True:
                q = self.c.query(TableName=self.name, IndexName="GSI1",
                                 KeyConditionExpression="GSI1PK = :p",
                                 ExpressionAttributeValues={
                                     ":p": {"S": f"VIN#sim-DYN_B#STATE#{state}"}},
                                 Select="COUNT", **kw)
                n += q["Count"]
                if "LastEvaluatedKey" not in q:
                    break
                kw = {"ExclusiveStartKey": q["LastEvaluatedKey"]}
            self.assertEqual(n, want, state)


if __name__ == "__main__":
    unittest.main()
