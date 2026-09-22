# -*- coding: utf-8 -*-
"""store.py — `ns-curation-index` 의 키 규약과 상태 전이. 수집기·조정기가 함께 쓴다.

키 규약의 단일 소스는 `schema/ddb_table.json` 의 `_access_patterns` 다. 이 모듈이 그것을
코드로 옮긴 유일한 곳이다 — 수집기·조정기·스냅샷이 키 문자열을 따로 만들지 않는다.

    FRAME#{frame_uuid} / META       판정 1건. state · GSI1PK = VIN#{vin}#STATE#{state}
    BUNDLE#{payload.key} / META     번들 1개. frame_uuids(문자열 집합) · 도착 속성
    VIN#{vin} / TEL#{sent_at}#{boot_id}   텔레메트리 1건

테이블 두 구현
-------------------------------------------------------------------------------
`Store` 는 아래 네 연산만 쓰는 `Table` 위에 선다. 연산이 적어야 두 구현이 갈라지지 않는다.

    put_new(item)                       PK 가 없을 때만 쓴다 → bool   (멱등 쓰기)
    get(pk, sk)                         강한 일관성 읽기 → dict | None
    update(pk, sk, set_if_absent, add)  없는 속성만 SET · 집합 ADD → 갱신 후 아이템 전체
    transition(pk, sk, src, updates)    state == src 일 때만 SET → bool

- `DynamoTable` — boto3 저수준 클라이언트. 접근은 `common.aws` 를 통과한다.
- `MemoryTable` — dict. 단위 테스트가 스택 없이 같은 `Store` 로직을 돌리게 한다.
  `tests/test_store_dynamo.py` 가 같은 시나리오를 DynamoDB Local 에 돌려 둘을 대조한다.

스큐 — 어느 쪽이 먼저 와도 된다
-------------------------------------------------------------------------------
판정(수 초)과 번들(최대 L_max)은 순서 없이 온다. 둘 다 BUNDLE# **한 아이템**을 원자적으로
갱신하고 갱신 후 전체를 받는다. 따라서 나중에 온 쪽은 반드시 먼저 온 쪽의 기록을 본다.
    판정 먼저  → 수집기가 frame_uuids 에 ADD (도착 속성 없음 → PENDING 유지)
                 조정기가 도착 SET → 받은 frame_uuids 전부 UPLOADED
    번들 먼저  → 조정기가 도착 SET (frame_uuids 없음 → 할 일 없음)
                 수집기가 ADD → 도착 속성이 보이므로 자기 판정을 UPLOADED
전이는 조건부(state == PENDING)라 양쪽이 같은 프레임을 옮기려 해도 한 번만 일어난다.
"""
from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for _p in (os.path.join(_ROOT, "schema"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import verdict as V   # noqa: E402

META = "META"


def table_name() -> str:
    """테이블 이름의 단일 소스는 schema/ddb_table.json 이다."""
    import json
    with open(os.path.join(_ROOT, "schema", "ddb_table.json"), encoding="utf-8") as f:
        return json.load(f)["TableName"]


def utc_now() -> str:
    """클라우드 벽시계. 차량 시각(모의 시각의 UTC 환산)과 섞지 않는다 — 속성 이름으로 가른다."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ─────────────────────────────────────────────────────────── 키
def frame_pk(frame_uuid: str) -> str:
    return f"FRAME#{frame_uuid}"


def bundle_pk(key: str) -> str:
    return f"BUNDLE#{key}"


def vin_pk(vin: str) -> str:
    return f"VIN#{vin}"


def state_gsi1pk(vin: str, state: V.FrameState) -> str:
    return f"VIN#{vin}#STATE#{state.value}"


# ─────────────────────────────────────────────────────────── 테이블 구현
class ConditionFailed(Exception):
    pass


class MemoryTable:
    """단위 테스트용. DynamoDB 가 보장하는 것(아이템 단위 원자성·조건부 쓰기)만 흉내 낸다."""

    def __init__(self):
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_new(self, item: dict[str, Any]) -> bool:
        k = (item["PK"], item["SK"])
        if k in self.items:
            return False
        self.items[k] = copy.deepcopy(item)
        return True

    def get(self, pk: str, sk: str) -> Optional[dict[str, Any]]:
        it = self.items.get((pk, sk))
        return copy.deepcopy(it) if it is not None else None

    def update(self, pk: str, sk: str, set_if_absent: dict[str, Any],
               add: Optional[dict[str, set[str]]] = None) -> dict[str, Any]:
        it = self.items.setdefault((pk, sk), {"PK": pk, "SK": sk})
        for k, v in set_if_absent.items():
            it.setdefault(k, copy.deepcopy(v))
        for k, vals in (add or {}).items():
            it.setdefault(k, set()).update(vals)
        return copy.deepcopy(it)

    def transition(self, pk: str, sk: str, src: str, updates: dict[str, Any]) -> bool:
        it = self.items.get((pk, sk))
        if it is None or it.get("state") != src:
            return False
        it.update(copy.deepcopy(updates))
        return True


def _to_ddb(v: Any) -> Any:
    """TypeSerializer 가 float 를 거부하므로 Decimal 로 바꾼다. 나머지는 그대로."""
    if isinstance(v, float):
        return Decimal(repr(v))
    if isinstance(v, dict):
        return {k: _to_ddb(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_to_ddb(x) for x in v]
    return v


def _from_ddb(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: _from_ddb(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_ddb(x) for x in v]
    return v


class DynamoTable:
    """DynamoDB(로컬·AWS 동일). 클라이언트는 `common.aws.dynamodb_client()` 에서 받는다."""

    def __init__(self, client, name: str):
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
        self.c, self.name = client, name
        self._ser, self._de = TypeSerializer(), TypeDeserializer()

    def _s(self, d: dict[str, Any]) -> dict[str, Any]:
        return {k: self._ser.serialize(_to_ddb(v)) for k, v in d.items()}

    def _d(self, d: dict[str, Any]) -> dict[str, Any]:
        return {k: _from_ddb(self._de.deserialize(v)) for k, v in d.items()}

    @staticmethod
    def _is_cond_fail(e: Exception) -> bool:
        return getattr(e, "response", {}).get("Error", {}).get("Code") == \
            "ConditionalCheckFailedException"

    def put_new(self, item: dict[str, Any]) -> bool:
        try:
            self.c.put_item(TableName=self.name, Item=self._s(item),
                            ConditionExpression="attribute_not_exists(PK)")
            return True
        except Exception as e:          # botocore ClientError — 조건 실패만 삼킨다
            if self._is_cond_fail(e):
                return False
            raise

    def get(self, pk: str, sk: str) -> Optional[dict[str, Any]]:
        r = self.c.get_item(TableName=self.name, Key=self._s({"PK": pk, "SK": sk}),
                            ConsistentRead=True)
        return self._d(r["Item"]) if "Item" in r else None

    def update(self, pk: str, sk: str, set_if_absent: dict[str, Any],
               add: Optional[dict[str, set[str]]] = None) -> dict[str, Any]:
        names, values, sets, adds = {}, {}, [], []
        for i, (k, v) in enumerate(set_if_absent.items()):
            names[f"#s{i}"] = k
            values[f":s{i}"] = v
            sets.append(f"#s{i} = if_not_exists(#s{i}, :s{i})")
        for i, (k, vals) in enumerate((add or {}).items()):
            names[f"#a{i}"] = k
            values[f":a{i}"] = set(vals)
            adds.append(f"#a{i} :a{i}")
        expr = " ".join(p for p in (
            ("SET " + ", ".join(sets)) if sets else "",
            ("ADD " + ", ".join(adds)) if adds else "") if p)
        r = self.c.update_item(TableName=self.name, Key=self._s({"PK": pk, "SK": sk}),
                               UpdateExpression=expr, ExpressionAttributeNames=names,
                               ExpressionAttributeValues=self._s(values),
                               ReturnValues="ALL_NEW")
        return self._d(r["Attributes"])

    def transition(self, pk: str, sk: str, src: str, updates: dict[str, Any]) -> bool:
        names = {"#state": "state"}
        values = {":src": src}
        sets = []
        for i, (k, v) in enumerate(updates.items()):
            names[f"#u{i}"] = k
            values[f":u{i}"] = v
            sets.append(f"#u{i} = :u{i}")
        try:
            self.c.update_item(TableName=self.name, Key=self._s({"PK": pk, "SK": sk}),
                               UpdateExpression="SET " + ", ".join(sets),
                               ConditionExpression="#state = :src",
                               ExpressionAttributeNames=names,
                               ExpressionAttributeValues=self._s(values))
            return True
        except Exception as e:
            if self._is_cond_fail(e):
                return False
            raise


# ─────────────────────────────────────────────────────────── 규약
class Store:
    def __init__(self, table):
        self.t = table

    # ── 프레임
    def put_verdict(self, v: V.Verdict, now: str) -> bool:
        """PENDING 으로 쓴다. 이미 있으면 쓰지 않는다(재전송 흡수) → False."""
        item = v.to_dict()
        st = V.FrameState.PENDING
        item.update(PK=frame_pk(v.frame_uuid), SK=META, state=st.value,
                    GSI1PK=state_gsi1pk(v.vin, st), GSI1SK=now, t_ingest_utc=now)
        return self.t.put_new(item)

    def put_dropped(self, frame_uuid: str, vin: str, boot_id: str, drive_session: int,
                    now: str) -> bool:
        """즉시 드롭된 선별 프레임. 판정이 없으므로 텔레메트리가 아는 것만 싣는다."""
        st = V.FrameState.DROPPED
        return self.t.put_new({
            "PK": frame_pk(frame_uuid), "SK": META, "frame_uuid": frame_uuid,
            "vin": vin, "boot_id": boot_id, "drive_session": drive_session,
            "state": st.value, "GSI1PK": state_gsi1pk(vin, st), "GSI1SK": now,
            "t_ingest_utc": now, "t_dropped_utc": now, "source": "telemetry"})

    def get_frame(self, frame_uuid: str) -> Optional[dict[str, Any]]:
        return self.t.get(frame_pk(frame_uuid), META)

    def transition(self, frame_uuid: str, vin: str, src: V.FrameState, dst: V.FrameState,
                   now: str, extra: Optional[dict[str, Any]] = None) -> bool:
        """state == src 일 때만 dst 로. 이미 옮겨졌거나 아이템이 없으면 False (오류 아님)."""
        if not V.can_transition(src, dst):
            raise ValueError(f"허용되지 않은 전이 {src.value} → {dst.value}")
        upd = {"state": dst.value, "GSI1PK": state_gsi1pk(vin, dst),
               f"t_{dst.value.lower()}_utc": now}
        upd.update(extra or {})
        return self.t.transition(frame_pk(frame_uuid), META, src.value, upd)

    # ── 번들
    def bundle_add_frames(self, key: str, bucket: str, vin: str,
                          frame_uuids: Iterable[str]) -> dict[str, Any]:
        """판정 쪽 기록. 갱신 후 번들 아이템 전체를 돌려준다 — 도착 속성이 있으면 이미 왔다."""
        return self.t.update(bundle_pk(key), META, {"bucket": bucket, "key": key, "vin": vin},
                             add={"frame_uuids": set(frame_uuids)})

    def bundle_mark_arrived(self, key: str, bucket: str, arrival: dict[str, Any],
                            now: str) -> dict[str, Any]:
        """번들 쪽 기록(조정기). 재전송된 이벤트가 첫 도착 시각을 덮지 않는다(if_not_exists)."""
        attrs = {"bucket": bucket, "key": key, "t_arrived_utc": now}
        attrs.update(arrival)
        return self.t.update(bundle_pk(key), META, attrs)

    def get_bundle(self, key: str) -> Optional[dict[str, Any]]:
        return self.t.get(bundle_pk(key), META)

    def promote_uploaded(self, bundle: dict[str, Any], frame_uuids: Iterable[str],
                         now: str) -> int:
        """도착한 번들에 든 판정을 PENDING → UPLOADED. 수집기·조정기가 같은 규칙으로 부른다.

        `t_put_utc` 는 차량 시계(번들 메타)라 도착 지연 = t_put_utc − t_frame_utc 를 잴 수 있다.
        옮긴 수를 돌려준다. 이미 옮겨진 프레임은 세지 않는다.
        """
        extra = {"t_put_utc": bundle["t_put_utc"]} if "t_put_utc" in bundle else {}
        return sum(self.transition(fu, bundle["vin"], V.FrameState.PENDING,
                                   V.FrameState.UPLOADED, now, extra)
                   for fu in frame_uuids)

    # ── 텔레메트리
    def put_telemetry(self, tel: V.Telemetry, now: str) -> bool:
        from dataclasses import asdict
        item = asdict(tel)
        item.update(PK=vin_pk(tel.vin), SK=f"TEL#{tel.sent_at}#{tel.boot_id}", t_ingest_utc=now)
        return self.t.put_new(item)


def arrived(bundle: dict[str, Any]) -> bool:
    return "t_arrived_utc" in bundle
