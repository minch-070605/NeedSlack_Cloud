# -*- coding: utf-8 -*-
"""replay.py — 차량 시뮬레이터. 한 팔의 pool 후보 전량을 재생해 두 평면으로 내보낸다.

    python run.py replay DYN_B              # 로컬 스택으로 (MQTT + MinIO)
    python run.py replay DYN_B --dry-run    # 싱크 없이 계약만 돌린다 (스택 불필요)

무엇을 하는가 (구현설계_MVP.md §2-4)
-------------------------------------------------------------------------------
    frames.bin 레코드 ─┐
    선별 배열(.npy) ───┼─▶ 후보 24,491 전량을 f_cand 로 ─▶ 선별분만 업링크 큐(토큰버킷·즉시 드롭)
    arm_signals.json ─┘                                   │
                                                          ├─▶ 평면 1  판정  (MQTT, 1초 배치)
                                                          │           텔레메트리 (5분 집계 + 드롭 보고)
                                                          └─▶ 평면 2  페이로드 (S3 번들, 35프레임)

★ **선별분만 재생하지 않는다.** 그러면 토큰버킷·드롭·도착 지연이 한 번도 돌지 않는다
  (PRD 암묵지 3). 후보는 전부 흐르고, 판정 메시지는 선별분만 나간다 — 미선별 후보의
  정보는 텔레메트리 집계로 올린다(보고서 §1-2).

시간 — 모의 시간이 차량 시계다
-------------------------------------------------------------------------------
큐 거동은 `uplink.py` 의 모의 시간으로 계산한다. 가속 배수(`--speed`)는 **벽시계 페이싱만**
바꾸고 결과를 바꾸지 않는다. 차량이 내보내는 모든 시각(`t_frame_utc` · `sent_at` · 번들
메타데이터 `t-put-utc`)은 모의 시각을 UTC 로 환산한 값이다. 가속 재생에서 클라우드가
벽시계로 도착 지연을 재면 틀린다 — 이 시각들로 재야 한다.

페이로드 — 실물 JPEG
-------------------------------------------------------------------------------
페이로드는 rev.9 `yolo_ds/images/all/<frame_id:05d>.jpg` 실물이다. M3 데이터셋이 S3 에서
꺼낸 이미지로 만들어져야 경로가 증명된다. 큐는 실제 바이트 수를 과금한다
(계약 `S` = 144,500 B 는 이 이미지들의 실측 평균에서 왔다 — 보고서 부록).

번들 — 보고서 §1-2
-------------------------------------------------------------------------------
큐에 들어가는 순간 번들 번호와 오프셋이 정해진다(FIFO 라 순서가 보존된다). 그래서 판정은
수 초 만에 나가면서도 페이로드 위치(`payload.key/offset/length/sha256`)를 이미 담는다.
번들 객체는 **마지막 프레임이 큐를 빠져나가는 시각**에 PUT 된다 — 클라우드가 관측하는
도착 지연은 그 시각 기준이다.

드롭 — 텔레메트리로 보고
-------------------------------------------------------------------------------
즉시 드롭된 선별 프레임은 판정을 내지 않는다(판정 스키마의 `selected ⇒ payload` 불변식을
지킨다). 대신 텔레메트리 `dropped_frame_uuids` 로 알리고, 조정기가 DROPPED 를 기록한다.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_HERE, os.path.join(ROOT, "schema"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import armsignals                          # noqa: E402
import npyread                             # noqa: E402
import verdict as V                        # noqa: E402
from contract import EdgeContract          # noqa: E402
from contract import load as load_contract  # noqa: E402
from frames import FrameRecord, FramesFile  # noqa: E402
from uplink import UplinkQueue             # noqa: E402

# ── 설계 파라미터. 계약값이 아니므로 edge_contract.json 에 없다 — 출처를 적는다
BUNDLE_FRAMES = 35        # 보고서 §1-2 "5 MB · 35프레임" 번들 (PUT 을 35분의 1로)
BATCH_S = 1.0             # 보고서 §1-2 판정 1초 배치
TELEMETRY_S = 300.0       # 보고서 §1-2 집계 텔레메트리 5분 주기
SPEED_MAX = 12.5          # 구현설계_MVP.md §5-2 재생 가속 상한
PARK_STEP_S = 1.0         # 주차 배출 구간의 시간 전진 폭 (결과에 영향 없음 — 해상도만)
EPS = 1e-9


# ─────────────────────────────────────────────────────────── 싱크
class NullSink:
    """아무 데도 보내지 않고 센다. --dry-run 과 대용량 검증용."""

    def __init__(self):
        self.messages: dict[str, int] = {}
        self.objects: dict[str, int] = {}          # key → 바이트 수

    def publish(self, topic: str, payload: str) -> None:
        self.messages[topic] = self.messages.get(topic, 0) + 1

    def put_object(self, bucket: str, key: str, data: bytes, metadata: dict[str, str]) -> None:
        self.objects[key] = len(data)

    def close(self) -> None:
        pass


class MemorySink(NullSink):
    """보낸 것을 전부 들고 있는다. 단위 테스트용."""

    def __init__(self):
        super().__init__()
        self.published: list[tuple[str, str]] = []
        self.blobs: dict[str, tuple[bytes, dict[str, str]]] = {}

    def publish(self, topic: str, payload: str) -> None:
        super().publish(topic, payload)
        self.published.append((topic, payload))

    def put_object(self, bucket: str, key: str, data: bytes, metadata: dict[str, str]) -> None:
        super().put_object(bucket, key, data, metadata)
        self.blobs[key] = (bytes(data), dict(metadata))


class LiveSink:
    """로컬 스택(Mosquitto · MinIO) 또는 AWS. 접근은 전부 `common.aws` 를 통과한다."""

    def __init__(self, client_id: str):
        from common import aws
        self._s3 = aws.s3_client()
        self._mq = aws.mqtt_client(client_id)

    def publish(self, topic: str, payload: str) -> None:
        info = self._mq.publish(topic, payload, qos=1)
        info.wait_for_publish(timeout=30)
        if not info.is_published():
            raise RuntimeError(f"MQTT 발행 실패 rc={info.rc} topic={topic}")

    def put_object(self, bucket: str, key: str, data: bytes, metadata: dict[str, str]) -> None:
        self._s3.put_object(Bucket=bucket, Key=key, Body=data, Metadata=metadata)

    def close(self) -> None:
        self._mq.loop_stop()
        self._mq.disconnect()


# ─────────────────────────────────────────────────────────── 설정·보고
@dataclass
class ReplayConfig:
    arm: str
    boot_id: str
    t0_utc: datetime
    bucket: str = "ns-raw-payload"
    drive_session: int = 1
    speed: Optional[float] = None            # None = 페이싱 없음 (최대 속도)
    limit: Optional[int] = None              # 앞에서 N 후보만 (스모크 테스트)
    queue_capacity: Optional[int] = None     # None = 계약 Q_min. 테스트에서만 줄인다


@dataclass
class ReplayReport:
    arm: str
    vin: str
    boot_id: str
    candidates: int = 0
    selected: int = 0
    accepted: int = 0
    dropped: int = 0
    verdicts: int = 0
    verdict_messages: int = 0
    telemetry_messages: int = 0
    bundles: int = 0
    bytes_uploaded: int = 0
    payload_mean_bytes: float = 0.0
    contract_payload_bytes: int = 0
    peak_queue_bytes: int = 0
    contract_queue_bytes: float = 0.0
    max_latency_frame_s: float = 0.0        # 프레임이 큐를 빠져나간 시각 기준
    max_latency_bundle_s: float = 0.0       # 번들 PUT 시각 기준 = 클라우드가 관측하는 값
    contract_latency_s: float = 0.0
    t_drive_s: float = 0.0
    t_park_s: float = 0.0
    wall_s: float = 0.0

    @property
    def latency_within_contract(self) -> bool:
        return self.max_latency_bundle_s <= self.contract_latency_s

    def summary(self) -> str:
        mb = 1e6
        return "\n".join([
            f"  팔 {self.arm} · vin {self.vin} · boot {self.boot_id}",
            f"  후보 {self.candidates:,} · 선별 {self.selected:,} · 수락 {self.accepted:,} "
            f"· 드롭 {self.dropped:,}",
            f"  판정 {self.verdicts:,} 건 / 메시지 {self.verdict_messages:,} · "
            f"텔레메트리 {self.telemetry_messages:,} · 번들 {self.bundles:,} "
            f"({self.bytes_uploaded / mb:,.1f} MB)",
            f"  페이로드 평균 {self.payload_mean_bytes:,.0f} B (계약 S {self.contract_payload_bytes:,} B)",
            f"  큐 최대점유 {self.peak_queue_bytes / mb:,.1f} MB (계약 Q_min "
            f"{self.contract_queue_bytes / mb:,.0f} MB)",
            f"  도착지연 최대  프레임 {self.max_latency_frame_s:,.0f} s · 번들 "
            f"{self.max_latency_bundle_s:,.0f} s (계약 L_max {self.contract_latency_s:,.0f} s) "
            + ("OK" if self.latency_within_contract else "★ 초과"),
            f"  모의시간 주행 {self.t_drive_s:,.1f} s + 주차 배출 {self.t_park_s:,.1f} s "
            f"· 벽시계 {self.wall_s:,.1f} s",
        ])


def iso_utc(t0: datetime, t_s: float) -> str:
    return (t0 + timedelta(seconds=t_s)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class _Bundle:
    index: int
    key: str
    frames: list[int] = field(default_factory=list)
    shas: list[str] = field(default_factory=list)
    size: int = 0
    departed: int = 0
    sealed: bool = False
    put: bool = False
    last_depart_s: float = 0.0


# ─────────────────────────────────────────────────────────── 재생기
class Replayer:
    def __init__(self, cfg: ReplayConfig, contract: EdgeContract, signals: armsignals.ArmSignals,
                 selected: set[int], record_of: Callable[[int], FrameRecord],
                 payload_of: Callable[[int], bytes], sink,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if cfg.arm != signals.arm:
            raise ValueError(f"설정 팔 {cfg.arm} 과 신호 팔 {signals.arm} 이 다르다")
        if cfg.speed is not None and not 0 < cfg.speed <= SPEED_MAX:
            raise ValueError(f"speed={cfg.speed}. 범위 (0, {SPEED_MAX}] (구현설계 §5-2)")
        outside = [f for f in selected if f not in signals]
        if outside:
            raise ValueError(f"선별 {len(outside)} 건이 pool 밖이다 (예: {outside[:3]})")

        self.cfg, self.c, self.sig, self.sink = cfg, contract, signals, sink
        self.selected = selected
        self.record_of, self.payload_of = record_of, payload_of
        self.clock, self.sleep = clock, sleep
        self.vin = V.vin_of(cfg.arm)
        self.mode = V.ReclaimMode(signals.reclaim_mode)
        self.q = UplinkQueue(contract, cfg.queue_capacity)

        self._batch: list[V.Verdict] = []
        self._bundles: list[_Bundle] = []
        self._open: Optional[_Bundle] = None
        self._bundle_of: dict[int, int] = {}
        self._frame_t: dict[int, float] = {}
        self._dep_idx = 0
        self._next_batch = BATCH_S
        self._next_tel = TELEMETRY_S
        self._last_tel = 0.0
        self._phase = "drive"
        self._win = self._new_window()
        self._payload_bytes = 0
        self.r = ReplayReport(cfg.arm, self.vin, cfg.boot_id,
                              contract_payload_bytes=contract.payload_bytes,
                              contract_queue_bytes=contract.queue_min_bytes,
                              contract_latency_s=contract.max_arrival_latency_s)

    # ─────────────────────────────────────────── 시간
    def _pace(self, t_s: float) -> None:
        if self.cfg.speed is None:
            return
        delay = self._wall0 + t_s / self.cfg.speed - self.clock()
        if delay > 0:
            self.sleep(delay)

    def _drain_to(self, t_s: float) -> None:
        dt = t_s - self.q.sim_time_s
        if dt > 0:
            self.q.advance(dt)
            self._on_departures()
        self._pace(t_s)

    def _advance_to(self, t_s: float) -> None:
        """t 까지 시간을 흘리며, 그 사이의 배치·텔레메트리 경계를 순서대로 처리한다."""
        while True:
            nxt = min(self._next_batch, self._next_tel)
            if nxt > t_s + EPS:
                break
            self._drain_to(nxt)
            if abs(nxt - self._next_batch) <= EPS:
                self._flush_batch(nxt)
                self._next_batch += BATCH_S
            if abs(nxt - self._next_tel) <= EPS:
                self._emit_telemetry(nxt)
                self._next_tel += TELEMETRY_S
        self._drain_to(t_s)

    # ─────────────────────────────────────────── 번들
    def _assign(self, frame: int, n: int, sha: str) -> tuple[str, int]:
        if self._open is None:
            b = len(self._bundles)
            key = (f"vin={self.vin}/boot={self.cfg.boot_id}/"
                   f"session={self.cfg.drive_session:04d}/bundle-{b:05d}.bin")
            self._open = _Bundle(b, key)
            self._bundles.append(self._open)
        bd = self._open
        off = bd.size
        bd.frames.append(frame)
        bd.shas.append(sha)
        bd.size += n
        self._bundle_of[frame] = bd.index
        if len(bd.frames) >= BUNDLE_FRAMES:
            self._seal()
        return bd.key, off

    def _seal(self) -> None:
        if self._open is not None:
            self._open.sealed = True
            self._maybe_put(self._open)
            self._open = None

    def _on_departures(self) -> None:
        dep = self.q.departed
        while self._dep_idx < len(dep):
            p = dep[self._dep_idx]
            self._dep_idx += 1
            bd = self._bundles[self._bundle_of[p.frame_id]]
            bd.departed += 1
            bd.last_depart_s = max(bd.last_depart_s, p.t_depart_s)
            lat = p.t_depart_s - p.t_enqueue_s
            self.r.max_latency_frame_s = max(self.r.max_latency_frame_s, lat)
            self._maybe_put(bd)

    def _maybe_put(self, bd: _Bundle) -> None:
        """봉인됐고 모든 프레임이 링크를 빠져나갔으면 PUT. 그 전에는 올릴 수 없다."""
        if bd.put or not bd.sealed or bd.departed < len(bd.frames):
            return
        t_put = max(bd.last_depart_s, self.q.sim_time_s)
        parts = []
        for f, sha in zip(bd.frames, bd.shas):
            data = self.payload_of(f)
            if hashlib.sha256(data).hexdigest() != sha:
                raise RuntimeError(f"프레임 {f} 페이로드가 큐 투입 이후 바뀌었다")
            parts.append(data)
            self.r.max_latency_bundle_s = max(self.r.max_latency_bundle_s,
                                              t_put - self._frame_t[f])
        body = b"".join(parts)
        self.sink.put_object(self.cfg.bucket, bd.key, body, {
            "vin": self.vin, "boot-id": self.cfg.boot_id,
            "drive-session": str(self.cfg.drive_session),
            "n-frames": str(len(bd.frames)),
            "t-put-utc": iso_utc(self.cfg.t0_utc, t_put),
        })
        bd.put = True
        self.r.bundles += 1
        self.r.bytes_uploaded += len(body)

    # ─────────────────────────────────────────── 평면 1
    def _flush_batch(self, t_s: float) -> None:
        if not self._batch:
            return
        msg = V.VerdictBatch(self.vin, iso_utc(self.cfg.t0_utc, t_s), self._batch)
        self.sink.publish(V.topic_verdict(self.vin), msg.to_json())
        self.r.verdicts += len(self._batch)
        self.r.verdict_messages += 1
        self._batch = []

    @staticmethod
    def _new_window() -> dict:
        return {"candidates": 0, "selected": 0, "accepted": 0, "dropped": 0,
                "dropped_frame_uuids": [], "value_bucket_hist": {}, "tiles_hist": {}}

    def _emit_telemetry(self, t_s: float, final: bool = False) -> None:
        w, s = self._win, self.q.stats
        msg = V.Telemetry(
            vin=self.vin, boot_id=self.cfg.boot_id, drive_session=self.cfg.drive_session,
            sent_at=iso_utc(self.cfg.t0_utc, t_s), phase=self._phase,
            t_sim_s=round(t_s, 3), window_s=round(t_s - self._last_tel, 3),
            candidates=w["candidates"], selected=w["selected"], accepted=w["accepted"],
            dropped=w["dropped"], dropped_frame_uuids=w["dropped_frame_uuids"],
            value_bucket_hist=w["value_bucket_hist"], tiles_hist=w["tiles_hist"],
            queue_bytes=self.q.queued_bytes, peak_queue_bytes=s.peak_queue_bytes,
            candidates_total=self.r.candidates, accepted_total=s.accepted,
            dropped_total=s.dropped, bundles_uploaded_total=self.r.bundles,
            max_arrival_latency_s=round(self.r.max_latency_bundle_s, 3), final=final)
        self.sink.publish(V.topic_telemetry(self.vin), msg.to_json())
        self.r.telemetry_messages += 1
        self._last_tel = t_s
        self._win = self._new_window()

    # ─────────────────────────────────────────── 후보 한 장
    def _candidate(self, frame: int, t_s: float) -> None:
        rec = self.record_of(frame)
        if rec.frame_id != frame:
            raise ValueError(f"frames.bin[{frame}].frame_id = {rec.frame_id}")
        sg = self.sig[frame]
        w = self._win
        w["candidates"] += 1
        self.r.candidates += 1
        vb, tb = str(sg.value_score >> 5), str(sg.tiles_scored)
        w["value_bucket_hist"][vb] = w["value_bucket_hist"].get(vb, 0) + 1
        w["tiles_hist"][tb] = w["tiles_hist"].get(tb, 0) + 1
        if frame not in self.selected:
            return

        w["selected"] += 1
        self.r.selected += 1
        data = self.payload_of(frame)
        n, sha = len(data), hashlib.sha256(data).hexdigest()
        fu = V.frame_uuid(self.vin, self.cfg.boot_id, self.cfg.drive_session, rec.frame_id)
        if not self.q.offer(frame, n):
            w["dropped"] += 1
            w["dropped_frame_uuids"].append(fu)
            self.r.dropped += 1
            return

        self._frame_t[frame] = t_s
        self._payload_bytes += n
        w["accepted"] += 1
        self.r.accepted += 1
        key, off = self._assign(frame, n, sha)
        v = V.Verdict(
            vin=self.vin, boot_id=self.cfg.boot_id, drive_session=self.cfg.drive_session,
            frame_id=rec.frame_id, t_frame_mono_ms=rec.timestamp_ms,
            t_frame_utc=iso_utc(self.cfg.t0_utc, t_s),
            conf_q8=rec.conf_q8, mean_p=rec.mean_p, kinematic_u8=rec.kinematic_u8,
            det_count=rec.det_count,
            value_score=sg.value_score, tile_mask=sg.tile_mask, tiles_scored=sg.tiles_scored,
            reclaim_mode=self.mode, rtl_build=self.sig.rtl_build,
            granule_k=None, l_pre_cyc=0, resume_cnt=0,     # 구현설계 §2-1 "MVP 의 기전 필드"
            selected=True, profile_sha=self.sig.profile_sha,
            payload=V.PayloadRef(self.cfg.bucket, key, off, n, sha))
        v.validate()
        self._batch.append(v)

    # ─────────────────────────────────────────── 실행
    def run(self) -> ReplayReport:
        self._wall0 = self.clock()
        pool = self.sig.pool if self.cfg.limit is None else self.sig.pool[:self.cfg.limit]
        dt = self.c.frame_interval_s

        # 주행 — 후보 전량을 f_cand 로
        for k, frame in enumerate(pool):
            t = k * dt
            self._advance_to(t)
            self._candidate(frame, t)
        t_end = len(pool) * dt
        self._advance_to(t_end)
        self._flush_batch(t_end)
        self._seal()
        self.r.t_drive_s = t_end

        # 주차 — 링크가 살아 있다는 계약 가정(UPLINK_LINK_UP_WHEN_PARKED) 아래 큐를 비운다
        self._phase = "park"
        t = t_end
        while self.q.queued_bytes > 0 or not all(b.put for b in self._bundles):
            t += PARK_STEP_S
            self._advance_to(t)
        self.r.t_park_s = t - t_end
        self._emit_telemetry(t, final=True)

        self.r.peak_queue_bytes = self.q.stats.peak_queue_bytes
        self.r.payload_mean_bytes = (self._payload_bytes / self.r.accepted
                                     if self.r.accepted else 0.0)
        self.r.wall_s = self.clock() - self._wall0
        self._check_invariants(len(pool))
        return self.r

    def _check_invariants(self, n_pool: int) -> None:
        """어기면 재생기 버그다. 결과(지연 초과 등)와 달리 조용히 넘기지 않는다."""
        r = self.r
        assert r.candidates == n_pool, (r.candidates, n_pool)
        assert r.accepted + r.dropped == r.selected, (r.accepted, r.dropped, r.selected)
        assert r.verdicts == r.accepted, (r.verdicts, r.accepted)
        assert self.q.queued_bytes == 0 and all(b.put for b in self._bundles)
        assert r.bytes_uploaded == self._payload_bytes


# ─────────────────────────────────────────────────────────── 조립
def rev9_root() -> str:
    return os.path.abspath(os.environ.get("REV9_ROOT", os.path.join(ROOT, "..", "rev.9")))


def _sha16(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def build(arm: str, sink, speed: Optional[float], limit: Optional[int]) -> tuple[Replayer, FramesFile]:
    r9 = rev9_root()
    signals = armsignals.load(arm)
    frames = FramesFile(os.path.join(r9, "05_데이터셋", "board", "frames.bin"))
    sel_dir = os.path.join(r9, "04_중앙AI", "selections_axis2")

    # 선별이 스냅샷을 만든 그 파일인지, 세 시드가 여전히 같은지 확인한다
    sel_path = os.path.join(sel_dir, signals.selection_file)
    if _sha16(sel_path) != signals.selection_sha16:
        raise RuntimeError(f"{signals.selection_file} 이 arm_signals.json 생성 이후 바뀌었다. "
                           f"`schema/gen_arm_signals.py --check` 로 확인하라")
    selected = set(npyread.load_canonical_selection(sel_dir, arm, frames.frame_count))

    img_dir = os.path.join(r9, "05_데이터셋", "yolo_ds", "images", "all")

    def payload_of(frame: int) -> bytes:
        with open(os.path.join(img_dir, f"{frame:05d}.jpg"), "rb") as f:
            return f.read()

    cfg = ReplayConfig(arm=arm, boot_id=str(uuid.uuid4()),
                       t0_utc=datetime.now(timezone.utc).replace(tzinfo=None),
                       bucket=os.environ.get("S3_BUCKET", "ns-raw-payload"),
                       speed=speed, limit=limit)
    return Replayer(cfg, load_contract(), signals, selected, frames.record, payload_of, sink), frames


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="차량 시뮬레이터 — 한 팔의 pool 후보 전량 재생")
    ap.add_argument("arm", choices=V.ARMS)
    ap.add_argument("--dry-run", action="store_true",
                    help="싱크 없이 계약만 돌린다. 스택이 필요 없다")
    ap.add_argument("--speed", type=float, default=None,
                    help=f"재생 가속 배수 (0, {SPEED_MAX}]. 기본: live 는 REPLAY_SPEED, "
                         f"dry-run 은 페이싱 없음")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N 후보만 (스모크 테스트)")
    a = ap.parse_args(argv)

    speed = a.speed
    if speed is None and not a.dry_run:
        speed = float(os.environ.get("REPLAY_SPEED", "1.0"))

    sink = NullSink() if a.dry_run else None
    if sink is None:
        sink = LiveSink(client_id=f"{V.vin_of(a.arm)}-{uuid.uuid4().hex[:8]}")
    rp, frames = build(a.arm, sink, speed, a.limit)
    print(f"재생 시작  {a.arm} · {'dry-run' if a.dry_run else 'live'} · "
          f"speed {speed if speed else '페이싱 없음'} · boot {rp.cfg.boot_id}")
    print(f"  계약  {rp.c.describe()}")
    try:
        report = rp.run()
    finally:
        sink.close()
        frames.close()
    print("재생 완료")
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
