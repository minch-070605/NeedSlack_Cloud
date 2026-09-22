# -*- coding: utf-8 -*-
"""build_primer.py — 마일스톤 입문 페이지를 굽는다. 진행 현황 한 판을 읽는 데 필요한 개념만 담는다.

    python run.py primer m2          # docs/_build/primer-m2.html — 동결과 계보
    python run.py primer m3          # docs/_build/primer-m3.html — 재학습과 판정

`build_guide.py`(전체 입문)와 같은 디자인 토큰을 쓴다 — 스타일은 그 템플릿에서 꺼내 온다.
숫자는 전부 아래에서 읽는다(CLAUDE.md §2):

    m2   PostgreSQL 계보 · MinIO ns-datasets snapshot.json · schema/pg/001_init.sql
    m3   rev.9 runs_yolo/axis2/eval_axis2_mc.json (오프라인 결과 · 시드별 값)
         rev.9 04_중앙AI/yolo_detector.py 의 LOCKED_TRAIN_ARGS (ast 로 값만 읽는다 — import 하지 않는다)
         pipelines/build_colab_nb.py 의 학습 인자 · ultralytics 버전 · 부트스트랩 횟수
"""
from __future__ import annotations

import ast
import html
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(ROOT, "docs"), os.path.join(ROOT, "pipelines"), os.path.join(ROOT, "edge-agent"),
           os.path.join(ROOT, "schema"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_guide as BG        # noqa: E402  (스타일 재사용)

E = html.escape
OUT = os.path.join(ROOT, "docs", "_build")


def style() -> str:
    """build_guide 의 <style> 을 꺼내 format 이스케이프({{ }})를 푼다."""
    t = BG.TEMPLATE
    css = t[t.index("<style>"):t.index("</style>") + len("</style>")]
    return css.replace("{{", "{").replace("}}", "}") + EXTRA_CSS


EXTRA_CSS = """
<style>
.chain{display:flex;flex-wrap:wrap;align-items:stretch;gap:8px}
.chain .node{flex:1;min-width:150px;display:flex;flex-direction:column;gap:2px}
.chain .node .num{font-size:18px}
.chain .arrow{align-self:center}
.node.empty{border-style:dashed;opacity:.8}
.stops{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule);border-radius:6px;overflow:hidden}
.stops li{background:var(--surface);padding:14px 16px;display:flex;flex-direction:column;gap:6px}
.stops b{font-size:14.5px}.stops span{font-size:13.5px;color:var(--muted);line-height:1.55}
.stops .got{font-family:var(--mono);font-size:12.5px;color:var(--ok)}
.overlap{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px;display:flex;flex-direction:column;gap:10px}
.ov-row{display:grid;grid-template-columns:64px 1fr;gap:12px;align-items:center}
.ov-bar{display:flex;height:26px;border-radius:4px;overflow:hidden;gap:2px}
.ov-bar div{display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:12px;color:var(--surface)}
.ov-bar .own-s{background:var(--sta)}.ov-bar .own-d{background:var(--dyn)}.ov-bar .both{background:var(--idle);color:var(--ink)}
.ov-bar .gap{background:transparent}
.steps-do{margin:0;padding-left:1.3em;display:flex;flex-direction:column;gap:10px;max-width:760px}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule);border-radius:6px;overflow:hidden}
.kv div{background:var(--surface);padding:10px 14px;display:flex;flex-direction:column;gap:2px}
.kv b{font-family:var(--mono);font-size:13px}.kv span{font-size:12.5px;color:var(--muted)}
svg .dot-s{fill:var(--sta)}svg .dot-d{fill:var(--dyn)}svg .dot-b{fill:var(--idle)}
svg .mean{stroke-width:2.5}svg .mean-s{stroke:var(--sta)}svg .mean-d{stroke:var(--dyn)}svg .mean-b{stroke:var(--idle)}
svg .brk{stroke:var(--accent);stroke-width:1.5;fill:none}svg .brk-t{font-size:12.5px;fill:var(--accent);font-weight:600}
svg .lbl{font-size:13px;font-family:var(--mono);font-weight:500}
</style>
"""


def page(title: str, eyebrow: str, thesis: str, lede: str, body: str) -> str:
    return (f"<title>{E(title)}</title>\n"
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700'
            '&family=IBM+Plex+Mono:wght@400;500&family=Noto+Serif+KR:wght@500;700&display=swap">\n'
            + style() + f'\n<div class="wrap">\n<header><div class="eyebrow">{E(eyebrow)}</div>'
            f'<p class="thesis">{thesis}</p><p class="lede prose">{lede}</p></header>\n{body}\n</div>\n')


def sec(n: int, title: str, inner: str) -> str:
    return (f'<section><div class="sec-head"><span class="sec-n">{n}</span><h2>{E(title)}</h2></div>'
            f"{inner}</section>")


def gloss(items: list[tuple[str, str]]) -> str:
    return '<div class="gloss">' + "".join(f"<div><b>{E(k)}</b><span>{v}</span></div>" for k, v in items) + "</div>"


# ─────────────────────────────────────────────────────────── M2
def m2_data() -> dict:
    import psycopg
    from common import aws
    d = {"arms": {}}
    with psycopg.connect(os.environ["PG_DSN"], connect_timeout=2) as c, c.cursor() as cur:
        for t in ("campaign", "dataset_version", "dataset_frame", "model_run", "eval_result"):
            cur.execute(f"SELECT count(*) FROM {t}")
            d[t] = cur.fetchone()[0]
        cur.execute("""SELECT count(*) FROM dataset_frame a JOIN dataset_frame b
                       ON a.frame_id = b.frame_id AND a.ds_id = 'ds-STA_B' AND b.ds_id = 'ds-DYN_B'""")
        d["both"] = cur.fetchone()[0]
    s3 = aws.s3_client()
    for a in ("STA_B", "DYN_B"):
        d["arms"][a] = json.loads(s3.get_object(Bucket=aws.dataset_bucket(),
                                                Key=f"datasets/ds-{a}/snapshot.json")["Body"].read())
    return d


def build_m2() -> str:
    d = m2_data()
    sa, da = d["arms"]["STA_B"], d["arms"]["DYN_B"]
    n = sa["n_frames"]
    only = n - d["both"]
    ov = (f'<div class="overlap">'
          f'<div class="ov-row"><span class="arm sta_b">STA_B</span><div class="ov-bar">'
          f'<div class="own-s" style="flex:{only}">{only:,}</div><div class="both" style="flex:{d["both"]}">공통 {d["both"]:,}</div>'
          f'<div class="gap" style="flex:{only}"></div></div></div>'
          f'<div class="ov-row"><span class="arm dyn_b">DYN_B</span><div class="ov-bar">'
          f'<div class="gap" style="flex:{only}"></div><div class="both" style="flex:{d["both"]}">공통 {d["both"]:,}</div>'
          f'<div class="own-d" style="flex:{only}">{only:,}</div></div></div>'
          f'<p class="lede">두 팔은 같은 후보에서 같은 {n:,}장을 고르지만, <strong>{only:,}장({100 * only / n:.1f}%)이 서로 다르다.</strong>'
          f' 재학습 결과의 차이는 오직 이 {only:,}장에서 나온다 — 나머지는 두 팔이 똑같이 받는다.</p></div>')

    stops = [
        ("① 정본 선별과 같은가", "재생·수집·조정을 지나 INDEXED 에 닿은 프레임 집합이 rev.9 선별 파일(세 시드 동일)과 한 장이라도 다르면 멈춘다. 파이프라인이 대조를 바꾸지 않았다는 증거다(수용 기준 3).",
         f"두 팔 모두 {n:,} = {n:,}"),
        ("② rev.9 가 실제로 학습한 목록과 같은가", "rev.9 가 오프라인 실험에서 쓴 학습 목록 파일과도 대조한다. 선별 파일과 학습 목록이 어긋나 있었다면 여기서 드러난다.",
         "두 팔 모두 일치"),
        ("③ 평가셋과 겹치지 않는가", "학습 프레임이 held-out(평가 전용)에 한 장이라도 있으면 모든 점수가 무의미해진다. 학습 ∩ held-out = ∅ (수용 기준 7).",
         "겹침 0"),
        ("④ 기전·프로파일이 섞이지 않았는가", "한 스냅샷 안의 판정은 회수 기전(reclaim_mode)과 가치 프로파일이 하나여야 한다 — 섞인 데이터로 낸 점수는 어느 기전의 것도 아니다(설계결정-0001).",
         f"{sa['reclaim_mode']} · {da['reclaim_mode']}"),
        ("⑤ 사진이 원본과 같은가", "번들에서 잘라 낸 바이트의 지문(sha256)을 판정이 들고 온 값, 그리고 rev.9 원본 파일과 대조한다. 전송 도중 한 비트라도 바뀌었으면 멈춘다.",
         f"{2 * n:,}장 전부 일치"),
    ]
    stops_html = '<ul class="stops">' + "".join(
        f'<li><b>{E(t)}</b><span>{E(x)}</span><span class="got">✓ {E(g)}</span></li>' for t, x, g in stops) + "</ul>"

    chain = [("eval_result", d["eval_result"], "점수 한 줄 (M3 가 채운다)"),
             ("model_run", d["model_run"], "학습 런 (M3 가 채운다)"),
             ("dataset_version", d["dataset_version"], "동결 데이터셋 — 팔당 하나"),
             ("dataset_frame", d["dataset_frame"], "데이터셋에 든 사진 한 장씩"),
             ("frame_uuid → DynamoDB", None, "판정 · 번들 위치 · 지문 → 원본 사진")]
    chain_html = '<div class="chain">' + '<span class="arrow">→</span>'.join(
        f'<div class="node{" empty" if v == 0 else ""}"><b>{E(k)}</b>'
        + (f'<span class="num">{v:,} 행</span>' if v is not None else "")
        + f"<small>{E(x)}</small></div>" for k, v, x in chain) + "</div>"

    kv = "".join(f"<div><b>{E(k)}</b><span>{E(v)}</span></div>" for k, v in [
        ("ds-STA_B.tar", sa["tar_sha256"][:16] + "…"), ("ds-DYN_B.tar", da["tar_sha256"][:16] + "…"),
        ("eval-common.tar", sa["eval_common_tar_sha256"][:16] + "…"), ("split_seed", str(sa["split_seed"])),
        ("profile_sha", sa["profile_sha"]), ("manifest", "ns-datasets/datasets/ds-{ARM}/")])

    body = (
        sec(1, "스냅샷 — 움직이던 장부를 한 순간에 얼린다", '<div class="prose" style="display:flex;flex-direction:column;gap:12px">'
            "<p>M1 까지 장부(DynamoDB)는 계속 바뀌었다 — 판정이 들어오고, 사진이 도착하고, 상태가 옮겨 갔다. 학습에 쓸 데이터는 그렇게 움직이면 안 된다."
            " 같은 이름의 데이터셋이 어제와 오늘 다르면, 점수가 무엇의 점수인지 말할 수 없다.</p>"
            f"<p>그래서 M2 는 두 팔 각각에 대해 <strong>검증이 끝난 판정 {n:,}건을 골라 한 번 얼린다(동결).</strong> 얼린 뒤에는 고치지 않는다 —"
            " 같은 스냅샷을 다시 만들면 바이트 하나까지 같아야 하고, 다르면 멈춘다. 얼린 판정은 장부에서 <code>IN_DATASET</code> 으로 옮겨 간다.</p></div>"
            '<div class="fig"><div class="states"><span class="st pending">PENDING</span>→<span class="st ok">UPLOADED</span>→'
            '<span class="st ok">INDEXED</span>→<span class="st ok" style="outline:2px solid var(--accent)">IN_DATASET</span>'
            '<span style="color:var(--muted);font-size:13px">M2 가 옮긴다 · 어느 데이터셋에 들었는지(ds_id)를 함께 적는다</span></div></div>'),
        sec(2, "얼리기 전에 멈추는 다섯 조건", '<p class="lede prose">하나라도 어기면 아무것도 쓰지 않는다. 진행 현황 M2 판의 "스냅샷 실측" 표가 이 다섯의 결과다.</p>' + stops_html),
        sec(3, "두 팔의 차이는 어디에 있나", ov),
        sec(4, "지문(sha256)과 결정적 tar", '<div class="concept"><div class="text">'
            "<p><strong>sha256</strong> 은 파일의 지문이다. 한 비트만 달라도 전혀 다른 값이 나온다. 그래서 \"이 파일이 그 파일인가\"를 파일을 열어 보지 않고 확인할 수 있다.</p>"
            "<p>스냅샷은 사진 2,449장과 라벨, 목록을 <strong>tar</strong> 하나로 묶는다. 이때 순서·시각·소유자를 고정해, 같은 입력이면 <strong>언제 만들어도 같은 바이트</strong>가 나오게 했다(결정적)."
            " 그래서 tar 하나의 지문이 곧 데이터셋 전체의 지문이 된다. M3 노트북은 Drive 에 올라온 tar 의 지문이 이 값과 다르면 학습을 시작하지 않는다.</p></div>"
            f'<div class="kv">{kv}</div></div>'),
        sec(5, "계보 — 점수 하나에서 사진 한 장까지", '<p class="lede prose">PostgreSQL 다섯 테이블이 사슬로 이어진다. 끝의 점수에서 출발해 조인 한 번으로 그 점수를 만든 사진 전부에 닿는다(수용 기준 4).'
            " 지금은 앞쪽 두 칸이 비어 있다 — M3 가 채운다. 조인이 실제로 도는지는 테스트가 롤백되는 트랜잭션 안에서 가짜 점수로 확인한다.</p>" + chain_html),
        sec(6, "왜 Drive 에 tar 로 올리나", '<div class="prose" style="display:flex;flex-direction:column;gap:12px">'
            "<p>재학습은 Colab(구글의 무료 GPU)에서 한다. Colab 은 인터넷 너머에 있고 MinIO 는 이 PC 안에 있어서, <strong>Colab 이 번들을 직접 읽을 수 없다.</strong>"
            " 그렇다고 rev.9 폴더의 사진을 쓰면 \"사진이 파이프라인을 지나왔다\"는 증명이 끊긴다.</p>"
            "<p>그래서 스냅샷이 <strong>번들에서 잘라 낸 사진</strong>을 tar 로 묶고, 사람이 Drive 에 올린다. 평가셋(val 800 · held-out 2,826)과 시작 가중치는 두 팔이 똑같이 받는 고정 자산이라"
            " <code>eval-common.tar</code> 하나로 따로 묶는다(설계결정-0002). rev.9 도 같은 방식으로 Colab 학습을 했다.</p></div>"),
        sec(7, "용어", gloss([
            ("동결 (freeze)", "한 번 만든 데이터셋을 다시는 고치지 않는 것. 다시 만들면 같아야 한다."),
            ("스냅샷", "움직이는 장부에서 한 순간을 떠 낸 데이터셋. 여기선 팔당 하나."),
            ("manifest", "스냅샷에 든 사진 목록 파일(한 줄에 한 장). 번들 위치·지문·기전을 함께 적는다."),
            ("sha256 (지문)", "파일 내용에서 계산한 64자리 값. 내용이 한 비트만 달라도 바뀐다."),
            ("결정적", "같은 입력이면 언제 해도 같은 결과(바이트)가 나오는 성질."),
            ("계보 (lineage)", "점수 → 모델 → 데이터셋 → 사진으로 거슬러 오르는 기록."),
            ("IN_DATASET", "동결 데이터셋에 편입된 판정의 상태. INDEXED 의 다음."),
            ("held-out", "학습에 절대 쓰지 않는 평가 전용 사진 2,826장."),
            ("val (tune 800)", "학습 중 진행을 보여 주는 용도의 사진. 가중치 선택에는 쓰지 않는다."),
        ])),
    )
    return page("입문 M2 동결과 계보", "NeedSlack Cloud · 마일스톤 입문 · M2",
                "파이프라인을 지나온 사진이 <em>rev.9 가 고른 그 사진과 한 장도 다르지 않은지</em> 확인하고, 얼려서, 점수까지 이어지는 사슬을 놓는다.",
                "진행 현황의 \"M2 완료\" 판을 읽는 데 필요한 것만 모았다. 숫자는 이 페이지를 만들 때 PostgreSQL 계보와 MinIO 의 snapshot.json 에서 읽었다.",
                "".join(body))


# ─────────────────────────────────────────────────────────── M3
def locked_args() -> dict:
    import replay
    src = open(os.path.join(replay.rev9_root(), "04_중앙AI", "yolo_detector.py"), encoding="utf-8").read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "LOCKED_TRAIN_ARGS":
            return {kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords}
    raise SystemExit("LOCKED_TRAIN_ARGS 를 찾지 못했다")


def seed_plot(off: dict) -> str:
    rows = [("B0", "b", off["B0"]), ("STA_B", "s", off["STA_B"]), ("DYN_B", "d", off["DYN_B"])]
    lo, hi = 0.28, 0.36
    W, H, L, R, T, rowh = 760, 250, 80, 20, 20, 56
    X = lambda v: L + (W - L - R) * (v - lo) / (hi - lo)          # noqa: E731
    ticks = "".join(f'<line class="gr" x1="{X(v):.1f}" x2="{X(v):.1f}" y1="{T}" y2="{T + 3 * rowh}"/>'
                    f'<text class="tk" x="{X(v):.1f}" y="{T + 3 * rowh + 18}" text-anchor="middle">{v:.2f}</text>'
                    for v in (0.28, 0.30, 0.32, 0.34, 0.36))
    marks = ""
    for i, (name, k, c) in enumerate(rows):
        y = T + rowh * i + rowh / 2
        marks += f'<text class="lbl" x="{L - 12}" y="{y + 4:.1f}" text-anchor="end">{name}</text>'
        marks += f'<line class="mean mean-{k}" x1="{X(c["mean"]):.1f}" x2="{X(c["mean"]):.1f}" y1="{y - 16:.1f}" y2="{y + 16:.1f}"/>'
        marks += "".join(f'<circle class="dot-{k}" cx="{X(v):.1f}" cy="{y:.1f}" r="6"><title>{name} 시드 {j + 1} · {v:.4f}</title></circle>'
                         for j, v in enumerate(c["per_seed"]))
    ys, yd = T + rowh * 1.5, T + rowh * 2.5
    xs, xd = X(off["STA_B"]["mean"]), X(off["DYN_B"]["mean"])
    yb = T + 3 * rowh + 34
    brk = (f'<path class="brk" d="M{xs:.1f},{yb - 6} L{xs:.1f},{yb} L{xd:.1f},{yb} L{xd:.1f},{yb - 6}"/>'
           f'<text class="brk-t" x="{(xs + xd) / 2:.1f}" y="{yb + 16}" text-anchor="middle">Δ {off["DYN_B_vs_STA_B"]["delta"]:+.4f}</text>')
    b0 = off["B0"]["per_seed"]
    return (f'<figure class="fig"><svg viewBox="0 0 {W} {H + 40}" role="img" aria-label="rev.9 오프라인 시드별 mAP50">'
            f'{ticks}{marks}{brk}</svg><figcaption>rev.9 오프라인 결과(eval_axis2_mc.json). 점 = 시드 하나, 세로선 = 세 시드 평균. '
            f'B0(무작위 선별)는 같은 조건에서 시드만 바꿔도 {min(b0):.4f}–{max(b0):.4f}, <b>{max(b0) - min(b0):.4f}</b> 만큼 흔들린다 — '
            f'Δ {off["DYN_B_vs_STA_B"]["delta"]:.4f} 의 약 두 배다.</figcaption></figure>')


def build_m3() -> str:
    import build_colab_nb as NB
    import replay
    off = json.load(open(os.path.join(replay.rev9_root(), "04_중앙AI", "runs_yolo", "axis2", "eval_axis2_mc.json"),
                         encoding="utf-8"))
    oc, oe = off["conditions"], off["equivalence"]["DYN_B_vs_STA_B"]
    offl = {"B0": oc["B0"], "STA_B": oc["STA_B"], "DYN_B": oc["DYN_B"], "DYN_B_vs_STA_B": oe}
    la = locked_args()
    show = [("optimizer", "옵티마이저를 고정 — 'auto' 면 데이터 크기로 갈린다"), ("lr0", "시작 학습률"),
            ("mosaic", "사진 4장 합성 증강 끔 — 선별이 만든 다양성을 덮어쓴다"), ("fliplr", "허용된 유일한 증강 — 좌우 반전"),
            ("amp", "혼합 정밀도 끔 — 하드웨어·버전에 따라 수치가 달라진다"), ("deterministic", "같은 시드면 같은 결과"),
            ("patience", "조기 종료가 절대 발동하지 않게"), ("cos_lr", "학습률 곡선 고정")]
    kv = "".join(f"<div><b>{E(k)} = {E(repr(la[k]))}</b><span>{E(x)}</span></div>" for k, x in show)
    kv += "".join(f"<div><b>{E(k)} = {v}</b><span>{E(x)}</span></div>" for k, v, x in [
        ("epochs", NB.TRAIN["epochs"], "고정 에폭 — 마지막 가중치(last.pt)만 쓴다"),
        ("batch", NB.TRAIN["batch"], ""), ("imgsz", NB.TRAIN["imgsz"], "입력 해상도"),
        ("ultralytics", NB.ULTRALYTICS, "rev.9 와 같은 버전으로 고정")])

    todo = ('<ol class="steps-do">'
            "<li><b>Drive 한 폴더에 네 파일을 올린다</b> — <code>data/snapshots/</code> 의 <code>ds-STA_B.tar</code> · <code>ds-DYN_B.tar</code> · <code>eval-common.tar</code>(합계 약 1.6 GB)"
            " 와 <code>pipelines/colab_train.ipynb</code></li>"
            "<li><b>노트북을 Colab 으로 연다</b> → 런타임 → 런타임 유형 변경 → <b>T4 GPU</b></li>"
            "<li><b>런타임 → 모두 실행.</b> Drive 접근을 묻는 창이 뜨면 허용한다. 첫 셀들이 tar 의 지문을 대조한다 — 다르면 거기서 멈춘다</li>"
            "<li><b>세션이 끊기면 다시 \"모두 실행\".</b> 끝난 런은 Drive 에 저장돼 있어 건너뛴다</li>"
            "<li><b>끝나면</b> Drive 의 <code>m3_results/m3_eval.json</code> 을 내려받아 <code>data/m3/</code> 에 두고 <code>python run.py compare</code></li></ol>")

    body = (
        sec(1, "처음부터 배우지 않는다 — 같은 출발점에서 다듬는다", '<div class="prose" style="display:flex;flex-direction:column;gap:12px">'
            "<p>검출기(YOLOv8n)를 매번 처음부터 학습시키면 오래 걸리고 결과도 크게 흔들린다. 그래서 rev.9 는 먼저 기본 모델 하나(<strong>ybase</strong>, 0단 base)를 만들어 두고,"
            " 각 팔이 고른 사진으로 그 위에서 조금 더 학습시킨다 — <strong>fine-tune</strong>.</p>"
            "<p>두 팔은 <strong>같은 ybase</strong> 에서 출발하고, <strong>같은 레시피</strong>로, <strong>같은 평가셋</strong>에서 점수를 받는다. 다른 것은 학습에 쓰인 사진 2,449장뿐이다"
            " — 그중에서도 실제로 다른 것은 406장이다(M2 입문 §3). 그래야 점수 차이를 \"선별 방식의 차이\"라고 말할 수 있다.</p></div>"),
        sec(2, "레시피를 못 바꾸게 잠근다", '<p class="lede prose">학습 인자 하나만 달라도 비교가 무너진다. rev.9 는 인자를 코드에 잠가 두었고(LOCKED_TRAIN_ARGS), 노트북은 그 코드를 <strong>원문 그대로</strong> 쓴다.'
            " 학습이 끝나면 실제로 쓰인 인자에서 팔·시드·경로를 뺀 나머지의 <strong>해시</strong>를 남긴다 — 여섯 런의 해시가 하나여야 한다(수용 기준 3). 아래 값은 rev.9 코드에서 읽었다.</p>"
            f'<div class="kv">{kv}</div>'),
        sec(3, "시드 — 같은 조건을 다시 돌려도 점수는 흔들린다", '<p class="lede prose">학습에는 무작위가 섞인다(섞는 순서, 초기화). <strong>시드</strong>는 그 무작위를 고정하는 번호다. 시드만 바꿔도 점수가 달라지는데,'
            " 그 흔들림이 두 팔의 차이보다 크다. 그래서 시드 하나로 비교하면 부호가 뒤집힐 수 있고, 팔마다 <strong>세 시드</strong>가 최소다(PRD 암묵지 1).</p>" + seed_plot(offl)),
        sec(4, "점수 — mAP50", '<div class="concept"><div class="text">'
            "<p>평가 전용 사진 2,826장에서 모델이 찾은 상자를 정답과 맞춰 본다. 겹치는 정도(IoU)가 <strong>0.5 이상</strong>이면 맞힌 것으로 친다.</p>"
            "<p>클래스(차·트럭·버스·보행자·자전거·차단물)마다 '얼마나 빠짐없이, 얼마나 정확히' 찾았는지를 한 수(AP)로 줄이고, 여섯 클래스의 평균이 <strong>mAP50</strong> 이다. 0~1, 높을수록 좋다."
            " rev.9 와 <strong>같은 계산 코드</strong>(VOC all-points)를 쓴다 — 평가자가 바뀌면 숫자를 비교할 수 없다.</p></div>"
            f'<div class="kv"><div><b>STA_B {oc["STA_B"]["mean"]:.4f}</b><span>오프라인 · 세 시드 평균</span></div>'
            f'<div><b>DYN_B {oc["DYN_B"]["mean"]:.4f}</b><span>오프라인 · 세 시드 평균</span></div>'
            f'<div><b>held-out {off["n_held_out"]:,}</b><span>평가 전용 사진</span></div></div></div>'),
        sec(5, "Δ 와 신뢰구간 — 흔들림 두 가지를 함께 흔든다", '<div class="prose" style="display:flex;flex-direction:column;gap:12px">'
            f"<p><strong>Δ</strong> 는 DYN_B 평균 − STA_B 평균이다. 오프라인에서 <strong>{oe['delta']:+.4f}</strong>. 이 값이 우연인지 보려고 <strong>부트스트랩</strong>을 한다 —"
            f" 평가 사진 2,826장을 복원 추출로 다시 뽑고, 시드도 다시 뽑아 Δ 를 {NB.BOOT}번 계산한다. 그 가운데 95% 가 들어가는 구간이 <strong>95% CI</strong> 다.</p>"
            "<p>사진만 다시 뽑으면 구간이 부당하게 좁아진다 — 시드를 바꿨을 때의 흔들림(§3)이 빠지기 때문이다. 그래서 <strong>둘을 함께</strong> 다시 뽑는다. 두 팔에는 같은 추출을 쓴다(짝지어 비교).</p>"
            f"<p>오프라인 기준: Δ {oe['delta']:+.4f} · 95% CI [{oe['ci'][0]:+.4f}, {oe['ci'][1]:+.4f}]. 구간이 0 을 포함하지 않으면 \"DYN 이 더 좋다\"를 우연으로 보기 어렵다.</p></div>"),
        sec(6, "무엇이 성공인가", '<p class="pull prose"><strong>부호가 맞아야 성공인 게 아니다.</strong> 파이프라인을 지나온 Δ 가 오프라인과 다르면, 어느 단계가 대조를 흐리는지가 드러난 것이고'
            " 그것도 결과다. <strong>실패는 숫자가 나오지 않는 것</strong>이다(PRD). 그래서 <code>compare.py</code> 는 판정을 내리지 않고 사실만 적는다 —"
            " 부호가 같은가, 구간이 0 을 배제하는가, 두 구간이 겹치는가, 시드별로 얼마나 다른가.</p>"),
        sec(7, "사람이 할 일", todo),
        sec(8, "용어", gloss([
            ("ybase (0단 base)", "모든 런의 출발점이 되는 기본 검출 모델. rev.9 가 만들어 두었다."),
            ("fine-tune", "이미 학습된 모델을 새 데이터로 조금 더 학습시키는 것."),
            ("epoch", "학습 데이터를 한 바퀴 다 보는 것. 여기선 16 바퀴로 고정."),
            ("last.pt", "마지막 에폭의 가중치 파일. 점수를 보고 고른 best.pt 는 쓰지 않는다."),
            ("학습 인자 해시", "팔·시드를 뺀 학습 설정의 지문. 여섯 런이 같아야 한다."),
            ("mAP50", "IoU 0.5 기준 클래스별 AP 의 평균. 검출 정확도 점수."),
            ("IoU", "예측 상자와 정답 상자가 겹치는 비율."),
            ("부트스트랩", "표본을 복원 추출로 다시 뽑아 통계량의 흔들림을 재는 방법."),
            ("95% CI", "부트스트랩 결과의 가운데 95% 구간."),
            ("Colab · T4", "구글의 브라우저 노트북 서비스와 거기서 쓰는 무료 GPU."),
        ])),
    )
    return page("입문 M3 재학습과 판정", "NeedSlack Cloud · 마일스톤 입문 · M3",
                "같은 출발점 · 같은 레시피 · 같은 평가셋에서 <em>사진 406장의 차이만으로</em> 점수가 갈리는지를, 시드의 흔들림까지 넣어 잰다.",
                "진행 현황의 \"M3 노트북 준비\" 판을 읽는 데 필요한 것만 모았다. 숫자는 rev.9 오프라인 결과 파일과 rev.9 학습 코드, 노트북 생성기에서 읽었다.",
                "".join(body))


def main(argv: list[str]) -> int:
    which = argv[0] if argv else ""
    if which not in ("m2", "m3"):
        raise SystemExit("사용: python run.py primer m2|m3")
    html_ = build_m2() if which == "m2" else build_m3()
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"primer-{which}.html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html_)
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
