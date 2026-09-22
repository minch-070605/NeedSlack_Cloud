# -*- coding: utf-8 -*-
"""build_status.py — 진행 현황 페이지를 **산출물에서 굽는다** (손으로 쓴 수치 없음).

    python run.py status                     # docs/_build/status.html 생성
    python run.py status <출력경로>

무엇을 읽는가
-------------------------------------------------------------------------------
    00_README.md               현재 위치 한 줄 · "🔴 먼저" 항목 · "🟡 열린 질문"   (상태의 유일한 출처)
    schema/edge_contract.json  계약 상한 (Q_min · L_max · S)
    schema/arm_signals.json    팔별 타일 분포 · rev.9 발견
    edge-agent/replay.py       두 팔을 dry-run 해서 재생 실측을 **지금** 잰다 (약 10초)
    DynamoDB · MinIO (운영)     스택이 떠 있으면 팔별 상태·도착 지연·번들을 **지금** 읽는다.
                               내려가 있으면 그 절은 "측정 안 함"으로 굽는다
    rev.9 yolo_ds 이미지        페이로드 크기 분포
    git log · tests/           최근 커밋 · 테스트 통과 수
    파일 존재                   마일스톤 단계 완료 여부 (산출물이 있으면 완료)

상태를 여기에 적지 않는다 — README 가 유일한 출처다(CLAUDE.md §0). 이 스크립트는
README 와 산출물을 **보여 줄** 뿐이다. 렌더링 산출물(*.html)은 커밋하지 않는다.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import subprocess
import sys
import unittest
from datetime import date
from typing import Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(ROOT, "edge-agent"), os.path.join(ROOT, "schema"), ROOT):
    sys.path.insert(0, _p)

import replay as RP                                    # noqa: E402
import verdict as V                                    # noqa: E402
from contract import load as load_contract             # noqa: E402

OUT_DEFAULT = os.path.join(ROOT, "docs", "_build", "status.html")


# ─────────────────────────────────────────────────────────── 수집
def readme() -> tuple[str, str, list[str]]:
    s = io.open(os.path.join(ROOT, "00_README.md"), encoding="utf-8").read()
    pos = re.search(r"현재 위치:\s*(.+?)\*\*", s).group(1).strip()
    blk = re.search(r"### 🔴 먼저 — (.+?)\n(.*?)\n### ", s, re.S)
    head, body = blk.group(1).strip(), blk.group(2)
    items, cur = [], None
    for line in body.splitlines():
        m = re.match(r"^\d+\.\s+(.*)", line)
        if m:
            cur = [m.group(1)]
            items.append(cur)
        elif cur is not None and line.startswith("   ") and line.strip():
            cur.append(line.strip())
        elif not line.strip():
            cur = None
    return pos, head, [" ".join(i) for i in items]


def open_questions() -> list[str]:
    """README "### 🟡 열린 질문" 의 번호 항목. 없으면 빈 목록."""
    s = io.open(os.path.join(ROOT, "00_README.md"), encoding="utf-8").read()
    m = re.search(r"### 🟡 열린 질문.*?\n(.*?)\n(?:### |## |---)", s, re.S)
    if not m:
        return []
    items, cur = [], None
    for line in m.group(1).splitlines():
        mm = re.match(r"^\d+\.\s+(.*)", line)
        if mm:
            cur = [mm.group(1)]
            items.append(cur)
        elif cur is not None and line.startswith("   ") and line.strip():
            cur.append(line.strip())
    return [" ".join(i) for i in items]


def git_log(n: int = 8) -> list[tuple[str, str, str]]:
    out = subprocess.run(["git", "log", f"-{n}", "--format=%h%x09%ad%x09%s", "--date=short"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    return [tuple(l.split("\t", 2)) for l in out.strip().splitlines()]


def test_count() -> tuple[int, int]:
    suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"))
    res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    return res.testsRun, res.testsRun - len(res.failures) - len(res.errors)


def replay_all() -> dict[str, RP.ReplayReport]:
    out = {}
    for arm in ("STA_B", "DYN_B"):
        rp, frames = RP.build(arm, RP.NullSink(), speed=None, limit=None)
        try:
            out[arm] = rp.run()
        finally:
            frames.close()
    return out


def pipeline_live() -> Optional[dict]:
    """운영 테이블·버킷을 지금 읽는다. 스택이 없으면 None — 추측한 값을 굽지 않는다."""
    ep = os.environ.get("AWS_ENDPOINT_DYNAMODB")
    if not ep:
        return None
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return None
    sys.path.insert(0, os.path.join(ROOT, "ingest"))
    import store as S
    cfg = Config(connect_timeout=1, read_timeout=10, retries={"max_attempts": 1})
    region = os.environ.get("AWS_REGION", "ap-northeast-2")
    ddb = boto3.client("dynamodb", endpoint_url=ep, region_name=region, config=cfg)
    try:
        ddb.describe_table(TableName=S.table_name())
    except Exception:
        return None
    st = S.Store(S.DynamoTable(ddb, S.table_name()))
    bundles: dict[str, list[int]] = {}
    tel: dict[str, list[int]] = {}
    for page in ddb.get_paginator("scan").paginate(
            TableName=S.table_name(), ProjectionExpression="PK, vin, t_arrived_utc, #f",
            ExpressionAttributeNames={"#f": "final"},
            FilterExpression="begins_with(PK, :b) OR begins_with(PK, :v)",
            ExpressionAttributeValues={":b": {"S": "BUNDLE#"}, ":v": {"S": "VIN#"}}):
        for it in page["Items"]:
            vin = it.get("vin", {}).get("S", "")
            if it["PK"]["S"].startswith("BUNDLE#"):
                b = bundles.setdefault(vin, [0, 0])
                b[0] += 1
                b[1] += "t_arrived_utc" in it
            else:
                t = tel.setdefault(vin, [0, 0])
                t[0] += 1
                t[1] += it.get("final", {}).get("BOOL", False)
    s3 = boto3.client("s3", endpoint_url=os.environ.get("AWS_ENDPOINT_S3"), region_name=region, config=cfg)
    bucket = os.environ.get("S3_BUCKET", "ns-raw-payload")
    out = {"table": S.table_name(), "bucket": bucket, "arms": {}}
    for arm in V.ARMS:
        vin = V.vin_of(arm)
        states = {}
        lat: list[float] = []
        for fs in V.FrameState:
            items = st.frames_in_state(vin, fs)
            states[fs.value] = len(items)
            if fs in (V.FrameState.INDEXED, V.FrameState.IN_DATASET):   # 검증을 마친 프레임
                lat += [float(f["arrival_latency_s"]) for f in items if "arrival_latency_s" in f]
        lat.sort()
        n_obj = n_bytes = 0
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"vin={vin}/"):
            for o in page.get("Contents", []):
                n_obj += 1
                n_bytes += o["Size"]
        b, t = bundles.get(vin, [0, 0]), tel.get(vin, [0, 0])
        out["arms"][arm] = {"vin": vin, "states": states, "latency": lat,
                            "bundles": b[0], "bundles_arrived": b[1], "telemetry": t[0], "final": t[1],
                            "objects": n_obj, "bytes": n_bytes}
    return out


def run_checks(live_arm: dict, r: "RP.ReplayReport") -> list[tuple[str, str, bool]]:
    """운영 적재가 dry-run 재생 보고와 맞는가 — M1 완료 판정 (README §4)."""
    s, lat = live_arm["states"], live_arm["latency"]
    lat_max = lat[-1] if lat else 0.0
    done = s["INDEXED"] + s["IN_DATASET"]           # 검증 완료 — 스냅샷에 편입됐어도 같다
    return [
        ("판정 전부 검증 (INDEXED·IN_DATASET)", f"{n0(done)} / {n0(r.accepted)}", done == r.accepted),
        ("QUARANTINED 없음", n0(s["QUARANTINED"]), s["QUARANTINED"] == 0),
        ("대기(PENDING·UPLOADED) 없음", n0(s["PENDING"] + s["UPLOADED"]), s["PENDING"] + s["UPLOADED"] == 0),
        ("번들 전부 도착", f"{n0(live_arm['bundles_arrived'])} / {n0(r.bundles)}",
         live_arm["bundles_arrived"] == r.bundles == live_arm["objects"]),
        ("S3 바이트 = 재생 업로드", f"{live_arm['bytes'] / 1e6:,.1f} MB", live_arm["bytes"] == r.bytes_uploaded),
        ("도착 지연 최대 = 재생 보고", f"{lat_max:,.1f} s / {r.max_latency_bundle_s:,.1f} s",
         abs(lat_max - r.max_latency_bundle_s) < 1.0),
    ]


def arm_complete(live: Optional[dict], rep: dict, arm: str) -> bool:
    st = live["arms"][arm]["states"] if live else {}
    return bool(live and st["INDEXED"] + st["IN_DATASET"] and
                all(ok for _, _, ok in run_checks(live["arms"][arm], rep[arm])))


def payload_sizes(pool: list[int]) -> dict[str, float]:
    d = os.path.join(RP.rev9_root(), "05_데이터셋", "yolo_ds", "images", "all")
    n = len([f for f in os.listdir(d) if f.endswith(".jpg")])
    sz = [os.path.getsize(os.path.join(d, f"{i:05d}.jpg")) for i in range(n)]
    return {"all": sum(sz) / n, "pool": sum(sz[i] for i in pool) / len(pool), "n_all": n}


def milestones(runs: Optional[dict] = None) -> list[dict]:
    """단계 완료 = 그 단계의 산출물이 있다. 사람이 적은 체크 표시가 아니다.

    코드 단계는 파일 존재로, **완주** 단계는 운영 테이블이 재생 보고와 맞는지(`runs`)로 판정한다.
    `runs` 가 없으면(스택 내려감) 완주는 판정하지 않고 미완으로 둔다.
    """
    ex = lambda *p: os.path.exists(os.path.join(ROOT, *p))  # noqa: E731
    runs = runs or {}
    return [
        {"m": "M1", "goal": "한 팔이 로컬을 통과", "steps": [
            ("로컬 스택", ex("infra", "docker-compose.yml")),
            ("부트스트랩", ex("infra", "bootstrap", "01_create.py")),
            ("재생기", ex("edge-agent", "replay.py")),
            ("업링크 큐", ex("edge-agent", "uplink.py")),
            ("수집기", ex("ingest", "collector.py")),
            ("조정기", ex("ingest", "reconciler.py")),
            ("DYN_B 완주", runs.get("DYN_B", False)),
        ]},
        {"m": "M2", "goal": "계보와 스냅샷", "steps": [
            ("STA_B 완주", runs.get("STA_B", False)),
            ("PG 스키마", ex("schema", "pg", "001_init.sql")),
            ("스냅샷", ex("pipelines", "snapshot.py")),
            ("역추적 질의", ex("tests", "test_lineage.py")),
        ]},
        {"m": "M3", "goal": "두 팔 × 3시드 재현 — 결론", "steps": [
            ("Colab 학습", ex("pipelines", "colab_train.ipynb")),
            ("평가", ex("pipelines", "evaluate.py")),
            ("Δ 대조표", ex("pipelines", "compare.py")),
        ]},
        {"m": "M4", "goal": "AWS 수직 절단면", "steps": [
            ("IoT 정책", ex("infra", "aws")),
        ]},
    ]


# ─────────────────────────────────────────────────────────── 렌더링
E = html.escape


def md_inline(s: str) -> str:
    s = E(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)


def n0(x: float) -> str:
    return f"{x:,.0f}"


def gauge(label: str, value: float, limit: float, unit: str, cls: str) -> str:
    pct = max(0.0, min(100.0, 100.0 * value / limit))
    return (f'<div class="gauge"><div class="g-label">{label}</div>'
            f'<div class="g-track" role="img" aria-label="{label} {n0(value)} {unit} / 상한 {n0(limit)} {unit}">'
            f'<div class="g-fill {cls}" style="width:{max(pct, 0.6):.2f}%"></div></div>'
            f'<div class="g-val"><span class="num">{n0(value)}</span> <span class="unit">{unit}</span>'
            f'<span class="of">/ {n0(limit)} · {pct:.1f}%</span></div></div>')


def tile_bar(arm: str, hist: list[int], mean: float) -> str:
    tot = sum(hist)
    segs = "".join(
        f'<div class="seg t{k}" style="flex:{c}" title="{k}타일 {c:,}프레임">'
        f'{"<span>" + str(k) + "</span>" if c / tot > 0.06 else ""}</div>'
        for k, c in enumerate(hist) if c)
    legend = " · ".join(f"{k}타일 {100 * c / tot:.1f}%" for k, c in enumerate(hist) if c)
    return (f'<div class="tile-row"><div class="arm {arm.lower()}">{arm}</div>'
            f'<div class="tile-bar">{segs}</div>'
            f'<div class="tile-mean"><span class="num">{mean:.2f}</span><span class="unit">타일/프레임</span></div>'
            f'<div class="tile-legend">{legend}</div></div>')


def size_bars(rows: list[tuple[str, float, str]], s_contract: int) -> str:
    top = 240_000
    ticks = "".join(f'<div class="tick" style="left:{100 * t / top:.2f}%"><span>{t // 1000}</span></div>'
                    for t in range(0, top + 1, 40_000))
    marker = (f'<div class="s-mark" style="left:{100 * s_contract / top:.2f}%">'
              f'<span>계약 S {s_contract / 1000:.1f} KB</span></div>')
    bars = "".join(
        f'<div class="sz-row"><div class="sz-label">{E(lbl)}</div>'
        f'<div class="sz-track"><div class="sz-fill {cls}" style="width:{100 * v / top:.2f}%"></div></div>'
        f'<div class="sz-val num">{v / 1000:.1f} KB</div></div>' for lbl, v, cls in rows)
    return (f'<div class="sizes">{bars}<div class="sz-axis"><div class="sz-label"></div>'
            f'<div class="axis-track">{ticks}{marker}</div><div class="sz-val unit">KB</div></div></div>')


STATE_ORDER = (("IN_DATASET", "ds", "데이터셋 편입"), ("INDEXED", "ok", "검증 완료"),
               ("UPLOADED", "up", "도착·검증 대기"),
               ("PENDING", "pend", "사진 대기"), ("QUARANTINED", "crit", "격리"),
               ("DROPPED", "drop", "버려짐"))


def funnel(arm: str, states: dict) -> str:
    tot = sum(states.values())
    if not tot:
        return (f'<div class="fn-row"><div class="arm {arm.lower()}">{arm}</div>'
                f'<div class="fn-empty">아직 재생하지 않았다</div></div>')
    segs = "".join(
        f'<div class="fn-seg {cls}" style="flex:{states[k]}" title="{k} {states[k]:,}"></div>'
        for k, cls, _ in STATE_ORDER if states.get(k))
    legend = "".join(
        f'<span class="fn-key"><i class="{cls}"></i>{k} <b class="num">{states.get(k, 0):,}</b></span>'
        for k, cls, _ in STATE_ORDER)
    return (f'<div class="fn-row"><div class="arm {arm.lower()}">{arm}</div>'
            f'<div class="fn-bar">{segs}</div><div class="fn-tot num">{tot:,}</div>'
            f'<div class="fn-legend">{legend}</div></div>')


def latency_hist(lat: list[float], cls: str, lmax: float) -> str:
    """도착 지연 분포 (차량 시계). 막대 하나 = 계급 하나."""
    top = lat[-1]
    w = next(x for x in (30, 60, 120, 150, 300, 600, 900, 1800, 3600) if top / x <= 24)
    nb = int(top // w) + 1
    counts = [0] * nb
    for x in lat:
        counts[min(int(x // w), nb - 1)] += 1
    cmax = max(counts)
    ystep = next(x for x in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000) if cmax / x <= 5)
    ytop = ystep * -(-cmax // ystep)
    W, H, L, R, T, B = 720, 230, 48, 16, 14, 40
    pw, ph = W - L - R, H - T - B
    bw = pw / nb
    X = lambda v: L + pw * v / (nb * w)                                   # noqa: E731
    Y = lambda c: T + ph * (1 - c / ytop)                                 # noqa: E731
    grid = "".join(f'<line class="gr" x1="{L}" x2="{W - R}" y1="{Y(c):.1f}" y2="{Y(c):.1f}"/>'
                   f'<text class="tk" x="{L - 8}" y="{Y(c) + 4:.1f}" text-anchor="end">{c:,}</text>'
                   for c in range(0, ytop + 1, ystep))
    bars = "".join(f'<rect class="hb {cls}" x="{L + i * bw + 1:.1f}" y="{Y(c):.1f}" '
                   f'width="{max(bw - 2, 1):.1f}" height="{Y(0) - Y(c):.1f}">'
                   f'<title>{i * w:,}–{(i + 1) * w:,} s · {c:,}</title></rect>'
                   for i, c in enumerate(counts) if c)
    every = max(1, -(-nb // 8))
    xt = "".join(f'<text class="tk" x="{X(i * w):.1f}" y="{H - B + 18}" text-anchor="middle">{i * w:,}</text>'
                 for i in range(0, nb + 1, every))
    p50 = lat[len(lat) // 2]
    marks = "".join(
        f'<line class="mk" x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{T}" y2="{Y(0):.1f}"/>'
        f'<text class="mk-t" x="{X(v) + (6 if anchor == "start" else -6):.1f}" y="{T + 12}" '
        f'text-anchor="{anchor}">{name} {v:,.0f} s</text>'
        for v, name, anchor in ((p50, "중앙값", "start"), (top, "최대", "end")))
    return (f'<figure class="hist"><svg viewBox="0 0 {W} {H}" role="img" '
            f'aria-label="도착 지연 분포 · 중앙값 {p50:,.0f} s · 최대 {top:,.0f} s">'
            f'{grid}{bars}<line class="ax" x1="{L}" x2="{W - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>{xt}{marks}'
            f'<text class="tk" x="{W - R}" y="{H - 4}" text-anchor="end">도착 지연 (s, 차량 시계)</text></svg>'
            f'<figcaption>판정 {len(lat):,}건의 <code>arrival_latency_s</code> = 번들 PUT 시각 − 프레임 시각. '
            f'계급 {w:,} s. 최대가 계약 상한 L_max {lmax:,.0f} s 의 <b>{100 * top / lmax:.1f}%</b> 다.'
            f'</figcaption></figure>')


def live_section(live: Optional[dict], rep: dict, lmax: float) -> str:
    if live is None:
        return ('<div class="live-off"><b>측정 안 함 — 스택이 내려가 있다.</b> '
                '<code>python run.py up</code> 뒤 <code>python run.py status</code> 로 다시 구우면 운영 테이블을 읽는다.</div>')
    out = '<div class="funnel">' + "".join(funnel(a, live["arms"][a]["states"]) for a in V.ARMS) + "</div>"
    for a in V.ARMS:
        la = live["arms"][a]
        if not sum(la["states"].values()):
            continue
        checks = run_checks(la, rep[a])
        ok_all = all(ok for _, _, ok in checks)
        items = "".join(f'<li class="{"ok" if ok else "bad"}"><span>{E(k)}</span><b class="num">{E(v)}</b></li>'
                        for k, v, ok in checks)
        verdict = "완주 — 재생 보고와 전부 일치" if ok_all else "불일치 — ✗ 항목을 본다"
        out += (f'<div class="run {"pass" if ok_all else "fail"}"><div class="run-head">'
                f'<span class="arm {a.lower()}">{a}</span><span class="run-verdict">{verdict}</span>'
                f'<span class="run-meta">번들 {la["bundles"]:,} · 텔레메트리 {la["telemetry"]:,} '
                f'(final {la["final"]})</span></div><ul class="checks">{items}</ul>'
                + (latency_hist(la["latency"], a.lower(), lmax) if la["latency"] else "") + "</div>")
    return out


def render(ctx: dict) -> str:
    c, sig, rep = ctx["contract"], ctx["signals"], ctx["replay"]
    ms_html = ""
    for m in ctx["milestones"]:
        done = sum(1 for _, ok in m["steps"] if ok)
        state = "done" if done == len(m["steps"]) else ("active" if done else "idle")
        chips = "".join(f'<li class="step {"ok" if ok else "todo"}">{E(n)}</li>' for n, ok in m["steps"])
        ms_html += (f'<div class="ms {state}"><div class="ms-head"><span class="ms-id">{m["m"]}</span>'
                    f'<span class="ms-goal">{E(m["goal"])}</span>'
                    f'<span class="ms-count num">{done}/{len(m["steps"])}</span></div>'
                    f'<ol class="steps">{chips}</ol></div>')

    arms = ("STA_B", "DYN_B")
    rows = [
        ("후보", lambda r: n0(r.candidates)),
        ("선별 · 수락 · 드롭", lambda r: f"{n0(r.selected)} · {n0(r.accepted)} · {n0(r.dropped)}"),
        ("판정 메시지 (1초 배치)", lambda r: n0(r.verdict_messages)),
        ("텔레메트리 (5분 집계)", lambda r: n0(r.telemetry_messages)),
        ("번들 · 용량", lambda r: f"{n0(r.bundles)} · {r.bytes_uploaded / 1e6:,.1f} MB"),
        ("모의시간 주행 + 주차 배출", lambda r: f"{n0(r.t_drive_s)} + {n0(r.t_park_s)} s"),
    ]
    table = "".join(f"<tr><th>{E(k)}</th>" + "".join(f'<td class="num">{f(rep[a])}</td>' for a in arms)
                    + "</tr>" for k, f in rows)
    gauges = ""
    for a in arms:
        r = rep[a]
        gauges += (f'<div class="gauge-group"><div class="arm {a.lower()}">{a}</div>'
                   + gauge("큐 최대점유", r.peak_queue_bytes / 1e6, c.queue_min_bytes / 1e6, "MB", a.lower())
                   + gauge("도착 지연 최대", r.max_latency_bundle_s, c.max_arrival_latency_s, "s", a.lower())
                   + "</div>")

    sizes = size_bars([
        (f"전체 {ctx['sizes']['n_all']:,}장 평균", ctx["sizes"]["all"], "neutral"),
        ("pool 후보 평균", ctx["sizes"]["pool"], "neutral"),
        ("STA_B 선별 평균", rep["STA_B"].payload_mean_bytes, "sta_b"),
        ("DYN_B 선별 평균", rep["DYN_B"].payload_mean_bytes, "dyn_b"),
    ], c.payload_bytes)
    over = [100 * (rep[a].payload_mean_bytes / c.payload_bytes - 1) for a in arms]

    mism = sig["_findings"]["frames_bin_vs_interim_mismatch"]
    n_pool = len(sig["pool_frame"])
    todo = "".join(f"<li>{md_inline(t)}</li>" for t in ctx["todo"])
    qs = ctx["questions"]
    questions = ("" if not qs else '<div class="questions"><h3>열린 질문 — 사람이 정할 것</h3><ol>'
                 + "".join(f"<li>{md_inline(q)}</li>" for q in qs) + "</ol></div>")
    live = ctx["live"]
    live_where = (f'운영 테이블 <code>{E(live["table"])}</code> 과 버킷 <code>{E(live["bucket"])}</code>'
                  if live else "운영 테이블과 버킷")
    log = "".join(f'<li><code>{E(h)}</code><span class="date">{E(d)}</span>{E(m)}</li>'
                  for h, d, m in ctx["log"])
    t_run, t_ok = ctx["tests"]

    return TEMPLATE.format(
        position=md_inline(ctx["position"]), today=date.today().isoformat(),
        head_commit=E(ctx["log"][0][0]), t_ok=t_ok, t_run=t_run,
        milestones=ms_html, todo_head=md_inline(ctx["todo_head"]), todo=todo, questions=questions,
        live=live_section(live, rep, c.max_arrival_latency_s), live_where=live_where,
        tiles=tile_bar("STA_B", sig["arms"]["STA_B"]["tile_hist"], sig["arms"]["STA_B"]["mean_tiles"])
        + tile_bar("DYN_B", sig["arms"]["DYN_B"]["tile_hist"], sig["arms"]["DYN_B"]["mean_tiles"]),
        c_tile=f"{sig['operating_point']['c_tile_ms']:.2f}", budget=n0(sig["operating_point"]["budget"]),
        n_pool=n0(n_pool), table=table, gauges=gauges, sizes=sizes,
        over_sta=f"{over[0]:+.0f}", over_dyn=f"{over[1]:+.0f}",
        mism_conf=n0(mism["conf_q8"]["frames"]), mism_det=n0(mism["det_count"]["frames"]),
        mism_mp=n0(mism["mean_p"]["frames"]), mism_kn=n0(mism["kinematic_u8"]["frames"]),
        profile_sha=E(sig["profile"]["sha"]), log=log,
        link_mbps=f"{c.uplink_bps / 1e6:.3f}", demand_mbps=f"{c.demand_bps / 1e6:.3f}",
    )


TEMPLATE = """<title>NeedSlack Cloud 진행 현황</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{
  --ground:#F5F2EB;--surface:#FFFDF8;--ink:#1B1916;--muted:#6F6758;--rule:#DDD5C6;
  --accent:#8C6A22;--accent-soft:#F4E9D0;--code:#EFE9DD;
  --sta:#5D7894;--sta-soft:#DCE5EE;--dyn:#B9862C;--dyn-soft:#F2E3C4;
  --t1:#C9D5E1;--t2:#E3C27C;--t3:#A8741C;
  --ok:#3E7A53;--ok-soft:#DDEBDF;--warn:#A86A12;--warn-soft:#F6E6C8;--idle:#B3AA9A;
  --crit:#A63D32;--crit-soft:#F4DCD7;--up:#7FA88C;
  --sans:"IBM Plex Sans KR","Malgun Gothic","Apple SD Gothic Neo",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{
  --ground:#161411;--surface:#1F1C18;--ink:#ECE6DA;--muted:#A49B8B;--rule:#38332C;
  --accent:#D8B366;--accent-soft:#352C1C;--code:#2A2620;
  --sta:#8EA9C6;--sta-soft:#243140;--dyn:#DDAA4E;--dyn-soft:#3A2F1B;
  --t1:#3C4B5B;--t2:#9C7A35;--t3:#E0A945;
  --ok:#72B58A;--ok-soft:#1E3326;--warn:#E3A94B;--warn-soft:#3A2D16;--idle:#5C554A;
  --crit:#E07A6C;--crit-soft:#3D1F1B;--up:#4F7A5E;
}}}}
:root[data-theme="dark"]{{
  --ground:#161411;--surface:#1F1C18;--ink:#ECE6DA;--muted:#A49B8B;--rule:#38332C;
  --accent:#D8B366;--accent-soft:#352C1C;--code:#2A2620;
  --sta:#8EA9C6;--sta-soft:#243140;--dyn:#DDAA4E;--dyn-soft:#3A2F1B;
  --t1:#3C4B5B;--t2:#9C7A35;--t3:#E0A945;
  --ok:#72B58A;--ok-soft:#1E3326;--warn:#E3A94B;--warn-soft:#3A2D16;--idle:#5C554A;
  --crit:#E07A6C;--crit-soft:#3D1F1B;--up:#4F7A5E;
}}
*{{box-sizing:border-box}}
body{{background:var(--ground);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.6;
  padding-inline:20px;padding-block:32px 64px}}
.wrap{{max-width:1000px;margin:0 auto;display:flex;flex-direction:column;gap:40px}}
h1,h2{{text-wrap:balance;margin:0;letter-spacing:-.01em}}
h1{{font-size:clamp(22px,3.2vw,30px);font-weight:700;line-height:1.3}}
h2{{font-size:19px;font-weight:600}}
p{{margin:0;max-width:68ch}}
code{{font-family:var(--mono);font-size:.88em;background:var(--code);padding:.08em .35em;border-radius:3px}}
.num{{font-family:var(--mono);font-variant-numeric:tabular-nums}}
.unit{{color:var(--muted);font-size:.82em}}
.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:600}}
header{{display:flex;flex-direction:column;gap:12px;padding-bottom:24px;border-bottom:1px solid var(--rule)}}
.meta{{display:flex;flex-wrap:wrap;gap:8px 20px;color:var(--muted);font-size:13px}}
.meta b{{color:var(--ink);font-weight:500}}
section{{display:flex;flex-direction:column;gap:16px}}
.lede{{color:var(--muted)}}
/* 마일스톤 */
.ms-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
.ms{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:14px 16px;display:flex;flex-direction:column;gap:10px}}
.ms.active{{border-color:var(--warn);box-shadow:inset 0 3px 0 var(--warn)}}
.ms.done{{box-shadow:inset 0 3px 0 var(--ok)}}
.ms-head{{display:flex;align-items:baseline;gap:8px}}
.ms-id{{font-family:var(--mono);font-weight:500;font-size:13px;color:var(--muted)}}
.ms.active .ms-id{{color:var(--warn)}}
.ms-goal{{font-weight:600;flex:1;line-height:1.35}}
.ms-count{{font-size:13px;color:var(--muted)}}
.steps{{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px}}
.step{{font-size:12.5px;padding:2px 9px;border-radius:999px;border:1px solid var(--rule);color:var(--muted)}}
.step.ok{{background:var(--ok-soft);border-color:transparent;color:var(--ok);font-weight:500}}
.step.ok::before{{content:"✓ "}}
/* 먼저 할 일 */
.next{{background:var(--warn-soft);border-radius:6px;padding:18px 20px;display:flex;flex-direction:column;gap:10px}}
.next h3{{margin:0;font-size:15px;font-weight:600;color:var(--warn)}}
.next ol{{margin:0;padding-left:1.3em;display:flex;flex-direction:column;gap:8px}}
.next li{{max-width:80ch}}
/* 타일 */
.tiles{{display:flex;flex-direction:column;gap:14px;background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px}}
.tile-row{{display:grid;grid-template-columns:64px 1fr auto;grid-template-areas:"arm bar mean" ". legend legend";gap:4px 14px;align-items:center}}
.arm{{font-family:var(--mono);font-size:13px;font-weight:500}}
.arm.sta_b{{color:var(--sta)}}.arm.dyn_b{{color:var(--dyn)}}
.tile-row .arm{{grid-area:arm}}
.tile-bar{{grid-area:bar;display:flex;height:26px;border-radius:4px;overflow:hidden;gap:2px}}
.seg{{display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:12px;color:var(--ink)}}
.seg.t1{{background:var(--t1)}}.seg.t2{{background:var(--t2)}}.seg.t3{{background:var(--t3);color:var(--surface)}}
.tile-mean{{grid-area:mean;display:flex;gap:6px;align-items:baseline}}
.tile-mean .num{{font-size:20px;font-weight:500}}
.tile-legend{{grid-area:legend;font-size:12.5px;color:var(--muted)}}
/* 표 */
.tbl-wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;min-width:480px;font-size:14px}}
th,td{{padding:9px 12px;border-bottom:1px solid var(--rule);text-align:right}}
th{{text-align:left;font-weight:500;color:var(--muted)}}
thead th{{color:var(--ink);font-family:var(--mono);font-size:13px;text-align:right}}
thead th.sta_b{{color:var(--sta)}}thead th.dyn_b{{color:var(--dyn)}}
/* 게이지 */
.gauges{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
.gauge-group{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:16px 18px;display:flex;flex-direction:column;gap:12px}}
.gauge{{display:flex;flex-direction:column;gap:5px}}
.g-label{{font-size:13px;color:var(--muted)}}
.g-track{{height:10px;background:var(--code);border-radius:5px;overflow:hidden}}
.g-fill{{height:100%;border-radius:5px}}
.g-fill.sta_b{{background:var(--sta)}}.g-fill.dyn_b{{background:var(--dyn)}}
.g-val{{display:flex;gap:6px;align-items:baseline;flex-wrap:wrap}}
.g-val .num{{font-size:17px;font-weight:500}}
.g-val .of{{color:var(--muted);font-size:12.5px;font-family:var(--mono)}}
/* 크기 */
.sizes{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px;display:flex;flex-direction:column;gap:10px}}
.sz-row,.sz-axis{{display:grid;grid-template-columns:150px 1fr 76px;gap:12px;align-items:center}}
.sz-label{{font-size:13.5px}}
.sz-track{{height:18px;position:relative}}
.sz-fill{{height:100%;border-radius:0 3px 3px 0}}
.sz-fill.neutral{{background:var(--idle)}}.sz-fill.sta_b{{background:var(--sta)}}.sz-fill.dyn_b{{background:var(--dyn)}}
.sz-val{{text-align:right;font-size:13.5px}}
.axis-track{{position:relative;height:34px;border-top:1px solid var(--rule)}}
.tick{{position:absolute;top:0;height:5px;border-left:1px solid var(--muted)}}
.tick span{{position:absolute;top:6px;transform:translateX(-50%);font-family:var(--mono);font-size:11px;color:var(--muted)}}
.s-mark{{position:absolute;bottom:34px;height:calc(4 * 28px);border-left:2px dashed var(--accent);pointer-events:none}}
.s-mark span{{position:absolute;top:-20px;left:4px;white-space:nowrap;font-size:11.5px;color:var(--accent);font-weight:600}}
.callout{{border-left:3px solid var(--accent);padding:4px 0 4px 16px;display:flex;flex-direction:column;gap:6px}}
.callout strong{{color:var(--ink)}}
/* 보기 */
.how{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.how div{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:16px 18px;display:flex;flex-direction:column;gap:8px}}
.how h3{{margin:0;font-size:15px;font-weight:600}}
.how pre{{margin:0;overflow-x:auto;font-family:var(--mono);font-size:12.5px;background:var(--code);padding:10px 12px;border-radius:4px;line-height:1.5}}
.how p{{font-size:13.5px;color:var(--muted)}}
/* 열린 질문 */
.questions{{border:1px solid var(--rule);border-radius:6px;padding:16px 20px;display:flex;flex-direction:column;gap:10px;background:var(--surface)}}
.questions h3{{margin:0;font-size:15px;font-weight:600}}
.questions ol{{margin:0;padding-left:1.3em;display:flex;flex-direction:column;gap:8px}}
.questions li{{max-width:84ch}}
/* 운영 실측 */
.live-off{{background:var(--code);border-radius:6px;padding:14px 18px;color:var(--muted)}}
.live-off b{{color:var(--ink)}}
.funnel{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px;display:flex;flex-direction:column;gap:16px}}
.fn-row{{display:grid;grid-template-columns:64px 1fr 64px;grid-template-areas:"arm bar tot" ". legend legend";gap:6px 14px;align-items:center}}
.fn-row .arm{{grid-area:arm}}
.fn-bar{{grid-area:bar;display:flex;height:22px;border-radius:4px;overflow:hidden;gap:2px;background:var(--code)}}
.fn-tot{{grid-area:tot;text-align:right;font-size:14px}}
.fn-empty{{grid-column:2 / span 2;color:var(--muted);font-size:13.5px}}
.fn-seg.ds,.fn-key i.ds{{background:var(--accent)}}.fn-seg.ok,.fn-key i.ok{{background:var(--ok)}}.fn-seg.up,.fn-key i.up{{background:var(--up)}}
.fn-seg.pend,.fn-key i.pend{{background:var(--warn)}}.fn-seg.crit,.fn-key i.crit{{background:var(--crit)}}
.fn-seg.drop,.fn-key i.drop{{background:var(--idle)}}
.fn-legend{{grid-area:legend;display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12.5px;color:var(--muted)}}
.fn-key i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.fn-key b{{color:var(--ink);font-weight:500}}
.run{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:16px 20px;display:flex;flex-direction:column;gap:14px}}
.run.pass{{box-shadow:inset 3px 0 0 var(--ok)}}.run.fail{{box-shadow:inset 3px 0 0 var(--crit)}}
.run-head{{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px}}
.run-verdict{{font-weight:600}}.run.pass .run-verdict{{color:var(--ok)}}.run.fail .run-verdict{{color:var(--crit)}}
.run-meta{{color:var(--muted);font-size:13px;margin-left:auto}}
.checks{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:6px 20px}}
.checks li{{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-bottom:1px solid var(--rule);font-size:13.5px}}
.checks li span::before{{content:"✓ ";color:var(--ok);font-weight:600}}
.checks li.bad span::before{{content:"✗ ";color:var(--crit)}}
.hist{{margin:0;overflow-x:auto}}
.hist svg{{width:100%;min-width:520px;height:auto;display:block}}
.hist figcaption{{font-size:12.5px;color:var(--muted);margin-top:6px}}
.hist b{{color:var(--ink)}}
svg text{{font-family:var(--sans);fill:var(--ink)}}
svg .tk{{font-size:11.5px;fill:var(--muted);font-family:var(--mono)}}
svg .gr{{stroke:var(--rule);stroke-width:1}}svg .ax{{stroke:var(--muted);stroke-width:1}}
svg .hb.dyn_b{{fill:var(--dyn)}}svg .hb.sta_b{{fill:var(--sta)}}
svg .mk{{stroke:var(--ink);stroke-width:1.2;stroke-dasharray:3 3}}
svg .mk-t{{font-size:12px;font-weight:600}}
/* 로그 */
.log{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}}
.log li{{display:grid;grid-template-columns:72px 92px 1fr;gap:12px;padding:8px 0;border-bottom:1px solid var(--rule);font-size:14px}}
.log .date{{color:var(--muted);font-family:var(--mono);font-size:12.5px}}
@media (max-width:560px){{
  .sz-row,.sz-axis{{grid-template-columns:96px 1fr 64px}}
  .log li{{grid-template-columns:64px 1fr}}.log .date{{display:none}}
  .fn-row{{grid-template-columns:56px 1fr;grid-template-areas:"arm bar" "tot tot" "legend legend"}}.fn-tot{{text-align:left}}
  .run-meta{{margin-left:0}}
  .tile-row{{grid-template-columns:56px 1fr;grid-template-areas:"arm bar" "mean mean" "legend legend"}}
}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">NeedSlack Cloud · 진행 현황</div>
  <h1>{position}</h1>
  <div class="meta"><span>생성 <b class="num">{today}</b></span><span>커밋 <b class="num">{head_commit}</b></span>
  <span>테스트 <b class="num">{t_ok}/{t_run}</b> 통과</span><span>링크 <b class="num">{link_mbps}</b> Mbps · 수요 <b class="num">{demand_mbps}</b> Mbps</span></div>
</header>

<section>
  <h2>마일스톤</h2>
  <p class="lede">단계 완료는 그 단계의 산출물 파일이 저장소에 있는지로 판정한다.</p>
  <div class="ms-grid">{milestones}</div>
  <div class="next"><h3>먼저 — {todo_head}</h3><ol>{todo}</ol></div>
  {questions}
</section>

<section>
  <h2>운영 파이프라인 실측</h2>
  <p class="lede">이 페이지를 만들 때 {live_where}을 읽은 값이다. 판정이 차량을 떠나 사진과 짝지어지고 체크섬을 통과해
  <code>INDEXED</code> 에 닿았는지, 그리고 그 결과가 아래 dry-run 재생 보고와 같은지를 본다.</p>
  {live}
</section>

<section>
  <h2>파이프라인에 실리는 대조</h2>
  <p class="lede">이 프로젝트의 실험은 하나다. 두 팔은 같은 pool {n_pool} 후보에서 같은 예산 {budget} 프레임을 고르고,
  다른 것은 프레임당 채점 타일 수뿐이다(C_TILE {c_tile} ms, 레짐 B). 판정 레코드의 <code>tiles_scored</code> 가 이 분포를 그대로 싣는다.</p>
  <div class="tiles">{tiles}</div>
</section>

<section>
  <h2>재생 실측 — 두 팔 dry-run</h2>
  <p class="lede">이 페이지를 만들 때 두 팔을 싱크 없이 끝까지 재생해 잰 값이다. 후보 전량이 흐르고, 판정은 선별분만 나간다.</p>
  <div class="tbl-wrap"><table><thead><tr><th></th><th class="sta_b">STA_B</th><th class="dyn_b">DYN_B</th></tr></thead>
  <tbody>{table}</tbody></table></div>
  <div class="gauges">{gauges}</div>
</section>

<section>
  <h2>발견</h2>
  {sizes}
  <div class="callout"><p><strong>선별분 페이로드가 계약 S 보다 크다 — STA_B {over_sta}% · DYN_B {over_dyn}%.</strong>
  전체 평균은 계약 근거와 맞지만 가치 기반 선별은 복잡한 장면(큰 JPEG)을 고른다. 업링크 수요 · δ_max · L_max 가 그만큼 낙관적이다.
  이번 런 규모에서는 큐 여유가 커서 드롭이 없었다. 계약을 고칠지는 결정이 필요하다.</p></div>
  <div class="callout"><p><strong>rev.9 frames.bin 의 conf_q8·det_count 가 interim 현재본과 다르다</strong>
  (pool {n_pool} 중 conf_q8 {mism_conf} · det_count {mism_det} · mean_p {mism_mp} · kinematic_u8 {mism_kn} 프레임).
  profile <code>{profile_sha}</code> 가 w_conf = 0 이라 value_score 는 불변이고, 선별은 타일 YOLO 점수라 대조와 무관하다. rev.9 는 동결이라 기록만 한다.</p></div>
</section>

<section>
  <h2>로컬에서 직접 보기</h2>
  <div class="how">
    <div><h3>S3 번들 — MinIO 콘솔</h3>
      <pre>http://localhost:9001</pre>
      <p>로그인은 <code>.env</code> 의 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY. 버킷 <code>ns-raw-payload</code> → <code>vin=sim-…/boot=…/</code> 아래 35프레임 번들.</p></div>
    <div><h3>판정·텔레메트리 — MQTT</h3>
      <pre>docker exec -it ns-mosquitto \\
  mosquitto_sub -t "ns/v1/#" -v</pre>
      <p>다른 터미널에서 재생을 돌리면 판정 배치가 1초 간격으로 흐른다.</p></div>
    <div><h3>한 팔 완주 — 터미널 셋</h3>
      <pre>python run.py collect
python run.py reconcile
python run.py replay DYN_B</pre>
      <p>수집기·조정기는 Ctrl+C 로 멈춘다. 꺼져 있던 동안의 메시지는 브로커 세션이 보관한다.
      <code>replay … --dry-run</code> 은 스택 없이 계약만 돈다(약 5초).</p></div>
    <div><h3>이 페이지 다시 굽기</h3>
      <pre>python run.py status</pre>
      <p>README · 스냅샷 · 재생 실측 · git 로그에서 다시 만든다.</p></div>
  </div>
</section>

<section>
  <h2>최근 커밋</h2>
  <ul class="log">{log}</ul>
</section>
</div>
"""


def main(argv: list[str]) -> int:
    out = argv[0] if argv else OUT_DEFAULT
    pos, todo_head, todo = readme()
    sig = json.load(io.open(os.path.join(ROOT, "schema", "arm_signals.json"), encoding="utf-8"))
    print("두 팔 dry-run 재생 중…", flush=True)
    rep = replay_all()
    live = pipeline_live()
    print("운영 테이블 " + ("읽음" if live else "없음 — 스택 내려감"), flush=True)
    ctx = {
        "position": pos, "todo_head": todo_head, "todo": todo, "questions": open_questions(),
        "live": live, "contract": load_contract(), "signals": sig, "replay": rep,
        "sizes": payload_sizes(sig["pool_frame"]), "log": git_log(), "tests": test_count(),
        "milestones": milestones({a: arm_complete(live, rep, a) for a in V.ARMS}),
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    io.open(out, "w", encoding="utf-8", newline="\n").write(render(ctx))
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
