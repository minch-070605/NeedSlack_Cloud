# -*- coding: utf-8 -*-
"""collector.py — 수집기 (M1-4). 판정 평면 MQTT → DynamoDB `ns-curation-index`.

    python run.py collect            # 로컬 스택. Ctrl+C 로 멈춘다 (세션은 브로커에 남는다)

구독 (구현설계_MVP.md §2-4)
-------------------------------------------------------------------------------
    ns/v1/+/curation/verdict      판정 배치 → FRAME# PENDING (조건부 쓰기) + BUNDLE# frame_uuids ADD
    ns/v1/+/curation/telemetry    텔레메트리 → VIN#/TEL# + dropped_frame_uuids → FRAME# DROPPED

번들이 판정보다 먼저 와 있으면(BUNDLE# 에 도착 속성) 그 자리에서 UPLOADED 로 옮긴다 —
`store.py` 머리말 "스큐". 체크섬(UPLOADED → INDEXED)은 조정기 몫이다.

전달 보장 — at-least-once 를 멱등으로 흡수한다
-------------------------------------------------------------------------------
- **영속 세션 + 수동 PUBACK.** 처리를 마친 메시지만 확인한다. 저장소 오류로 처리하지 못하면
  확인하지 않고 **프로세스를 멈춘다** — 다시 켜면 브로커가 미확인 메시지를 재전송한다.
  계속 돌면서 건너뛰면 그 메시지는 재접속 전까지 오지 않는다.
- **독 메시지는 버린다.** 파싱·스키마·불변식 위반은 몇 번 재전송해도 같으므로 확인하고 센다.
- 재전송된 판정은 조건부 쓰기가, 재전송된 번들 목록은 집합 ADD 가 흡수한다(수용 기준 5).

격리 — 토픽의 vin 과 본문의 vin 이 다르면 버린다
-------------------------------------------------------------------------------
M4 에서 IoT 정책이 토픽을 차량별로 가른다(`${iot:Connection.Thing.ThingName}`). 본문 vin 을
그대로 믿으면 한 차량이 남의 프레임을 쓸 수 있다 — 정책이 지키는 것은 토픽뿐이다.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from typing import Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_HERE, os.path.join(ROOT, "schema"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store as S          # noqa: E402
import verdict as V        # noqa: E402

CLIENT_ID = "ns-collector"      # 고정 — 영속 세션이 이 이름에 붙는다
TOPICS = (V.topic_verdict("+"), V.topic_telemetry("+"))
STATS_EVERY_S = 10.0


class Rejected(ValueError):
    """독 메시지. 재전송해도 결과가 같으므로 확인하고 버린다."""


def topic_vin(topic: str) -> tuple[str, str]:
    """ns/v1/{vin}/curation/{kind} → (vin, kind)."""
    p = topic.split("/")
    if len(p) != 5 or p[:2] != ["ns", "v1"] or p[3] != "curation" or \
            p[4] not in ("verdict", "telemetry"):
        raise Rejected(f"모르는 토픽 {topic}")
    return p[2], p[4]


class Collector:
    def __init__(self, store: S.Store, now: Callable[[], str] = S.utc_now):
        self.s, self.now = store, now
        self.n = Counter()

    def handle(self, topic: str, payload: bytes) -> None:
        """메시지 1건. `Rejected` 는 독 메시지, 그 밖의 예외는 저장소 오류다."""
        vin, kind = topic_vin(topic)
        try:
            text = payload.decode("utf-8")
            if kind == "verdict":
                msg = V.VerdictBatch.from_json(text)
            else:
                msg = V.Telemetry.from_json(text)
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
            raise Rejected(f"{kind} 파싱 실패: {e}") from e
        if msg.vin != vin:
            raise Rejected(f"토픽 vin {vin} ≠ 본문 vin {msg.vin}")
        if kind == "verdict":
            self._verdicts(msg)
        else:
            self._telemetry(msg)

    def _verdicts(self, batch: V.VerdictBatch) -> None:
        for v in batch.verdicts:
            if v.vin != batch.vin:
                raise Rejected(f"판정 vin {v.vin} ≠ 봉투 vin {batch.vin}")
            if not v.selected:
                raise Rejected("미선별 판정은 판정 평면에 오지 않는다 (텔레메트리 몫)")
            try:
                v.validate()
            except ValueError as e:
                raise Rejected(f"판정 불변식 위반: {e}") from e

        now = self.now()
        by_bundle: dict[tuple[str, str], list[str]] = defaultdict(list)
        for v in batch.verdicts:
            self.n["verdict_new" if self.s.put_verdict(v, now) else "verdict_dup"] += 1
            by_bundle[(v.payload.bucket, v.payload.key)].append(v.frame_uuid)

        for (bucket, key), uuids in by_bundle.items():
            bd = self.s.bundle_add_frames(key, bucket, batch.vin, uuids)
            if S.arrived(bd):                       # 번들이 먼저 와 있었다
                self.n["uploaded_on_verdict"] += self.s.promote_uploaded(bd, uuids, now)
        self.n["verdict_msg"] += 1

    def _telemetry(self, tel: V.Telemetry) -> None:
        now = self.now()
        self.n["telemetry_new" if self.s.put_telemetry(tel, now) else "telemetry_dup"] += 1
        for fu in tel.dropped_frame_uuids:
            ok = self.s.put_dropped(fu, tel.vin, tel.boot_id, tel.drive_session, now)
            self.n["dropped_new" if ok else "dropped_dup"] += 1
        if tel.final:
            self.n["final"] += 1

    def summary(self) -> str:
        n = self.n
        return (f"판정 메시지 {n['verdict_msg']:,} · 신규 {n['verdict_new']:,} · 중복 {n['verdict_dup']:,}"
                f" · 번들 선도착 UPLOADED {n['uploaded_on_verdict']:,}"
                f" | 텔레메트리 {n['telemetry_new']:,} (중복 {n['telemetry_dup']:,}, final {n['final']})"
                f" · DROPPED {n['dropped_new']:,} | 거부 {n['rejected']:,}")


# ─────────────────────────────────────────────────────────── 실행
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="수집기 — 판정 평면 MQTT → DynamoDB")
    ap.add_argument("--table", default=S.table_name(),
                    help="적재 테이블. 기본은 schema/ddb_table.json 의 운영 테이블 (점검용으로만 바꾼다)")
    ap.add_argument("--client-id", default=CLIENT_ID,
                    help="영속 세션 이름. 운영에서는 바꾸지 않는다 — 바꾸면 보관된 메시지를 못 받는다")
    a = ap.parse_args(argv)

    from common import aws
    col = Collector(S.Store(S.DynamoTable(aws.dynamodb_client(), a.table)))
    fatal: list[BaseException] = []
    stop = threading.Event()

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"MQTT 연결 실패: {reason_code}", flush=True)
            return
        # 영속 세션이라 구독이 남아 있을 수 있지만, 첫 연결·세션 만료에 대비해 늘 다시 건다
        c.subscribe([(t, 1) for t in TOPICS])
        print(f"구독  {', '.join(TOPICS)}  (세션 {'이어받음' if flags.session_present else '새로'})",
              flush=True)

    def on_message(c, userdata, m):
        if stop.is_set():
            return                              # 멈추는 중 — 확인하지 않는다(재전송된다)
        try:
            col.handle(m.topic, m.payload)
        except Rejected as e:
            col.n["rejected"] += 1
            print(f"거부  {m.topic}: {e}", flush=True)
        except BaseException as e:              # 저장소 오류 — 확인하지 않고 멈춘다
            fatal.append(e)
            stop.set()
            return
        c.ack(m.mid, m.qos)

    mq = aws.mqtt_client(a.client_id, persistent=True, on_connect=on_connect, on_message=on_message)
    print(f"수집기 시작  client_id={a.client_id} · 테이블 {a.table}", flush=True)
    try:
        while not stop.wait(STATS_EVERY_S):
            print("  " + col.summary(), flush=True)
    except KeyboardInterrupt:
        stop.set()
    finally:
        mq.loop_stop()
        mq.disconnect()
    print("수집기 정지  " + col.summary(), flush=True)
    if fatal:
        print(f"★ 저장소 오류로 멈췄다 — 처리하지 못한 메시지는 확인하지 않았다(재시작하면 "
              f"재전송된다): {fatal[0]!r}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
