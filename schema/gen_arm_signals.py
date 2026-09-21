# -*- coding: utf-8 -*-
"""arm_signals.json 생성 — 두 팔(STA_B · DYN_B)의 프레임별 가속기 산출을 스냅샷으로 고정한다.

무엇을 굳히는가
-------------------------------------------------------------------------------
판정 레코드(`schema/verdict.py`)의 가속기 산출 필드는 frames.bin 에 없다.

    tiles_scored   팔마다 다르다 — 이 프로젝트의 대조 그 자체 (STA_B 1.00 · DYN_B 1.85)
    tile_mask      채점한 타일의 비트맵 (타일 우선순위 순으로 상위 tiles_scored 개)
    value_score    need_profile 가치 함수 (`03_가치함수/value_score.py` 골든과 정수 일치)

tiles_scored 는 rev.9 `y4_select_axis2.py` 가 **numpy 난수로 순위 정합한 C_main** 에서
나오므로 표준 라이브러리로는 재현할 수 없다. 그래서 edge_contract.json 과 같은 방식으로
rev.9 코드를 **실제로 실행해** 값을 뽑고 JSON 으로 굳힌다. 재생기는 이 JSON 만 읽는다
(stdlib, rev.9 import 없음).

자기 검증 — 틀린 스냅샷을 쓰지 않는다
-------------------------------------------------------------------------------
1. 같은 입력으로 `y4.select()` 를 다시 돌려 **두 팔 × 3시드 선별이 rev.9 `.npy` 와
   바이트 단위로 같은지** 확인한다. 같지 않으면 운용점을 잘못 읽은 것이다.
2. value_score 는 **판정에 싣는 신호(frames.bin 레코드) + raw_sad** 로 계산한다 — 보드의
   curate.c 가 자기 레코드로 계산하는 것과 같은 입력이다. 그 값이 rev.9 interim 입력으로
   계산한 값과 **전 프레임 같은지** 확인한다.

   ⚠ frames.bin 의 conf_q8·det_count 는 interim 현재본(`conf_q8.npy`·`det_count.npy`)과
     다르다(일치율은 산출물 `_findings` 에 기록). mean_p·kinematic_u8 은 전부 같다.
     rev.9 는 동결이라 고치지 않는다. need_profile 이 w_conf = 0 이라 value_score 에는
     영향이 없고(검증 2 가 그것을 확인한다), 축 2 선별은 타일 YOLO 점수로 매겨 대조에도
     영향이 없다.
3. frames.bin 의 frame_id 가 전역 인덱스(= frame_index.csv 행 순서)와 같은지 확인한다.

운용점은 손으로 적지 않는다 — `selections_axis2/summary_mc.json` 의 `args` 에서 읽는다
(y4 가 산출물에 재현 명령을 각인해 둔 것을 그대로 쓴다).

사용
-------------------------------------------------------------------------------
    # 생성 — numpy 가 필요하다. rev.9 venv 로 돌린다 (이 프로젝트 .venv 에 numpy 를 넣지 않는다)
    C:/Users/minch/needslack_venv/Scripts/python schema/gen_arm_signals.py

    # 점검 — stdlib 만. rev.9 원천 파일의 sha 가 기록과 같은지만 본다. 파일을 쓰지 않는다
    python schema/gen_arm_signals.py --check

rev.9 경로가 다르면 REV9_ROOT 환경변수로 넘긴다.
"""
import hashlib
import io
import json
import os
import struct
import sys
from datetime import date

sys.dont_write_bytecode = True          # rev.9 는 동결이다. __pycache__ 도 남기지 않는다

_HERE = os.path.dirname(os.path.abspath(__file__))
REV9 = os.path.abspath(os.environ.get(
    "REV9_ROOT", os.path.join(_HERE, "..", "..", "rev.9")))
OUT = os.path.join(_HERE, "arm_signals.json")

ARMS = ("STA_B", "DYN_B")
SEEDS = (1, 2, 3)
#: 팔 → 회수 기전 (설계결정-0001 §1·§3-3). y4 의 n_tiles 는 창에서 L_pre 를 빼지 않으므로
#: DYN_B 는 k→∞ 선점형의 이상화이고, STA_B 는 고정 창의 정적 예약이다.
RECLAIM = {"DYN_B": "preempt_resume", "STA_B": "static_reserve"}

#: 기록할 rev.9 원천. --check 가 이 sha 들을 비교한다.
SOURCES = {
    "y4_select_axis2":  "04_중앙AI/y4_select_axis2.py",
    "summary_mc":       "04_중앙AI/selections_axis2/summary_mc.json",
    "need_profile":     "03_가치함수/need_profile.json",
    "value_score":      "03_가치함수/value_score.py",
    "tile_scores_yolo": "05_데이터셋/interim/tile_scores_yolo.npz",
    "raw_sad":          "05_데이터셋/interim/raw_sad.npy",
    "frames_bin_head":  "05_데이터셋/board/frames.bin",
}
for _a in ARMS:
    for _s in SEEDS:
        SOURCES[f"sel_{_a}_s{_s}"] = f"04_중앙AI/selections_axis2/{_a}_mc_rate0.10_s{_s}.npy"


def sha16(rel: str) -> str:
    """파일 sha256 앞 16자. frames.bin 은 560 MB 라 앞 16 MiB 만 본다(헤더·초반 레코드 변경 감지)."""
    p = os.path.join(REV9, rel)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        if rel.endswith("frames.bin"):
            h.update(f.read(16 << 20))
            h.update(str(os.path.getsize(p)).encode())
        else:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()[:16]


def profile_sha(profile: dict) -> str:
    """need_profile 식별자. **가중치만** 정규화 JSON 으로 해시한다 — `_comment` 같은
    주석을 고쳐도 같은 프로파일이어야 한다. PG `campaign.profile_sha` 와 같은 정의를 쓴다."""
    w = {k: v for k, v in profile.items() if not k.startswith("_")}
    canon = json.dumps(w, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def check() -> None:
    if not os.path.exists(OUT):
        sys.exit(f"FAIL  {os.path.basename(OUT)} 이 없다. --check 없이 생성하라.")
    cur = json.load(io.open(OUT, encoding="utf-8"))
    rec = cur["_source"]["sha256_16"]
    bad = [k for k, rel in SOURCES.items() if rec.get(k) != sha16(rel)]
    if bad:
        sys.exit(f"FAIL  rev.9 원천이 바뀌었다: {', '.join(bad)}. 재생성 여부를 먼저 정하라.")
    print(f"OK    rev.9 원천 {len(SOURCES)} 개 sha 일치 · 풀 {len(cur['pool_frame']):,} 프레임")


def generate() -> None:
    import numpy as np                                        # rev.9 venv 에만 있다

    for rel in ("04_중앙AI", "03_가치함수"):
        sys.path.insert(0, os.path.join(REV9, rel))
    import y4_select_axis2 as Y4                              # noqa: E402  (읽기 전용 실행)
    import value_score as VS                                  # noqa: E402

    summ = json.load(io.open(os.path.join(REV9, SOURCES["summary_mc"]), encoding="utf-8"))
    a = summ["args"]
    sig, _rows = Y4.load_signals()
    cm, tr = Y4.c_main_rank_matched(sig["det"], Y4.TRACES[a["load"]])
    d_rt = a["d_rt_arg"] if a["d_rt_arg"] is not None else float(np.percentile(tr, 99))
    win_dyn, win_sta = Y4.windows(cm, a["t_frame"], d_rt)
    mmac = summ["mmac_per_tile"]
    c_tile = mmac / a["gmacs_b"]                              # 레짐 B (깊이 제약)
    nt_map = {"DYN_B": Y4.n_tiles(win_dyn, c_tile), "STA_B": Y4.n_tiles(win_sta, c_tile)}
    order, _ = Y4.tile_priority(sig)
    pool = np.where(sig["split"] == "pool")[0]
    budget = int(round(len(pool) * a["rate"]))

    # ── 검증 0: 운용점이 산출물 요약과 같다
    assert order.tolist() == summ["tile_order"], (order.tolist(), summ["tile_order"])
    assert budget == summ["budget"] and len(pool) == summ["n_pool"]
    assert abs(c_tile - summ["regimes"]["B"]["c_tile_ms"]) < 1e-9

    # ── 검증 1: 두 팔 × 3시드 선별을 바이트 단위로 재현한다
    sel_sha = {}
    for arm in ARMS:
        for s in SEEDS:
            got = Y4.select(arm, sig, budget, pool, order, s, nt_map)
            ref = np.load(os.path.join(REV9, SOURCES[f"sel_{arm}_s{s}"]))
            if not np.array_equal(got, ref) or got.dtype != ref.dtype:
                sys.exit(f"FAIL  {arm} s{s}: 선별 재현 실패 — 운용점을 잘못 읽었다")
        sel_sha[arm] = sha16(SOURCES[f"sel_{arm}_s1"])
        rec = summ["conditions"][arm]
        hist = [int((nt_map[arm][pool] == k).sum()) for k in range(5)]
        assert hist == rec["tile_hist"], (arm, hist, rec["tile_hist"])

    # ── 검증 2·3: frames.bin 레코드가 value_score 입력·전역 인덱스와 같다
    fb = os.path.join(REV9, SOURCES["frames_bin_head"])
    interim = os.path.join(REV9, "05_데이터셋", "interim")
    conf_q8 = np.load(os.path.join(interim, "conf_q8.npy")).astype(np.int64)
    mean_p = np.load(os.path.join(interim, "mean_p.npy")).astype(np.int64)
    kine = np.load(os.path.join(interim, "kinematic_u8.npy")).astype(np.int64)
    det = np.load(os.path.join(interim, "det_count.npy")).astype(np.int64)
    raw_sad = np.load(os.path.join(interim, "raw_sad.npy")).astype(np.int64)
    prof = VS.load_profile()
    vscore, mism = [], {"conf_q8": 0, "mean_p": 0, "kinematic_u8": 0, "det_count": 0}
    with open(fb, "rb") as f:
        _m, _v, n, w, h, _r = struct.unpack("<4sHIHH2s", f.read(16))
        stride = 12 + w * h
        for i in pool.tolist():
            f.seek(16 + i * stride)
            fid, _ts, cq, mp, kn, dc = struct.unpack("<II4B", f.read(12))
            if fid != i:
                sys.exit(f"FAIL  frames.bin[{i}].frame_id = {fid} (전역 인덱스와 다르다)")
            for k, got, ref in (("conf_q8", cq, conf_q8[i]), ("mean_p", mp, mean_p[i]),
                                ("kinematic_u8", kn, kine[i]), ("det_count", dc, det[i])):
                mism[k] += int(got != ref)
            v_rec = VS.value_score(cq, raw_sad[i], mp, kn, dc, prof)          # 판정 신호로
            v_int = VS.value_score(conf_q8[i], raw_sad[i], mean_p[i], kine[i], det[i], prof)
            if v_rec != v_int:
                sys.exit(f"FAIL  frame {i}: value_score 가 입력 출처에 따라 다르다 "
                         f"(frames.bin {v_rec} · interim {v_int}). 신호 불일치가 점수에 닿았다")
            vscore.append(v_rec)

    def mask(nt_row: int) -> int:
        return sum(1 << int(t) for t in order[:nt_row])

    y4_sha = sha16(SOURCES["y4_select_axis2"])
    doc = {
        "_schema": "needslack.arm_signals/1",
        "_generated": str(date.today()),
        "_generator": "schema/gen_arm_signals.py",
        "_warning": "손으로 고치지 말 것. rev.9 를 고치고 생성기를 다시 돌려라.",
        "_source": {
            "repo": "rev.9",
            "repro_cmd": summ["repro_cmd"],
            "sha256_16": {k: sha16(rel) for k, rel in SOURCES.items()},
        },
        "operating_point": {
            "load": a["load"], "t_frame_ms": a["t_frame"], "gmacs": a["gmacs_b"],
            "c_tile_ms": c_tile, "d_rt_ms": d_rt, "rate": a["rate"],
            "budget": budget, "tile_order": order.tolist(),
            "note": "레짐 B (깊이 제약). STA_B·DYN_B 는 이 운용점의 두 팔이다",
        },
        "profile": {"sha": profile_sha(prof), "weights": prof},
        "arms": {arm: {
            "reclaim_mode": RECLAIM[arm],
            #: 비트스트림이 아니라 y4 파이썬 슬랙 모형이 선별을 만들었다 — 그 사실을 식별자에 남긴다
            "rtl_build": f"y4sim-{y4_sha[:8]}",
            "selection_file": os.path.basename(SOURCES[f"sel_{arm}_s1"]),
            "selection_sha16": sel_sha[arm],
            "mean_tiles": float(nt_map[arm][pool].mean()),
            "tile_hist": [int((nt_map[arm][pool] == k).sum()) for k in range(5)],
        } for arm in ARMS},
        "_findings": {
            "frames_bin_vs_interim_mismatch": {
                k: {"frames": v, "of": int(len(pool)), "rate": round(v / len(pool), 4)}
                for k, v in mism.items()},
            "note": "frames.bin 의 conf_q8·det_count 가 rev.9 interim 현재본과 다르다. "
                    "판정에는 frames.bin(보드 레코드)을 싣는다. w_conf=0 이라 value_score 는 "
                    "불변(자기검증), 축 2 선별은 타일 YOLO 점수라 대조와 무관",
        },
        "_selfcheck": {
            "selections_reproduced": [f"{a_}_s{s}" for a_ in ARMS for s in SEEDS],
            "value_score_independent_of_signal_source": True,
            "frame_id_is_global_index": True,
            "passed": True,
        },
        # ── 열 단위 배열. i 번째 원소는 pool_frame[i] 프레임의 값이다
        "pool_frame": pool.tolist(),
        "value_score": [int(v) for v in vscore],
        "tiles_scored": {arm: [int(x) for x in nt_map[arm][pool]] for arm in ARMS},
        "tile_mask": {arm: [mask(int(x)) for x in nt_map[arm][pool]] for arm in ARMS},
    }

    # 메타는 들여쓰고, 긴 배열은 한 줄로 둔다 (diff 가 읽히게)
    arrays = ("pool_frame", "value_score", "tiles_scored", "tile_mask")
    head = {k: v for k, v in doc.items() if k not in arrays}
    body = json.dumps(head, ensure_ascii=False, indent=2)[:-2]
    for k in arrays:
        body += f',\n  "{k}": ' + json.dumps(doc[k], separators=(",", ":"))
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(body + "\n}\n")

    print(f"저장: {OUT}")
    print(f"  풀 {len(pool):,} · 예산 {budget:,} · C_TILE {c_tile:.3f} ms · 타일순서 {order.tolist()}")
    for arm in ARMS:
        r = doc["arms"][arm]
        print(f"  {arm}: 평균 타일 {r['mean_tiles']:.4f} · 분포 {r['tile_hist']} · {r['reclaim_mode']}")
    print(f"  profile_sha {doc['profile']['sha']} · value_score 범위 "
          f"[{min(vscore)}, {max(vscore)}]")
    print(f"  발견: frames.bin ↔ interim 불일치 " + " · ".join(
        f"{k} {v:,}" for k, v in mism.items()) + f" (/{len(pool):,})")
    print("  자기검증 통과: 두 팔 × 3시드 선별 재현 · value_score 입력 출처 무관 · frame_id = 전역 인덱스")


if __name__ == "__main__":
    check() if "--check" in sys.argv else generate()
