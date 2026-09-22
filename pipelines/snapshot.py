# -*- coding: utf-8 -*-
"""snapshot.py — M2. INDEXED 판정 → 불변 데이터셋 스냅샷 (tar · manifest · PostgreSQL 계보).

    python run.py snapshot common          # eval-common.tar — val 800 · held-out · ybase (한 번)
    python run.py snapshot STA_B           # ds-STA_B.tar + 계보 등록 + IN_DATASET
    python run.py snapshot DYN_B

형식과 근거는 `docs/설계결정-0002-M3-학습데이터-이관.md`. 요약:

    ds-{ARM}.tar      images/train/{fid:05d}.jpg   ← MinIO 번들에서 잘라 sha256 재대조
                      labels/train/{fid:05d}.txt   ← rev.9 nuScenes 정답
                      lists/train.txt · manifest.jsonl · snapshot.json
    eval-common.tar   images|labels/{val,held_out}/ · weights/ybase_last.pt · lists/ · eval_common.json

멈추는 조건 — 하나라도 어기면 아무것도 쓰지 않는다
-------------------------------------------------------------------------------
1. INDEXED 프레임 집합이 rev.9 정본 선별(`selections_axis2/{ARM}_mc_rate0.10_s*.npy`, 세 시드 동일)과
   다르다 — 파이프라인이 대조를 바꿨다는 뜻이다(수용 기준 3 의 데이터 쪽 절반)
2. rev.9 가 실제로 학습에 쓴 목록(`lists/axis2_{ARM}_mc_s1_train.txt`)과 다르다
3. 학습 프레임이 held-out 과 겹친다 · pool 밖이다(수용 기준 7)
4. 한 스냅샷에 회수 기전·프로파일이 둘 이상 섞였다(설계결정-0001 §4)
5. 번들에서 자른 바이트의 sha256 이 판정의 값 · rev.9 원본과 다르다

불변성 — 같은 스냅샷을 두 번 만들어도 같다
-------------------------------------------------------------------------------
tar 는 항목 순서·mtime·소유자를 고정해 **바이트 단위로 결정적**이다. 이미 등록된 데이터셋은 다시 만들 때
프레임 집합과 sha256 이 같아야 하고, 다르면 멈춘다 — 동결된 데이터셋은 고치지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tarfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (os.path.join(ROOT, "edge-agent"), os.path.join(ROOT, "ingest"),
           os.path.join(ROOT, "schema"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import verdict as V         # noqa: E402

#: rev.9 y5_train_eval_axis2.py — `tune_val = ids_where(rows, split="tune")[:800]`
N_VAL = 800
OUT_DEFAULT = os.path.join(ROOT, "data", "snapshots")
DATASET_PREFIX = "datasets"


class SnapshotError(RuntimeError):
    """멈추는 조건(머리말). 스냅샷을 만들지 않는다."""


# ─────────────────────────────────────────────────────────── rev.9 고정 자산
@dataclass(frozen=True)
class Rev9:
    root: str

    @property
    def images(self) -> str:
        return os.path.join(self.root, "05_데이터셋", "yolo_ds", "images", "all")

    @property
    def labels(self) -> str:
        return os.path.join(self.root, "05_데이터셋", "yolo_ds", "labels", "all")

    @property
    def lists(self) -> str:
        return os.path.join(self.root, "05_데이터셋", "yolo_ds", "lists")

    @property
    def frame_index(self) -> str:
        return os.path.join(self.root, "05_데이터셋", "splits", "frame_index.csv")

    @property
    def selections(self) -> str:
        return os.path.join(self.root, "04_중앙AI", "selections_axis2")

    @property
    def base_weights(self) -> str:
        return os.path.join(self.root, "04_중앙AI", "runs_yolo", "ybase", "weights", "last.pt")

    def image(self, fid: int) -> bytes:
        with open(os.path.join(self.images, f"{fid:05d}.jpg"), "rb") as f:
            return f.read()

    def label(self, fid: int) -> bytes:
        with open(os.path.join(self.labels, f"{fid:05d}.txt"), "rb") as f:
            return f.read()

    def splits(self) -> tuple[dict[int, str], list[int]]:
        """frame_id → split, 그리고 파일 순서 그대로의 tune 목록 (val = 앞 800)."""
        by, tune = {}, []
        with open(self.frame_index, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fid = int(r["frame_id"])
                by[fid] = r["split"]
                if r["split"] == "tune":
                    tune.append(fid)
        return by, tune

    def trained_list(self, arm: str) -> Optional[list[int]]:
        """rev.9 가 실제로 학습에 쓴 목록. 없으면 None."""
        p = os.path.join(self.lists, f"axis2_{arm}_mc_s1_train.txt")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return [int(os.path.splitext(os.path.basename(line.strip().replace("\\", "/")))[0])
                    for line in f if line.strip()]


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────── 1. 계획 — 검증
def plan(arm: str, frames: list[dict], selection: Iterable[int], splits: dict[int, str],
         trained: Optional[list[int]], profile_sha: str) -> list[dict]:
    """멈추는 조건 1–4 를 검사하고 manifest 행(frame_id 오름차순)을 돌려준다."""
    vin = V.vin_of(arm)
    sel = set(selection)
    if not frames:
        raise SnapshotError(f"{vin} 에 INDEXED 프레임이 없다 — 완주를 먼저 한다")
    fids = [int(f["frame_id"]) for f in frames]
    if len(set(fids)) != len(fids):
        raise SnapshotError(f"{vin}: 같은 frame_id 가 두 번 나온다 — 재생이 두 번 적재됐다")
    got = set(fids)
    if got != sel:
        raise SnapshotError(f"{vin}: INDEXED {len(got):,} 프레임이 rev.9 정본 선별 {len(sel):,} 과 다르다 "
                            f"(빠짐 {len(sel - got):,} · 더함 {len(got - sel):,})")
    if trained is not None and set(trained) != got:
        raise SnapshotError(f"{vin}: rev.9 학습 목록 axis2_{arm}_mc_s1_train.txt 와 다르다")
    held = sorted(f for f in got if splits.get(f) == "held_out")
    if held:
        raise SnapshotError(f"{vin}: 학습 프레임 {len(held):,} 개가 held-out 이다 (예: {held[:3]})")
    outside = sorted(f for f in got if splits.get(f) != "pool")
    if outside:
        raise SnapshotError(f"{vin}: pool 밖 프레임 {len(outside):,} 개 (예: {outside[:3]})")
    for field in ("reclaim_mode", "profile_sha", "vin"):
        vals = {f.get(field) for f in frames}
        if len(vals) != 1:
            raise SnapshotError(f"{vin}: {field} 가 섞였다 {sorted(map(str, vals))}")
    if frames[0]["profile_sha"] != profile_sha:
        raise SnapshotError(f"{vin}: profile_sha {frames[0]['profile_sha']} ≠ 스냅샷 {profile_sha}")
    rows = []
    for f in sorted(frames, key=lambda f: int(f["frame_id"])):
        p = f["payload"]
        rows.append({
            "frame_uuid": f["frame_uuid"], "frame_id": int(f["frame_id"]), "split": "pool",
            "reclaim_mode": f["reclaim_mode"], "tiles_scored": int(f["tiles_scored"]),
            "value_score": int(f["value_score"]), "t_frame_utc": f["t_frame_utc"],
            "arrival_latency_s": f.get("arrival_latency_s"),
            "bucket": p["bucket"], "key": p["key"], "offset": int(p["offset"]),
            "length": int(p["length"]), "sha256": p["sha256"],
            "image": f"images/train/{int(f['frame_id']):05d}.jpg",
            "label": f"labels/train/{int(f['frame_id']):05d}.txt"})
    return rows


# ─────────────────────────────────────────────────────────── 2. 추출 — 번들에서 자르기
def extract(rows: list[dict], get_bundle: Callable[[str, str], bytes],
            original: Optional[Callable[[int], bytes]] = None) -> dict[int, bytes]:
    """번들을 한 번씩만 받아 자른다. 멈추는 조건 5."""
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        by_key[(r["bucket"], r["key"])].append(r)
    out = {}
    for (bucket, key), rs in sorted(by_key.items()):
        body = get_bundle(bucket, key)
        for r in rs:
            chunk = body[r["offset"]:r["offset"] + r["length"]]
            if len(chunk) != r["length"] or sha256(chunk) != r["sha256"]:
                raise SnapshotError(f"frame {r['frame_id']}: 번들 {key} 에서 자른 바이트의 sha256 이 판정과 다르다")
            if original is not None and chunk != original(r["frame_id"]):
                raise SnapshotError(f"frame {r['frame_id']}: 번들의 바이트가 rev.9 원본과 다르다")
            out[r["frame_id"]] = chunk
    return out


# ─────────────────────────────────────────────────────────── 3. tar — 결정적
class DetTar:
    """항목 순서·mtime·소유자를 고정한 tar. 같은 입력이면 같은 바이트."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "wb")
        self._t = tarfile.open(fileobj=self._f, mode="w", format=tarfile.USTAR_FORMAT)

    def add(self, name: str, data: bytes) -> None:
        ti = tarfile.TarInfo(name)
        ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        self._t.addfile(ti, io.BytesIO(data))

    def close(self) -> str:
        self._t.close()
        self._f.close()
        return sha256_file(self.path)


def jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode("utf-8")


def write_arm_tar(path: str, arm: str, rows: list[dict], images: dict[int, bytes],
                  label: Callable[[int], bytes], meta: dict) -> tuple[str, dict]:
    """ds-{ARM}.tar. (tar sha256, snapshot.json 내용) 을 돌려준다."""
    top = V.dataset_id(arm)
    manifest = jsonl(rows)
    t = DetTar(path)
    for r in rows:
        t.add(f"{top}/{r['image']}", images[r["frame_id"]])
        t.add(f"{top}/{r['label']}", label(r["frame_id"]))
    t.add(f"{top}/lists/train.txt", "".join(r["image"] + "\n" for r in rows).encode())
    t.add(f"{top}/manifest.jsonl", manifest)
    snap = dict(meta, ds_id=top, arm=arm, vin=V.vin_of(arm), n_frames=len(rows),
                manifest_sha256=sha256(manifest))
    t.add(f"{top}/snapshot.json", json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True).encode())
    return t.close(), snap


def write_common_tar(path: str, val: list[int], held: list[int], image: Callable[[int], bytes],
                     label: Callable[[int], bytes], weights: bytes, meta: dict) -> tuple[str, dict]:
    top = "eval-common"
    t = DetTar(path)
    for split, ids in (("val", val), ("held_out", held)):
        for fid in ids:
            t.add(f"{top}/images/{split}/{fid:05d}.jpg", image(fid))
            t.add(f"{top}/labels/{split}/{fid:05d}.txt", label(fid))
        t.add(f"{top}/lists/{split}.txt", "".join(f"images/{split}/{fid:05d}.jpg\n" for fid in ids).encode())
    t.add(f"{top}/weights/ybase_last.pt", weights)
    info = dict(meta, n_val=len(val), n_held_out=len(held), weights_sha256=sha256(weights),
                val_ids_sha256=sha256(",".join(map(str, val)).encode()),
                held_out_ids_sha256=sha256(",".join(map(str, held)).encode()))
    t.add(f"{top}/eval_common.json", json.dumps(info, ensure_ascii=False, indent=1, sort_keys=True).encode())
    return t.close(), info


def eval_sets(splits: dict[int, str], tune: list[int]) -> tuple[list[int], list[int]]:
    """rev.9 y5 와 같은 정의 — val = tune 앞 800, held-out = split 이 held_out 인 전부."""
    return tune[:N_VAL], sorted(f for f, s in splits.items() if s == "held_out")


# ─────────────────────────────────────────────────────────── 4. 계보 — PostgreSQL
def register_pg(conn, arm: str, rows: list[dict], manifest_uri: str, profile: dict,
                select_rate: float, split_seed: int, parity_verified: bool,
                ds_id: Optional[str] = None, commit: bool = True) -> str:
    """campaign · dataset_version · dataset_frame. 이미 있으면 같은지 확인만 한다. → 'new' | 'same'.

    `ds_id` · `commit` 은 테스트용이다 — 롤백되는 트랜잭션 안에서 실제 스키마로 계보 질의를 검사한다.
    """
    ds_id = ds_id or V.dataset_id(arm)
    campaign_id = f"cmp-{profile['sha']}"
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO campaign (campaign_id, profile_json, profile_sha, select_rate,
                                             value_th, parity_verified)
                       VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (campaign_id) DO NOTHING""",
                    (campaign_id, json.dumps(profile["weights"]), profile["sha"], select_rate,
                     int(profile["weights"].get("VALUE_TH", 0)), parity_verified))
        cur.execute("SELECT n_frames, manifest_s3_uri FROM dataset_version WHERE ds_id = %s", (ds_id,))
        have = cur.fetchone()
        if have is not None:
            cur.execute("SELECT frame_uuid FROM dataset_frame WHERE ds_id = %s", (ds_id,))
            existing = {r[0] for r in cur.fetchall()}
            if existing != {r["frame_uuid"] for r in rows} or have[1] != manifest_uri:
                raise SnapshotError(f"{ds_id} 는 이미 다른 내용으로 동결돼 있다 — 동결된 데이터셋은 고치지 않는다")
            if commit:
                conn.commit()
            return "same"
        cur.execute("""INSERT INTO dataset_version (ds_id, arm, campaign_id, manifest_s3_uri, n_frames, split_seed)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (ds_id, arm, campaign_id, manifest_uri, len(rows), split_seed))
        cur.executemany("""INSERT INTO dataset_frame (ds_id, frame_uuid, frame_id, split, reclaim_mode)
                           VALUES (%s, %s, %s, %s, %s)""",
                        [(ds_id, r["frame_uuid"], r["frame_id"], r["split"], r["reclaim_mode"]) for r in rows])
    if commit:
        conn.commit()
    return "new"


def mark_in_dataset(store, arm: str, rows: list[dict], now: str) -> int:
    """INDEXED → IN_DATASET. 계보가 커밋된 뒤에 부른다 — 중간에 죽어도 다시 돌리면 마저 옮긴다."""
    vin, ds_id = V.vin_of(arm), V.dataset_id(arm)
    return sum(store.transition(r["frame_uuid"], vin, V.FrameState.INDEXED, V.FrameState.IN_DATASET,
                                now, {"ds_id": ds_id}) for r in rows)


# ─────────────────────────────────────────────────────────── 실행
def _contract_json() -> dict:
    with open(os.path.join(ROOT, "schema", "edge_contract.json"), encoding="utf-8") as f:
        return json.load(f)


def _arm_signals() -> dict:
    with open(os.path.join(ROOT, "schema", "arm_signals.json"), encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="M2 데이터셋 스냅샷")
    ap.add_argument("target", choices=("common",) + V.ARMS)
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args(argv)

    import npyread
    import replay
    from frames import FramesFile

    r9 = Rev9(replay.rev9_root())
    splits, tune = r9.splits()
    cj = _contract_json()
    split_seed = int(cj["constants"]["SPLIT_SEED"]["value"])
    os.makedirs(a.out, exist_ok=True)

    if a.target == "common":
        val, held = eval_sets(splits, tune)
        with open(r9.base_weights, "rb") as f:
            weights = f.read()
        path = os.path.join(a.out, "eval-common.tar")
        sha, info = write_common_tar(path, val, held, r9.image, r9.label, weights,
                                     {"split_seed": split_seed, "source": "rev.9 yolo_ds · ybase last.pt"})
        with open(path + ".sha256", "w", encoding="utf-8") as f:
            f.write(f"{sha}  eval-common.tar\n")
        print(f"eval-common.tar  val {info['n_val']:,} · held-out {info['n_held_out']:,} · "
              f"{os.path.getsize(path) / 1e6:,.1f} MB\n  sha256 {sha}\n  저장 {path}")
        return 0

    arm = a.target
    common_sha_path = os.path.join(a.out, "eval-common.tar.sha256")
    if not os.path.exists(common_sha_path):
        raise SystemExit("eval-common.tar 가 없다 — `python run.py snapshot common` 을 먼저 돌린다")
    eval_sha = open(common_sha_path, encoding="utf-8").read().split()[0]

    import store as S
    from common import aws
    st = S.Store(S.DynamoTable(aws.dynamodb_client(), S.table_name()))
    vin = V.vin_of(arm)
    frames = (st.frames_in_state(vin, V.FrameState.INDEXED)
              + st.frames_in_state(vin, V.FrameState.IN_DATASET))
    with FramesFile(os.path.join(r9.root, "05_데이터셋", "board", "frames.bin")) as ff:
        selection = npyread.load_canonical_selection(r9.selections, arm, ff.frame_count)
    sig = _arm_signals()
    rows = plan(arm, frames, selection, splits, r9.trained_list(arm), sig["profile"]["sha"])
    print(f"{vin}: {len(rows):,} 프레임 — rev.9 정본 선별 · 학습 목록과 일치 · held-out 과 겹침 0")

    s3 = aws.s3_client()
    images = extract(rows, lambda b, k: s3.get_object(Bucket=b, Key=k)["Body"].read(), r9.image)
    print(f"  번들 {len({r['key'] for r in rows}):,} 개에서 {len(images):,} 장을 잘라 sha256 · rev.9 원본과 대조")

    ds_id = V.dataset_id(arm)
    ds_bucket = aws.dataset_bucket()
    manifest_uri = f"s3://{ds_bucket}/{DATASET_PREFIX}/{ds_id}/manifest.jsonl"
    path = os.path.join(a.out, f"{ds_id}.tar")
    sha, snap = write_arm_tar(path, arm, rows, images, r9.label, {
        "split_seed": split_seed, "profile_sha": sig["profile"]["sha"],
        "reclaim_mode": rows[0]["reclaim_mode"], "eval_common_tar_sha256": eval_sha,
        "manifest_s3_uri": manifest_uri})
    snap["tar_sha256"] = sha
    with open(path + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{sha}  {ds_id}.tar\n")

    s3.put_object(Bucket=ds_bucket, Key=f"{DATASET_PREFIX}/{ds_id}/manifest.jsonl", Body=jsonl(rows))
    s3.put_object(Bucket=ds_bucket, Key=f"{DATASET_PREFIX}/{ds_id}/snapshot.json",
                  Body=json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True).encode())

    import psycopg
    # parity_verified: arm_signals.json 자기검증이 rev.9 value_score.py 와의 정수 일치와
    # 두 팔 × 3시드 선별 재현을 확인했다(schema/gen_arm_signals.py). 그 결과를 그대로 싣는다.
    with psycopg.connect(os.environ["PG_DSN"]) as conn:
        state = register_pg(conn, arm, rows, manifest_uri, sig["profile"],
                            float(cj["select_rate_inst"]), split_seed,
                            bool(sig.get("_selfcheck", {}).get("passed")))
    moved = mark_in_dataset(st, arm, rows, S.utc_now())
    print(f"  계보 {state} · IN_DATASET 로 옮김 {moved:,}\n  {ds_id}.tar {os.path.getsize(path) / 1e6:,.1f} MB"
          f"\n  sha256 {sha}\n  저장 {path}\n  manifest {manifest_uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
