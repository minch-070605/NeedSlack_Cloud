# -*- coding: utf-8 -*-
"""compare.py — M3. Colab 이 낸 `m3_eval.json` 을 검증하고, 계보에 등록하고, 오프라인과 대조한다.

    python run.py compare                   # data/m3/m3_eval.json
    python run.py compare <경로>

왜 여기서 CI 를 다시 계산하지 않나
-------------------------------------------------------------------------------
부트스트랩(held-out 프레임 × 학습 시드, 400회)은 numpy 가 있어야 하는 계산량이고, 로컬 `.venv` 에는
numpy 가 없다(구현설계_MVP.md §8 — 학습·평가는 Colab 에서만). 노트북이 rev.9 y5 와 같은 절차·같은 rng
시드로 계산해 온 값을 쓴다. 여기서 하는 일은 그 값이 **어느 데이터·어느 코드·어느 인자에서 나왔는지**를
확인하고 계보에 묶는 것이다.

멈추는 조건 — 하나라도 어기면 등록하지 않는다
-------------------------------------------------------------------------------
1. 6런(2팔 × 3시드)이 다 있지 않다
2. 결과가 가리키는 tar sha256 이 스냅샷(`data/snapshots/*.sha256` · MinIO snapshot.json)과 다르다
3. 학습 인자 해시가 6런에서 하나가 아니다 · 고정 인자(epochs · batch · imgsz · SGD · mosaic 0 · amp 끔)와 다르다 (수용 기준 3)
4. held-out 식별자가 rev.9 frame_index 의 held_out 과 다르다 (수용 기준 7 의 평가 쪽)
5. 노트북에 실린 rev.9 코드의 sha256 이 지금 rev.9 파일과 다르다
6. 조건별 평균이 런별 mAP 와 맞지 않는다

⚠ 숫자가 오프라인과 달라도 **멈추지 않는다** — 그것이 결과다(PRD "실패 판정은 숫자가 안 나오는 것").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_HERE, os.path.join(ROOT, "edge-agent"), os.path.join(ROOT, "schema"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import verdict as V         # noqa: E402

#: Colab 결과를 내려받는 자리. 폴더째(`m3_results/`) 내려받아도 찾는다
IN_CANDIDATES = (os.path.join(ROOT, "data", "m3", "m3_eval.json"),
                 os.path.join(ROOT, "data", "m3_results", "m3_eval.json"))
OUT_DEFAULT = os.path.join(ROOT, "data", "m3", "compare.json")
RUNS = tuple(V.run_id(a, s) for a in V.ARMS for s in V.SEEDS)
#: 고정 인자 — 노트북 train.json 의 train_args(문자열 값)와 대조한다. 출처: rev.9 axis2 _mc args.yaml
LOCKED = {"epochs": "16", "batch": "32", "imgsz": "640", "optimizer": "SGD",
          "mosaic": "0.0", "amp": "False", "deterministic": "True", "workers": "2"}


class CompareError(RuntimeError):
    """멈추는 조건. 등록하지 않는다."""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ─────────────────────────────────────────────────────────── 1. 검증
def validate(m3: dict, tar_shas: dict[str, str], held_ids_sha: str, n_held: int,
             rev9_src_shas: dict[str, str]) -> list[tuple[str, str]]:
    """멈추는 조건 1–6. 통과한 항목 (이름, 근거) 목록을 돌려준다."""
    ok = []
    runs = m3.get("runs", {})
    missing = [r for r in RUNS if r not in runs]
    if missing:
        raise CompareError(f"런이 빠졌다: {missing} — 노트북을 다시 '모두 실행' 한다")
    ok.append(("6런", ", ".join(RUNS)))

    for name, want in tar_shas.items():
        if m3.get("tar_sha256", {}).get(name) != want:
            raise CompareError(f"{name} sha256 이 스냅샷과 다르다 — 다른 데이터로 학습했다")
    ok.append(("tar sha256 = 스냅샷", f"{len(tar_shas)}개"))

    shas = {runs[r]["train_args_sha"] for r in RUNS}
    if len(shas) != 1:
        raise CompareError(f"학습 인자 해시가 런마다 다르다 {sorted(shas)} — 팔·시드 말고 다른 것이 달랐다")
    for r in RUNS:
        ta = runs[r]["train_args"]
        bad = {k: ta.get(k) for k, v in LOCKED.items() if str(ta.get(k)) != v}
        if bad:
            raise CompareError(f"{r}: 고정 인자와 다르다 {bad}")
        arm, seed = r.split("-s")
        if runs[r]["arm"] != arm or int(runs[r]["seed"]) != int(seed):
            raise CompareError(f"{r}: 결과 안의 arm/seed 가 이름과 다르다")
    ok.append(("학습 인자 해시 6런 동일", next(iter(shas))))

    if m3.get("n_held_out") != n_held or m3.get("held_out_ids_sha256") != held_ids_sha:
        raise CompareError("held-out 식별자가 rev.9 frame_index 와 다르다")
    ok.append(("held-out 식별자", f"{n_held:,} 프레임"))

    got = m3.get("provenance", {}).get("rev9_sources_sha256", {})
    for name, want in rev9_src_shas.items():
        if got.get(name) != want:
            raise CompareError(f"노트북에 실린 {name} 이 지금 rev.9 와 다르다")
    ok.append(("rev.9 학습·평가 코드 sha256", ", ".join(sorted(rev9_src_shas))))

    for a in V.ARMS:
        per = [runs[V.run_id(a, s)]["mAP50"] for s in V.SEEDS]
        c = m3["conditions"][a]
        if any(abs(x - y) > 1e-12 for x, y in zip(per, c["per_seed"])) or \
                abs(sum(per) / len(per) - c["mean"]) > 1e-12:
            raise CompareError(f"{a}: 조건 평균이 런별 mAP 와 맞지 않는다")
    ok.append(("조건 평균 = 런별 mAP", "두 팔"))
    return ok


# ─────────────────────────────────────────────────────────── 2. 대조
def summarize(m3: dict) -> dict:
    """파이프라인 결과와 오프라인(rev.9) 을 나란히. 판정은 내리지 않고 사실만 모은다."""
    off = m3["offline"]
    e, oe = m3["DYN_B_vs_STA_B"], off["DYN_B_vs_STA_B"]
    arms = {}
    for a in V.ARMS:
        c, o = m3["conditions"][a], off[a]
        arms[a] = {"pipeline": c, "offline": {k: o[k] for k in ("per_seed", "mean", "sd")},
                   "per_seed_diff": [p - q for p, q in zip(c["per_seed"], o["per_seed"])]}
    lo, hi = e["ci"]
    olo, ohi = oe["ci"]
    return {
        "arms": arms,
        "delta": {"pipeline": e, "offline": {k: oe[k] for k in ("delta", "ci", "relative_pct")}},
        "facts": {
            "same_sign": (e["delta"] > 0) == (oe["delta"] > 0),
            "pipeline_ci_excludes_zero": lo > 0 or hi < 0,
            "pipeline_delta_in_offline_ci": olo <= e["delta"] <= ohi,
            "offline_delta_in_pipeline_ci": lo <= oe["delta"] <= hi,
            "cis_overlap": lo <= ohi and olo <= hi,
            "delta_diff": e["delta"] - oe["delta"],
        },
        "n_held_out": m3["n_held_out"], "boot": m3["boot"], "env": m3.get("env", {}),
    }


def render(summary: dict, checks: list[tuple[str, str]], reg: str) -> str:
    L = ["검증 — 전부 통과"] + [f"  ✓ {k:28s} {v}" for k, v in checks] + ["", f"계보 {reg}", ""]
    L.append(f"{'':8s}{'파이프라인 (mean ± sd)':>26s}{'오프라인 rev.9':>22s}{'시드별 차이':>24s}")
    for a, d in summary["arms"].items():
        p, o = d["pipeline"], d["offline"]
        diff = " ".join(f"{x:+.4f}" for x in d["per_seed_diff"])
        L.append(f"{a:8s}{p['mean']:>16.4f} ± {p['sd']:.4f}{o['mean']:>14.4f} ± {o['sd']:.4f}   {diff}")
    e, oe = summary["delta"]["pipeline"], summary["delta"]["offline"]
    L.append(f"{'Δ mAP50':8s}{e['delta']:>+16.4f}{'':9s}{oe['delta']:>+14.4f}")
    L.append(f"{'95% CI':8s}   [{e['ci'][0]:+.4f}, {e['ci'][1]:+.4f}]      [{oe['ci'][0]:+.4f}, {oe['ci'][1]:+.4f}]")
    f = summary["facts"]
    L += ["", "사실 (판정이 아니다 — PRD: 숫자가 달라도 그것이 결과다)",
          f"  부호가 같다                     {f['same_sign']}",
          f"  파이프라인 CI 가 0 을 배제한다   {f['pipeline_ci_excludes_zero']}",
          f"  파이프라인 Δ 가 오프라인 CI 안   {f['pipeline_delta_in_offline_ci']}",
          f"  두 CI 가 겹친다                 {f['cis_overlap']}",
          f"  Δ 차이 (파이프라인 − 오프라인)   {f['delta_diff']:+.4f}"]
    return "\n".join(L)


# ─────────────────────────────────────────────────────────── 3. 계보
def register(conn, m3: dict, ds_ids: Optional[dict[str, str]] = None, run_prefix: str = "",
             commit: bool = True) -> str:
    """model_run · eval_result. 이미 있으면 같은지 확인만 한다 → 'new' | 'same'.

    `ds_ids` · `run_prefix` · `commit` 은 테스트용이다 — 롤백 트랜잭션 안에서 실제 스키마를 검사한다.
    """
    ds_ids = ds_ids or {a: V.dataset_id(a) for a in V.ARMS}
    state = "new"
    with conn.cursor() as cur:
        for r in RUNS:
            run = m3["runs"][r]
            ta = run["train_args"]
            rid = run_prefix + r
            row = (rid, ds_ids[run["arm"]], int(ta["epochs"]), int(ta["batch"]), int(ta["imgsz"]),
                   int(run["seed"]), ta["optimizer"], float(ta["mosaic"]), ta["amp"] == "True",
                   run["train_args_sha"], run.get("weights_uri"))
            cur.execute("""SELECT ds_id, epochs, batch, imgsz, seed, optimizer, mosaic, amp, train_args_sha
                             FROM model_run WHERE run_id = %s""", (rid,))
            have = cur.fetchone()
            if have is not None:
                if (have[0], have[1], have[2], have[3], have[4], have[5], float(have[6]), have[7], have[8]) \
                        != row[1:10]:
                    raise CompareError(f"{rid} 는 이미 다른 인자로 등록돼 있다 — 사후에 런을 바꾸지 않는다")
                cur.execute("SELECT value, n_held_out FROM eval_result WHERE run_id = %s AND metric = 'mAP50'",
                            (rid,))
                ev = cur.fetchone()
                if ev is None or abs(ev[0] - run["mAP50"]) > 1e-12 or ev[1] != m3["n_held_out"]:
                    raise CompareError(f"{rid} 의 평가가 이미 다른 값으로 등록돼 있다")
                state = "same"
                continue
            cur.execute("""INSERT INTO model_run (run_id, ds_id, epochs, batch, imgsz, seed, optimizer,
                                                  mosaic, amp, train_args_sha, weights_uri)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", row)
            cur.execute("""INSERT INTO eval_result (run_id, metric, value, n_held_out)
                           VALUES (%s, 'mAP50', %s, %s)""", (rid, run["mAP50"], m3["n_held_out"]))
    if commit:
        conn.commit()
    return state


# ─────────────────────────────────────────────────────────── 실행
def local_expectations() -> tuple[dict[str, str], str, int, dict[str, str]]:
    """검증에 쓸 기대값 — 스냅샷 tar sha256 · held-out 식별자 · rev.9 코드 sha256."""
    import build_colab_nb as NB
    import snapshot as SN
    import replay
    r9 = SN.Rev9(replay.rev9_root())
    splits, tune = r9.splits()
    _, held = SN.eval_sets(splits, tune)
    cz = os.path.join(r9.root, "04_중앙AI")
    src = {n: sha256(NB.read(os.path.join(cz, n)).encode("utf-8")) for n in ("yolo_detector.py", "apeval.py")}
    return NB.expected_shas(), sha256(",".join(map(str, held)).encode()), len(held), src


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="M3 결과 검증 · 계보 등록 · 오프라인 대조")
    ap.add_argument("path", nargs="?", default=None)
    a = ap.parse_args(argv)
    a.path = a.path or next((p for p in IN_CANDIDATES if os.path.exists(p)), IN_CANDIDATES[0])
    if not os.path.exists(a.path):
        raise SystemExit(f"{a.path} 가 없다 — Colab 의 m3_results/m3_eval.json 을 내려받아 둔다")
    with open(a.path, encoding="utf-8") as f:
        m3 = json.load(f)

    tar_shas, held_sha, n_held, src = local_expectations()
    checks = validate(m3, tar_shas, held_sha, n_held, src)
    summary = summarize(m3)

    import psycopg
    with psycopg.connect(os.environ["PG_DSN"]) as conn:
        reg = register(conn, m3)
    summary["checks"] = checks
    summary["registered"] = reg
    os.makedirs(os.path.dirname(OUT_DEFAULT), exist_ok=True)
    with open(OUT_DEFAULT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(render(summary, checks, reg))
    print(f"\n저장 {OUT_DEFAULT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
