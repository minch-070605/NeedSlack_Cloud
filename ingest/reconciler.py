# -*- coding: utf-8 -*-
"""reconciler.py — 조정기 (M1-5). 평면 2 도착(S3 이벤트) → 상태 전이 → 체크섬.

    python run.py reconcile          # 로컬 스택. Ctrl+C 로 멈춘다 (세션은 브로커에 남는다)

하는 일 (구현설계_MVP.md §2-3 · §3-1)
-------------------------------------------------------------------------------
    S3 이벤트 ─▶ BUNDLE# 도착 기록 ─▶ frame_uuids 를 PENDING → UPLOADED
                                  └▶ 번들 검증: 프레임마다 payload.offset/length 로 잘라 sha256 대조
                                                  일치 → INDEXED · 불일치 → QUARANTINED

번들 객체는 **한 번 GET** 해 판정의 (offset, length) 로 자른다. 프레임마다 Range GET 하면
요청이 35배가 되고 검사하는 성질은 같다.

세 경로 — 이벤트만 믿지 않는다
-------------------------------------------------------------------------------
1. **이벤트** — `verdict.TOPIC_S3_EVENTS`. 영속 세션 + 수동 PUBACK (수집기와 같은 규약).
2. **버킷 대조** (기동 시) — 버킷을 훑어 도착 기록이 없는 번들을 이벤트처럼 처리한다.
   조정기 세션이 생기기 전에 올라간 번들, MinIO `queue_dir` 한도를 넘어 잃은 이벤트를 메운다.
3. **UPLOADED 스윕** (주기) — 판정이 번들보다 늦게 오면 **수집기가** UPLOADED 로 옮기는데,
   체크섬은 조정기 몫이라 그 프레임은 이벤트 없이 남는다. 검증 도중 죽은 경우도 같다.
   GSI1 `VIN#{vin}#STATE#UPLOADED` 를 훑어 번들별로 검증한다.

격리 — 키 접두사의 vin 을 믿는다
-------------------------------------------------------------------------------
M4 에서 S3 접두사 스코핑이 지키는 것은 **키**(`vin={vin}/…`)다. 메타데이터 vin 은 차량이
마음대로 적을 수 있으므로, 키의 vin 과 다르면 버린다. 프레임의 payload 가 가리키는 번들이
이 번들이 아니어도 QUARANTINED 로 둔다.

시각
-------------------------------------------------------------------------------
도착 지연 `arrival_latency_s = t_put_utc − t_frame_utc` 는 **둘 다 차량 시계**다. 가속 재생에서
클라우드 벽시계(t_arrived_utc)로 재면 틀린다 — 벽시계는 따로 적기만 한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_HERE, os.path.join(ROOT, "schema"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store as S          # noqa: E402
import verdict as V        # noqa: E402

CLIENT_ID = "ns-reconciler"     # 고정 — 영속 세션이 이 이름에 붙는다
SWEEP_EVERY_S = 30.0
STATS_EVERY_S = 10.0

#: 재생기가 쓰는 번들 키 (구현설계_MVP.md §2-4). 이 모양이 아니면 번들이 아니다.
BUNDLE_KEY = re.compile(
    r"^vin=(?P<vin>[^/]+)/boot=(?P<boot>[^/]+)/session=(?P<session>\d{4})/bundle-\d{5}\.bin$")


class Rejected(ValueError):
    """독 이벤트. 재전송해도 결과가 같으므로 확인하고 버린다."""


def _utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ")


def normalize_meta(meta: dict[str, str]) -> dict[str, str]:
    """이벤트의 `X-Amz-Meta-T-Put-Utc` 와 head_object 의 `t-put-utc` 를 같은 모양으로."""
    out = {}
    for k, v in meta.items():
        k = k.lower()
        if k.startswith("x-amz-meta-"):
            out[k[len("x-amz-meta-"):]] = v
        elif k != "content-type":
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────── 객체 저장소
class S3Blobs:
    """실제 S3/MinIO. 접근은 `common.aws` 를 통과한다."""

    def __init__(self, client):
        self.c = client

    def get(self, bucket: str, key: str) -> bytes:
        return self.c.get_object(Bucket=bucket, Key=key)["Body"].read()

    def head(self, bucket: str, key: str) -> dict[str, Any]:
        r = self.c.head_object(Bucket=bucket, Key=key)
        return {"size": r["ContentLength"], "etag": r["ETag"].strip('"'),
                "meta": normalize_meta(r.get("Metadata", {}))}

    def keys(self, bucket: str) -> Iterable[str]:
        for page in self.c.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for o in page.get("Contents", []):
                yield o["Key"]


# ─────────────────────────────────────────────────────────── 조정기
class Reconciler:
    def __init__(self, store: S.Store, blobs, bucket: str, vins: Iterable[str],
                 now: Callable[[], str] = S.utc_now):
        self.s, self.b, self.bucket, self.vins, self.now = store, blobs, bucket, tuple(vins), now
        self.n = Counter()
        #: 이벤트 스레드와 스윕이 같은 번들을 동시에 만지지 않게 한다. 전이가 조건부라
        #: 겹쳐도 틀리지는 않지만, 같은 번들을 두 번 GET 하는 낭비와 카운터 경합을 막는다.
        self.lock = threading.Lock()

    # ── 입력 1: 이벤트
    def handle_event(self, payload: bytes) -> None:
        """MQTT 메시지 1건(S3 이벤트, Records 여러 개 가능)."""
        try:
            doc = json.loads(payload.decode("utf-8"))
            records = doc["Records"]
        except (UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
            raise Rejected(f"S3 이벤트 파싱 실패: {e}") from e
        for r in records:
            try:
                name = r["eventName"]
                obj, bucket = r["s3"]["object"], r["s3"]["bucket"]["name"]
                key = urllib.parse.unquote_plus(obj["key"])        # 본문 키는 URL 인코딩돼 있다
            except (KeyError, TypeError) as e:
                raise Rejected(f"S3 이벤트 레코드 형식 오류: {e}") from e
            if not name.startswith("s3:ObjectCreated:"):
                self.n["event_ignored"] += 1
                continue
            with self.lock:
                self.arrive(bucket, key, obj.get("size"), obj.get("eTag"),
                            normalize_meta(obj.get("userMetadata", {})))
            self.n["event"] += 1

    # ── 입력 2: 버킷 대조
    def scan_bucket(self) -> int:
        """도착 기록이 없는 번들을 이벤트처럼 처리한다. 처리한 수."""
        done = 0
        for key in self.b.keys(self.bucket):
            if not BUNDLE_KEY.match(key):
                self.n["scan_skipped"] += 1
                continue
            bd = self.s.get_bundle(key)
            if bd is not None and S.arrived(bd):
                continue
            h = self.b.head(self.bucket, key)
            with self.lock:
                try:
                    self.arrive(self.bucket, key, h["size"], h["etag"], h["meta"])
                except Rejected as e:
                    self.n["rejected"] += 1
                    print(f"거부  {key}: {e}", flush=True)
                    continue
            done += 1
        self.n["scan_arrived"] += done
        return done

    # ── 입력 3: UPLOADED 스윕
    def sweep_uploaded(self) -> int:
        """UPLOADED 에 남은 프레임을 번들별로 검증한다. 검증을 마친 프레임 수."""
        done = 0
        for vin in self.vins:
            by_key: dict[str, list[str]] = defaultdict(list)
            for it in self.s.frames_in_state(vin, V.FrameState.UPLOADED):
                by_key[it["payload"]["key"]].append(it["frame_uuid"])
            for key, uuids in by_key.items():
                with self.lock:
                    done += self.verify(self.bucket, key, uuids)
        self.n["sweep_verified"] += done
        return done

    # ── 공통
    def arrive(self, bucket: str, key: str, size: Optional[int], etag: Optional[str],
               meta: dict[str, str]) -> None:
        if bucket != self.bucket:
            raise Rejected(f"모르는 버킷 {bucket}")
        m = BUNDLE_KEY.match(key)
        if not m:
            raise Rejected(f"번들 키 모양이 아니다: {key}")
        vin = m["vin"]
        if meta.get("vin") != vin:
            raise Rejected(f"키 vin {vin} ≠ 메타데이터 vin {meta.get('vin')}")
        if "t-put-utc" not in meta or "n-frames" not in meta:
            raise Rejected(f"번들 메타데이터 누락: {sorted(meta)}")
        now = self.now()
        arrival = {"vin": vin, "t_put_utc": meta["t-put-utc"], "n_frames": int(meta["n-frames"]),
                   "boot_id": m["boot"], "drive_session": int(m["session"])}
        if size is not None:
            arrival["size"] = int(size)
        if etag:
            arrival["etag"] = etag
        bd = self.s.bundle_mark_arrived(key, bucket, arrival, now)
        uuids = sorted(bd.get("frame_uuids", set()))
        self.n["uploaded"] += self.s.promote_uploaded(bd, uuids, now)
        self.verify(bucket, key, uuids)

    def verify(self, bucket: str, key: str, frame_uuids: Iterable[str]) -> int:
        """UPLOADED 인 프레임만 검증해 INDEXED / QUARANTINED. 옮긴 수."""
        frames = [f for f in (self.s.get_frame(u) for u in frame_uuids)
                  if f is not None and f["state"] == V.FrameState.UPLOADED.value]
        if not frames:
            return 0
        body = self.b.get(bucket, key)
        now, moved = self.now(), 0
        for f in frames:
            reason = self._check(f, bucket, key, body)
            if reason is None:
                extra = {}
                if "t_put_utc" in f:
                    extra["arrival_latency_s"] = round(
                        (_utc(f["t_put_utc"]) - _utc(f["t_frame_utc"])).total_seconds(), 3)
                ok = self.s.transition(f["frame_uuid"], f["vin"], V.FrameState.UPLOADED,
                                       V.FrameState.INDEXED, now, extra)
                self.n["indexed"] += ok
            else:
                ok = self.s.transition(f["frame_uuid"], f["vin"], V.FrameState.UPLOADED,
                                       V.FrameState.QUARANTINED, now, {"quarantine_reason": reason})
                self.n["quarantined"] += ok
                if ok:
                    print(f"격리  {f['frame_uuid']} ({key}): {reason}", flush=True)
            moved += ok
        return moved

    @staticmethod
    def _check(f: dict[str, Any], bucket: str, key: str, body: bytes) -> Optional[str]:
        p = f.get("payload") or {}
        if p.get("bucket") != bucket or p.get("key") != key:
            return f"payload 가 다른 번들을 가리킨다 ({p.get('bucket')}/{p.get('key')})"
        off, n = int(p["offset"]), int(p["length"])
        if off < 0 or off + n > len(body):
            return f"구간 [{off}, {off + n}) 이 번들 크기 {len(body)} 를 넘는다"
        if hashlib.sha256(body[off:off + n]).hexdigest() != p["sha256"]:
            return "sha256 불일치"
        return None

    def summary(self) -> str:
        n = self.n
        return (f"이벤트 {n['event']:,} (무시 {n['event_ignored']:,}) · 버킷대조 {n['scan_arrived']:,}"
                f" | UPLOADED {n['uploaded']:,} · INDEXED {n['indexed']:,}"
                f" · QUARANTINED {n['quarantined']:,} (스윕 {n['sweep_verified']:,}) | 거부 {n['rejected']:,}")


# ─────────────────────────────────────────────────────────── 실행
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="조정기 — S3 이벤트 → 상태 전이 → 체크섬")
    ap.add_argument("--table", default=S.table_name(),
                    help="적재 테이블. 기본은 schema/ddb_table.json 의 운영 테이블 (점검용으로만 바꾼다)")
    ap.add_argument("--bucket", default=None, help="평면 2 버킷. 기본은 S3_BUCKET")
    ap.add_argument("--client-id", default=CLIENT_ID,
                    help="영속 세션 이름. 운영에서는 바꾸지 않는다 — 바꾸면 보관된 이벤트를 못 받는다")
    a = ap.parse_args(argv)

    from common import aws
    rc = Reconciler(S.Store(S.DynamoTable(aws.dynamodb_client(), a.table)),
                    S3Blobs(aws.s3_client()), a.bucket or aws.raw_bucket(),
                    [V.vin_of(arm) for arm in V.ARMS])
    fatal: list[BaseException] = []
    stop = threading.Event()

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"MQTT 연결 실패: {reason_code}", flush=True)
            return
        c.subscribe(V.TOPIC_S3_EVENTS, qos=1)
        print(f"구독  {V.TOPIC_S3_EVENTS}  (세션 {'이어받음' if flags.session_present else '새로'})",
              flush=True)

    def on_message(c, userdata, m):
        if stop.is_set():
            return                              # 멈추는 중 — 확인하지 않는다(재전송된다)
        try:
            rc.handle_event(m.payload)
        except Rejected as e:
            rc.n["rejected"] += 1
            print(f"거부  {m.topic}: {e}", flush=True)
        except BaseException as e:              # 저장소 오류 — 확인하지 않고 멈춘다
            fatal.append(e)
            stop.set()
            return
        c.ack(m.mid, m.qos)

    print(f"조정기 시작  client_id={a.client_id} · 테이블 {a.table} · 버킷 {rc.bucket}", flush=True)
    # 구독을 먼저 건다 — 버킷 대조 중에 올라오는 번들의 이벤트를 놓치지 않게. 겹쳐 처리돼도 멱등이다.
    mq = aws.mqtt_client(a.client_id, persistent=True, on_connect=on_connect, on_message=on_message)
    try:
        print(f"버킷 대조  도착 기록 없던 번들 {rc.scan_bucket():,} 개 처리", flush=True)
        while not stop.is_set():
            rc.sweep_uploaded()
            for _ in range(int(SWEEP_EVERY_S / STATS_EVERY_S)):
                if stop.wait(STATS_EVERY_S):
                    break
                print("  " + rc.summary(), flush=True)
    except KeyboardInterrupt:
        stop.set()
    except BaseException as e:                  # 버킷 대조·스윕 중 저장소 오류
        fatal.append(e)
        stop.set()
    finally:
        mq.loop_stop()
        mq.disconnect()
    print("조정기 정지  " + rc.summary(), flush=True)
    if fatal:
        print(f"★ 저장소 오류로 멈췄다 — 처리하지 못한 이벤트는 확인하지 않았다(재시작하면 "
              f"재전송되고, 버킷 대조가 나머지를 메운다): {fatal[0]!r}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
