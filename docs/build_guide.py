# -*- coding: utf-8 -*-
"""build_guide.py — 도메인 입문 페이지를 굽는다. 수치는 계약·스냅샷에서 읽는다.

    python run.py guide                      # docs/_build/guide.html 생성
    python run.py guide <출력경로>

처음 합류한 사람이 "MQTT 가 뭐고, 왜 두 평면이고, 결국 무엇을 증명하나"를 한 페이지로
잡게 하는 것이 목적이다. 설명 문장은 여기 있고, 숫자는 전부 아래에서 온다
(docs/작업규칙.md §2 — 수치를 손으로 옮기지 않는다).

    schema/edge_contract.json   링크·수요·S·L_max·Q_min·δ_max·판정 지연 상한
    schema/arm_signals.json     두 팔의 타일 분포 · pool · 예산
    edge-agent/replay.py        번들 크기 · 배치 주기 · 텔레메트리 주기
    docs/build_status.py        마일스톤 단계 완료 · 운영 테이블 실측(스택이 떠 있을 때)
    ingest/                     수집기·조정기의 주기 상수 (스윕 간격)

rev.9 오프라인 결과(Δ mAP)만은 JSON 산출물이 없어 README 와 같은 값을 아래 상수로 둔다.
"""
from __future__ import annotations

import html
import io
import json
import math
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(ROOT, "edge-agent"), os.path.join(ROOT, "schema"), ROOT,
           os.path.join(ROOT, "docs")):
    sys.path.insert(0, _p)

import build_status as BS                     # noqa: E402  (마일스톤 판정 재사용)
import replay as RP                           # noqa: E402
import verdict as V                           # noqa: E402
from contract import load as load_contract    # noqa: E402

OUT_DEFAULT = os.path.join(ROOT, "docs", "_build", "guide.html")
#: rev.9 축 2 오프라인 결과 (docs/개발기록.md ★ 포지셔닝). JSON 산출물이 없는 유일한 수치
OFFLINE = {"sta": 0.2989, "dyn": 0.3230, "delta": 0.0241, "ci": (0.0115, 0.0369)}

E = html.escape


# ─────────────────────────────────────────────────────────── 그림
def svg_timescale(decide_ms: float, batch_s: float, lmax_s: float) -> str:
    """두 평면의 시간 척도 — 로그 축. 10 ms ~ 1 일."""
    lo, hi = math.log10(0.01), math.log10(86400)
    W, x0, x1 = 900, 40, 860
    X = lambda s: x0 + (x1 - x0) * (math.log10(s) - lo) / (hi - lo)   # noqa: E731
    ticks = [(0.01, "10 ms"), (1, "1 초"), (60, "1 분"), (3600, "1 시간"), (86400, "1 일")]
    t = "".join(
        f'<line x1="{X(v):.1f}" y1="150" x2="{X(v):.1f}" y2="156" class="ax"/>'
        f'<text x="{X(v):.1f}" y="172" class="tk" text-anchor="middle">{lab}</text>' for v, lab in ticks)
    d, b, l = X(decide_ms / 1000), X(batch_s), X(lmax_s)
    return f"""<svg viewBox="0 0 {W} 186" role="img" aria-label="판정은 {decide_ms:.0f} ms 안에 내려지고 1초 배치로 가지만, 사진은 최대 {lmax_s/3600:.2f} 시간 뒤 도착한다">
  <line x1="{x0}" y1="150" x2="{x1}" y2="150" class="ax"/>{t}
  <rect x="{x0}" y="34" width="{b - x0:.1f}" height="30" rx="3" class="lane-v"/>
  <text x="{x0 + 8}" y="54" class="lbl-in">판정 평면 · MQTT</text>
  <line x1="{d:.1f}" y1="28" x2="{d:.1f}" y2="150" class="mk"/>
  <text x="{d + 6:.1f}" y="22" class="mk-t">칩의 판정 ≤ {decide_ms:.0f} ms</text>
  <text x="{b + 8:.1f}" y="54" class="note">{batch_s:.0f} 초 배치로 도착</text>
  <rect x="{x0}" y="92" width="{l - x0:.1f}" height="30" rx="3" class="lane-p"/>
  <text x="{x0 + 8}" y="112" class="lbl-in">페이로드 평면 · S3 (사진)</text>
  <line x1="{l:.1f}" y1="86" x2="{l:.1f}" y2="150" class="mk"/>
  <text x="{l - 6:.1f}" y="82" class="mk-t" text-anchor="end">최대 {lmax_s/3600:.2f} 시간 뒤 도착</text>
</svg>"""


def svg_daycycle(growth_Bps: float, drain_Bps: float, duty: float, qmin: float) -> str:
    """계약의 일주기 — 최대 듀티로 주행하면 큐가 Q_min 까지 차고, 남은 시간에 정확히 빈다."""
    W, H, x0, x1, y0, y1 = 900, 250, 60, 870, 200, 30
    X = lambda h: x0 + (x1 - x0) * h / 24     # noqa: E731
    Y = lambda b: y0 - (y0 - y1) * b / qmin   # noqa: E731
    t_drive = duty * 24
    peak = growth_Bps * t_drive * 3600
    t_empty = t_drive + peak / drain_Bps / 3600
    grid = "".join(f'<line x1="{X(h):.1f}" y1="{y1}" x2="{X(h):.1f}" y2="{y0}" class="gr"/>'
                   f'<text x="{X(h):.1f}" y="{y0 + 18}" class="tk" text-anchor="middle">{h} h</text>'
                   for h in range(0, 25, 6))
    return f"""<svg viewBox="0 0 {W} {H}" role="img" aria-label="주행 {t_drive:.2f} 시간 동안 큐가 {qmin/1e9:.2f} GB 까지 차고, 주차 중 {24 - t_drive:.2f} 시간에 걸쳐 빈다">
  {grid}
  <line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" class="ax"/>
  <line x1="{x0}" y1="{Y(qmin):.1f}" x2="{x1}" y2="{Y(qmin):.1f}" class="cap"/>
  <text x="{x0 + 4}" y="{Y(qmin) - 6:.1f}" class="cap-t">큐 용량 Q_min {qmin/1e9:.2f} GB</text>
  <rect x="{X(0):.1f}" y="{y0 + 26}" width="{X(t_drive) - X(0):.1f}" height="8" class="band-d"/>
  <rect x="{X(t_drive):.1f}" y="{y0 + 26}" width="{X(24) - X(t_drive):.1f}" height="8" class="band-p"/>
  <text x="{X(t_drive / 2):.1f}" y="{y0 + 48}" class="tk" text-anchor="middle">주행 {t_drive:.2f} h — 수요 &gt; 링크, 큐가 찬다</text>
  <text x="{X((t_drive + 24) / 2):.1f}" y="{y0 + 48}" class="tk" text-anchor="middle">주차 {24 - t_drive:.2f} h — 링크만 쓰며 비운다</text>
  <path d="M{X(0):.1f},{Y(0):.1f} L{X(t_drive):.1f},{Y(peak):.1f} L{X(t_empty):.1f},{Y(0):.1f} Z" class="area"/>
  <path d="M{X(0):.1f},{Y(0):.1f} L{X(t_drive):.1f},{Y(peak):.1f} L{X(t_empty):.1f},{Y(0):.1f}" class="line"/>
  <circle cx="{X(t_drive):.1f}" cy="{Y(peak):.1f}" r="4.5" class="dot"/>
</svg>"""


# ─────────────────────────────────────────────────────────── 조립
def m1_proof(live, rep) -> str:
    """운영 테이블에 실린 두 팔의 상태 — 이 경로가 실제로 돈다는 증거. 스택이 없으면 문장만."""
    if live is None:
        return ('<p class="lede">스택이 내려가 있어 운영 테이블을 읽지 않았다. 띄운 뒤 '
                '<code>python run.py guide</code> 로 다시 구우면 실제 적재 상태가 여기 그려진다.</p>')
    done = [a for a in V.ARMS if BS.arm_complete(live, rep, a)]
    lat = {a: live["arms"][a]["latency"] for a in V.ARMS}
    txt = " · ".join(f"{a} 판정 {live['arms'][a]['states']['INDEXED'] + live['arms'][a]['states']['IN_DATASET']:,}건이 검증을 마쳤고, 도착 지연 최대 "
                     f"{lat[a][-1]:,.0f} s" for a in done)
    return (f'<div class="funnel">{"".join(BS.funnel(a, live["arms"][a]["states"]) for a in V.ARMS)}</div>'
            f'<p class="lede">지금 운영 테이블의 모습이다. {E(txt) if txt else "아직 끝까지 통과한 팔이 없다."}'
            f' — 재생기가 보고한 값과 전부 일치했다(진행 현황 페이지의 완료 판정).</p>')


def build() -> str:
    c = load_contract()
    sig = json.load(io.open(os.path.join(ROOT, "schema", "arm_signals.json"), encoding="utf-8"))
    op, arms = sig["operating_point"], sig["arms"]
    pos, todo_head, _ = BS.readme()
    rep, live = BS.replay_all(), BS.pipeline_live()
    runs = {a: BS.arm_complete(live, rep, a) for a in V.ARMS}
    sys.path.insert(0, os.path.join(ROOT, "ingest"))
    import reconciler as RC

    ms = ""
    for m in BS.milestones(runs):
        done = sum(1 for _, ok in m["steps"] if ok)
        state = "done" if done == len(m["steps"]) else ("active" if done else "idle")
        label = {"done": "완료", "active": "진행 중", "idle": "대기"}[state]
        ms += (f'<li class="rm {state}"><span class="rm-id">{m["m"]}</span>'
               f'<span class="rm-goal">{E(m["goal"])}</span><span class="rm-state">{label} '
               f'<span class="num">{done}/{len(m["steps"])}</span></span></li>')

    decide_ms = c.t_frame_ms
    ratio = c.max_arrival_latency_s / (decide_ms / 1000)
    ctx = dict(
        position=BS.md_inline(pos), todo_head=BS.md_inline(todo_head),
        sta=f"{OFFLINE['sta']:.4f}", dyn=f"{OFFLINE['dyn']:.4f}", delta=f"{OFFLINE['delta']:+.4f}",
        ci=f"[{OFFLINE['ci'][0]:+.4f}, {OFFLINE['ci'][1]:+.4f}]",
        tiles=BS.tile_bar("STA_B", arms["STA_B"]["tile_hist"], arms["STA_B"]["mean_tiles"])
        + BS.tile_bar("DYN_B", arms["DYN_B"]["tile_hist"], arms["DYN_B"]["mean_tiles"]),
        n_pool=f"{len(sig['pool_frame']):,}", budget=f"{op['budget']:,}",
        rate_pct=f"{c.select_rate * 100:.0f}", fps=f"{c.cand_rate_hz:.0f}",
        link=f"{c.uplink_bps / 1e6:g}", demand=f"{c.demand_bps / 1e6:.3f}",
        s_kb=f"{c.payload_bytes / 1000:.1f}", lmax_h=f"{c.max_arrival_latency_s / 3600:.2f}",
        qmin_gb=f"{c.queue_min_bytes / 1e9:.2f}", duty_h=f"{c.max_drive_duty * 24:.2f}",
        decide_ms=f"{decide_ms:.0f}", ratio="{:.1f} × 10<sup>{}</sup>".format(ratio / 10 ** math.floor(math.log10(ratio)),
                                                 math.floor(math.log10(ratio))),
        bundle=RP.BUNDLE_FRAMES, batch_s=f"{RP.BATCH_S:g}", tel_min=f"{RP.TELEMETRY_S / 60:g}",
        timescale=svg_timescale(decide_ms, RP.BATCH_S, c.max_arrival_latency_s),
        daycycle=svg_daycycle(c.queue_growth_bps, c.link_bytes_per_s, c.max_drive_duty,
                              c.queue_min_bytes),
        roadmap=ms, c_tile=f"{op['c_tile_ms']:.2f}",
        ingest_state=("완성" if os.path.exists(os.path.join(ROOT, "ingest", "reconciler.py")) else "다음에 만들 것"),
        sweep_s=f"{RC.SWEEP_EVERY_S:g}",
        m1_proof=m1_proof(live, rep),
        sta_tiles=f"{arms['STA_B']['mean_tiles']:.2f}", dyn_tiles=f"{arms['DYN_B']['mean_tiles']:.2f}",
    )
    return TEMPLATE.format(**ctx)


TEMPLATE = """<title>NeedSlack Cloud 입문</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Noto+Serif+KR:wght@500;700&display=swap">
<style>
:root{{
  --ground:#F5F2EB;--surface:#FFFDF8;--ink:#1B1916;--muted:#6B6355;--rule:#DDD5C6;--code:#EFE9DD;
  --accent:#8C6A22;--accent-soft:#F4E9D0;
  --sta:#5D7894;--dyn:#B9862C;--t1:#C9D5E1;--t2:#E3C27C;--t3:#A8741C;
  --plane-v:#2F6F8F;--plane-v-soft:#D6E7EF;--plane-p:#7A5C9A;--plane-p-soft:#E7DFF0;
  --ok:#3E7A53;--ok-soft:#DDEBDF;--warn:#A86A12;--warn-soft:#F6E6C8;--idle:#B3AA9A;
  --crit:#A63D32;--crit-soft:#F4DCD7;--up:#7FA88C;
  --sans:"IBM Plex Sans KR","Malgun Gothic","Apple SD Gothic Neo",sans-serif;
  --serif:"Noto Serif KR","Batang",serif;--mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{
  --ground:#161411;--surface:#1F1C18;--ink:#ECE6DA;--muted:#A49B8B;--rule:#38332C;--code:#2A2620;
  --accent:#D8B366;--accent-soft:#352C1C;
  --sta:#8EA9C6;--dyn:#DDAA4E;--t1:#3C4B5B;--t2:#9C7A35;--t3:#E0A945;
  --plane-v:#7DB9D6;--plane-v-soft:#1C3340;--plane-p:#B79BD6;--plane-p-soft:#2E2540;
  --ok:#72B58A;--ok-soft:#1E3326;--warn:#E3A94B;--warn-soft:#3A2D16;--idle:#5C554A;
  --crit:#E07A6C;--crit-soft:#3D1F1B;--up:#4F7A5E;
}}}}
:root[data-theme="dark"]{{
  --ground:#161411;--surface:#1F1C18;--ink:#ECE6DA;--muted:#A49B8B;--rule:#38332C;--code:#2A2620;
  --accent:#D8B366;--accent-soft:#352C1C;
  --sta:#8EA9C6;--dyn:#DDAA4E;--t1:#3C4B5B;--t2:#9C7A35;--t3:#E0A945;
  --plane-v:#7DB9D6;--plane-v-soft:#1C3340;--plane-p:#B79BD6;--plane-p-soft:#2E2540;
  --ok:#72B58A;--ok-soft:#1E3326;--warn:#E3A94B;--warn-soft:#3A2D16;--idle:#5C554A;
  --crit:#E07A6C;--crit-soft:#3D1F1B;--up:#4F7A5E;
}}
*{{box-sizing:border-box}}
body{{background:var(--ground);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.7;
  padding-inline:20px;padding-block:40px 80px}}
.wrap{{max-width:1040px;margin:0 auto;display:flex;flex-direction:column;gap:64px}}
.prose{{max-width:720px}}
h1,h2,h3{{text-wrap:balance;margin:0}}
h2{{font-size:24px;font-weight:700;letter-spacing:-.01em;line-height:1.35}}
h3{{font-size:17px;font-weight:600}}
p{{margin:0}}
code{{font-family:var(--mono);font-size:.86em;background:var(--code);padding:.08em .35em;border-radius:3px}}
.num{{font-family:var(--mono);font-variant-numeric:tabular-nums}}
.unit{{color:var(--muted);font-size:.8em}}
.eyebrow{{font-size:12.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:600}}
section{{display:flex;flex-direction:column;gap:20px}}
.sec-head{{display:flex;flex-direction:column;gap:6px}}
.sec-n{{font-family:var(--mono);font-size:13px;color:var(--accent)}}
.lede{{color:var(--muted);max-width:720px}}
strong{{font-weight:600}}
/* 머리 */
header{{display:flex;flex-direction:column;gap:18px;padding-bottom:32px;border-bottom:1px solid var(--rule)}}
.thesis{{font-family:var(--serif);font-weight:700;font-size:clamp(24px,3.6vw,36px);line-height:1.45;max-width:880px}}
.thesis em{{font-style:normal;color:var(--accent)}}
.hero-num{{display:flex;flex-wrap:wrap;gap:12px 32px;align-items:baseline}}
.hero-num .big{{font-family:var(--mono);font-size:30px;font-weight:500;color:var(--accent)}}
.hero-num span{{color:var(--muted);font-size:14px}}
.hero-num .where{{flex:1;min-width:260px;border-left:1px solid var(--rule);padding-left:24px}}
.hero-num .where p{{font-weight:500;font-size:15px}}
/* 비교 */
.pair{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
.card{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px;display:flex;flex-direction:column;gap:8px}}
.card .tag{{font-family:var(--mono);font-size:13px;font-weight:500}}
.card.sta_b .tag{{color:var(--sta)}}.card.dyn_b .tag{{color:var(--dyn)}}
.card .score{{font-family:var(--mono);font-size:24px}}
.tiles{{display:flex;flex-direction:column;gap:14px;background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px}}
.tile-row{{display:grid;grid-template-columns:64px 1fr auto;grid-template-areas:"arm bar mean" ". legend legend";gap:4px 14px;align-items:center}}
.arm{{font-family:var(--mono);font-size:13px;font-weight:500;grid-area:arm}}
.arm.sta_b{{color:var(--sta)}}.arm.dyn_b{{color:var(--dyn)}}
.tile-bar{{grid-area:bar;display:flex;height:26px;border-radius:4px;overflow:hidden;gap:2px}}
.seg{{display:flex;align-items:center;justify-content:center;font-family:var(--mono);font-size:12px;color:var(--ink)}}
.seg.t1{{background:var(--t1)}}.seg.t2{{background:var(--t2)}}.seg.t3{{background:var(--t3);color:var(--surface)}}
.tile-mean{{grid-area:mean;display:flex;gap:6px;align-items:baseline}}
.tile-mean .num{{font-size:20px;font-weight:500}}
.tile-legend{{grid-area:legend;font-size:12.5px;color:var(--muted)}}
/* 흐름도 */
.flow{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:22px;overflow-x:auto}}
.flow-grid{{display:grid;grid-template-columns:150px 28px 1fr 28px 1fr 28px 1fr;gap:12px 0;align-items:center;min-width:760px}}
.node{{border:1px solid var(--rule);border-radius:5px;padding:10px 12px;background:var(--ground);font-size:13.5px;line-height:1.45}}
.node b{{display:block;font-size:14.5px}}
.node small{{color:var(--muted);font-size:12px}}
.node.car{{grid-row:1 / span 2;align-self:stretch;display:flex;flex-direction:column;justify-content:center;border-color:var(--accent);background:var(--accent-soft)}}
.node.v{{border-color:var(--plane-v);background:var(--plane-v-soft)}}
.node.p{{border-color:var(--plane-p);background:var(--plane-p-soft)}}
.arrow{{text-align:center;color:var(--muted);font-size:18px}}
.flow-tail{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px;padding-top:16px;border-top:1px dashed var(--rule);min-width:760px}}
.flow-tail .node{{flex:1;min-width:150px}}
.flow-tail .node.goal{{border-color:var(--accent);background:var(--accent-soft)}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;font-size:13px;color:var(--muted)}}
.legend i{{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-1px;margin-right:6px}}
/* 사전 */
.dict{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
.entry{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:18px 20px;display:flex;flex-direction:column;gap:10px}}
.entry-head{{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap}}
.entry .what{{font-size:13px;color:var(--muted)}}
.entry dl{{margin:0;display:grid;grid-template-columns:auto 1fr;gap:6px 12px;font-size:14px}}
.entry dt{{color:var(--muted);font-size:12.5px;padding-top:2px;white-space:nowrap}}
.entry dd{{margin:0}}
.plane{{font-size:11.5px;padding:1px 8px;border-radius:999px;font-weight:500;white-space:nowrap}}
.plane.v{{background:var(--plane-v-soft);color:var(--plane-v)}}
.plane.p{{background:var(--plane-p-soft);color:var(--plane-p)}}
.plane.n{{background:var(--code);color:var(--muted)}}
/* 개념 */
.concept{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:24px;align-items:start}}
.concept.full{{grid-template-columns:1fr}}
.concept .text{{display:flex;flex-direction:column;gap:10px}}
.fig{{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:16px;overflow-x:auto}}
.fig svg{{width:100%;min-width:560px;height:auto;display:block}}
.fig figcaption{{font-size:12.5px;color:var(--muted);margin-top:8px}}
figure{{margin:0}}
svg text{{font-family:var(--sans);fill:var(--ink)}}
svg .tk{{font-size:12px;fill:var(--muted);font-family:var(--mono)}}
svg .ax{{stroke:var(--muted);stroke-width:1}}
svg .gr{{stroke:var(--rule);stroke-width:1}}
svg .lane-v{{fill:var(--plane-v)}}svg .lane-p{{fill:var(--plane-p)}}
svg .lbl-in{{font-size:13px;fill:var(--surface);font-weight:600}}
svg .mk{{stroke:var(--ink);stroke-width:1.5;stroke-dasharray:3 3}}
svg .mk-t{{font-size:12.5px;font-weight:600}}
svg .note{{font-size:12.5px;fill:var(--muted)}}
svg .cap{{stroke:var(--warn);stroke-width:1.5;stroke-dasharray:6 4}}
svg .cap-t{{font-size:12.5px;fill:var(--warn);font-weight:600}}
svg .band-d{{fill:var(--accent)}}svg .band-p{{fill:var(--plane-v)}}
svg .area{{fill:var(--accent-soft)}}
svg .line{{fill:none;stroke:var(--accent);stroke-width:2.5}}
svg .dot{{fill:var(--accent)}}
.states{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:14px}}
.st{{font-family:var(--mono);font-size:12.5px;padding:4px 10px;border-radius:4px;background:var(--code)}}
.st.pending{{background:var(--warn-soft);color:var(--warn);font-weight:500}}
.st.ok{{background:var(--ok-soft);color:var(--ok)}}
.st.drop{{background:var(--code);color:var(--muted);text-decoration:line-through}}
.st.crit{{background:var(--crit-soft);color:var(--crit);font-weight:500}}
/* 수집·조정 */
.order{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}}
.order .card h4{{margin:0;font-size:14.5px;font-weight:600}}
.order ol{{margin:0;padding-left:1.2em;display:flex;flex-direction:column;gap:6px;font-size:14px}}
.inputs{{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}}
.inputs th,.inputs td{{text-align:left;padding:9px 12px;border-bottom:1px solid var(--rule);vertical-align:top}}
.inputs th{{color:var(--muted);font-weight:500;font-size:13px}}
.inputs td:first-child{{font-weight:600;white-space:nowrap}}
.tbl{{overflow-x:auto;background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:4px 8px}}
.guar{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule);border-radius:6px;overflow:hidden}}
.guar div{{background:var(--surface);padding:14px 16px;display:flex;flex-direction:column;gap:6px}}
.guar b{{font-size:14.5px}}.guar span{{font-size:13.5px;color:var(--muted);line-height:1.55}}
.guar .eq{{background:var(--accent-soft)}}.guar .eq b{{color:var(--accent)}}
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
.formula{{font-family:var(--mono);font-size:14px;background:var(--code);padding:12px 14px;border-radius:5px;overflow-x:auto;white-space:nowrap}}
.pull{{border-left:3px solid var(--accent);padding:2px 0 2px 16px}}
/* 로드맵 */
.roadmap{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}}
.rm{{display:grid;grid-template-columns:52px 1fr auto;gap:14px;align-items:baseline;padding:12px 0;border-bottom:1px solid var(--rule)}}
.rm-id{{font-family:var(--mono);font-weight:500;color:var(--muted)}}
.rm.active .rm-id{{color:var(--warn)}}.rm.done .rm-id{{color:var(--ok)}}
.rm-goal{{font-weight:500}}
.rm-state{{font-size:13px;color:var(--muted)}}
.rm.active .rm-state{{color:var(--warn)}}
/* 용어 */
.gloss{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule);border-radius:6px;overflow:hidden}}
.gloss div{{background:var(--surface);padding:14px 16px;display:flex;flex-direction:column;gap:4px}}
.gloss b{{font-size:14.5px}}
.gloss span{{font-size:13.5px;color:var(--muted);line-height:1.55}}
footer{{border-top:1px solid var(--rule);padding-top:24px;display:flex;flex-direction:column;gap:12px}}
footer .thesis{{font-size:clamp(19px,2.6vw,24px)}}
@media (max-width:720px){{.concept{{grid-template-columns:1fr}}}}
@media (max-width:560px){{
  .tile-row{{grid-template-columns:56px 1fr;grid-template-areas:"arm bar" "mean mean" "legend legend"}}
  .rm{{grid-template-columns:44px 1fr}}.rm-state{{grid-column:2}}
  .fn-row{{grid-template-columns:56px 1fr;grid-template-areas:"arm bar" "tot tot" "legend legend"}}.fn-tot{{text-align:left}}
}}
</style>
<div class="wrap">

<header>
  <div class="eyebrow">NeedSlack Cloud · 처음 합류한 사람을 위한 입문</div>
  <p class="thesis">차 안의 칩이 <em>더 잘 고른</em> 데이터가, 느리고 불완전한 실제 전송 경로를 지나
  클라우드에서 재학습까지 가도 <em>그 우위가 살아남는가</em>.</p>
  <div class="hero-num">
    <div><div class="big">{delta}</div><span>rev.9 오프라인 Δ mAP · 95% CI {ci}</span></div>
    <div class="where"><span>지금 위치 (README)</span><p>{position}</p></div>
  </div>
  <p class="lede prose">이 프로젝트가 증명하려는 것은 위 한 문장이 전부다. 아래는 그 문장을 이해하는 데 필요한 것을
  쉬운 순서로 놓았다. 숫자는 이 페이지를 만들 때 계약·스냅샷 파일에서 읽었다.</p>
</header>

<section>
  <div class="sec-head"><span class="sec-n">1</span><h2>출발점 — 이미 끝난 실험 하나</h2></div>
  <div class="prose" style="display:flex;flex-direction:column;gap:12px">
  <p>자율주행 차의 카메라는 초당 <span class="num">{fps}</span>장을 찍는다. 전부 서버로 보내면 통신과 저장을 감당할 수 없어서,
  <strong>차 안의 칩(엣지 가속기)이 학습에 쓸 만한 {rate_pct}%만 골라</strong> 보낸다.</p>
  <p>칩은 사진을 타일로 나눠 채점하는데, 채점할 시간은 차의 본업(주행 AI)이 끝나고 남는 틈 — <strong>슬랙</strong> — 뿐이다.
  그 틈을 어떻게 쓰느냐가 두 방식을 가른다. 이 두 방식을 <strong>팔(arm)</strong>이라고 부른다.</p>
  </div>
  <div class="pair">
    <div class="card sta_b"><span class="tag">STA_B · 정적</span><p>최악의 경우를 대비해 틈을 보수적으로 고정한다.</p>
      <div class="score">mAP50 {sta}</div></div>
    <div class="card dyn_b"><span class="tag">DYN_B · 동적</span><p>실제로 남는 틈을 그때그때 회수한다. 같은 시간에 더 많이 본다.</p>
      <div class="score">mAP50 {dyn}</div></div>
  </div>
  <div class="tiles">{tiles}</div>
  <p class="lede">두 팔은 같은 후보 {n_pool}장에서 같은 {budget}장을 고른다. 다른 것은 <strong>한 장을 몇 타일이나 들여다봤느냐</strong>뿐이다
  ({sta_tiles} 대 {dyn_tiles} 타일, 타일 하나에 {c_tile} ms). 더 많이 본 쪽이 더 좋은 사진을 골랐고, 그걸로 학습한 AI 가 더 잘했다.</p>
  <p class="pull prose"><strong>문제:</strong> 이 결과는 PC 안의 시뮬레이션에서만 나왔다. "그 데이터가 정말 클라우드로 가서 재학습까지 이어지나?"라는
  질문에 지금은 말로만 답할 수 있다. <strong>이 프로젝트는 그 경로를 실제로 만들어 답한다.</strong></p>
</section>

<section>
  <div class="sec-head"><span class="sec-n">2</span><h2>전체 흐름 — 한 장의 그림</h2></div>
  <p class="lede">실제 차량과 AWS 대신, 전부 내 PC 의 Docker 컨테이너로 대신한다(비용 $0). 두 팔을 이 경로에 <strong>함께</strong> 흘린다 —
  한 팔만 흘리면 비교가 사라진다.</p>
  <div class="flow">
    <div class="flow-grid">
      <div class="node car"><b>차량</b>시뮬레이터 <code>replay.py</code><small>후보 전량을 {fps} Hz 로 재생 · 칩이 {rate_pct}% 선별</small></div>
      <div class="arrow">→</div>
      <div class="node v"><b>판정 메시지</b>"이 사진 골랐음 · 몇 점"<small>MQTT · {batch_s}초 배치</small></div>
      <div class="arrow">→</div>
      <div class="node v"><b>Mosquitto</b>메시지 중계소<small>실제 서비스: AWS IoT Core</small></div>
      <div class="arrow">→</div>
      <div class="node v"><b>수집기 → DynamoDB</b>사진별 상태 장부<small>{ingest_state} · 판정 → PENDING</small></div>
      <div class="arrow">→</div>
      <div class="node p"><b>사진 {bundle}장 묶음</b>업링크 큐를 거쳐 늦게<small>최대 {lmax_h} h 지연</small></div>
      <div class="arrow">→</div>
      <div class="node p"><b>MinIO</b>파일 창고<small>실제 서비스: Amazon S3</small></div>
      <div class="arrow">→</div>
      <div class="node p"><b>조정기</b>메모와 사진의 짝 맞춤<small>{ingest_state} · 체크섬 → INDEXED</small></div>
    </div>
    <div class="flow-tail">
      <div class="node"><b>데이터셋 동결</b>PostgreSQL 에 계보 기록</div><div class="arrow">→</div>
      <div class="node"><b>재학습</b>Colab 무료 GPU · 2팔 × 3시드</div><div class="arrow">→</div>
      <div class="node goal"><b>Δ mAP 대조표</b>{delta} 가 재현되나</div>
    </div>
  </div>
  <div class="legend"><span><i style="background:var(--plane-v)"></i>판정 평면 — 작고 빠르다</span>
    <span><i style="background:var(--plane-p)"></i>페이로드 평면 — 크고 느리다</span></div>
</section>

<section>
  <div class="sec-head"><span class="sec-n">3</span><h2>구성요소 사전</h2></div>
  <p class="lede">여섯 가지 모두 "진짜 클라우드 서비스의 무료 대역"이다. 같은 API 를 쓰므로 주소만 바꾸면 AWS 로 올라간다 — 코드를 두 번 쓰지 않는다.</p>
  <div class="dict">
    <div class="entry"><div class="entry-head"><h3>MQTT</h3><span class="plane v">판정 평면</span></div>
      <p class="what">통신 <strong>규칙</strong>의 이름. 소프트웨어가 아니다.</p>
      <dl><dt>무엇</dt><dd>연결이 불안정한 기기들이 짧은 메시지를 주고받는 경량 프로토콜. 보내는 쪽은 <strong>주제(topic)</strong>에 올리고, 그 주제를 구독한 쪽이 받는다 — 단톡방 이름과 비슷하다.</dd>
      <dt>여기서</dt><dd>주제 <code>ns/v1/sim-DYN_B/curation/verdict</code> 에 판정을, <code>…/telemetry</code> 에 {tel_min}분 집계를 올린다.</dd>
      <dt>주의</dt><dd><strong>QoS 1</strong> = "최소 한 번 배달". 잃어버리진 않지만 <strong>두 번 올 수 있다</strong> → 멱등 키가 필요하다(개념 ④).</dd></dl></div>
    <div class="entry"><div class="entry-head"><h3>Mosquitto</h3><span class="plane v">판정 평면</span></div>
      <p class="what">MQTT 규칙을 따르는 <strong>우체국(브로커)</strong>. 오픈소스.</p>
      <dl><dt>무엇</dt><dd>메시지를 받아 구독자에게 나눠 주는 중계 서버.</dd>
      <dt>대신하는 것</dt><dd>AWS IoT Core</dd>
      <dt>여기서</dt><dd>컨테이너 <code>ns-mosquitto</code>, 포트 1883.</dd></dl></div>
    <div class="entry"><div class="entry-head"><h3>MinIO</h3><span class="plane p">페이로드 평면</span></div>
      <p class="what">내 PC 안의 <strong>Amazon S3</strong>.</p>
      <dl><dt>무엇</dt><dd>사진·영상 같은 큰 파일을 "버킷"에 넣는 객체 저장소. S3 와 같은 명령을 알아듣는다.</dd>
      <dt>대신하는 것</dt><dd>Amazon S3</dd>
      <dt>여기서</dt><dd>버킷 <code>ns-raw-payload</code> 에 사진 {bundle}장을 한 파일(번들)로 묶어 넣는다 — 요청 수가 {bundle}분의 1 로 준다. 판정이 번들 안의 위치를 들고 있어 한 장만 꺼내 읽을 수 있다.</dd></dl></div>
    <div class="entry"><div class="entry-head"><h3>DynamoDB Local</h3><span class="plane n">클라우드 장부</span></div>
      <p class="what">택배 운송장 조회 시스템 같은 <strong>빠른 키-값 장부</strong>.</p>
      <dl><dt>무엇</dt><dd>사진 한 장마다 상태(골랐음 · 도착 · 검증)를 대량으로 빠르게 기록·조회.</dd>
      <dt>대신하는 것</dt><dd>Amazon DynamoDB</dd>
      <dt>여기서</dt><dd>테이블 하나에 세 종류를 담는다 — 사진 <code>FRAME#</code>, 묶음 <code>BUNDLE#</code>, 집계 <code>VIN#…/TEL#</code>.
      "이 차의 INDEXED 전부"처럼 상태로 찾는 길은 보조 색인 <strong>GSI1</strong> 이 낸다.</dd>
      <dt>주의</dt><dd>로컬판은 호출 한 번에 약 76 ms 로 느리다. 결과는 같고 시간만 더 든다.</dd></dl></div>
    <div class="entry"><div class="entry-head"><h3>PostgreSQL</h3><span class="plane n">클라우드 장부</span></div>
      <p class="what">관계를 정확히 따지는 <strong>회계 장부</strong>.</p>
      <dl><dt>무엇</dt><dd>"어느 데이터셋으로 어느 모델을 만들어 몇 점이 나왔나"의 계보. 점수 하나에서 사진 한 장까지 조인 한 번으로 거슬러 올라간다.</dd>
      <dt>대신하는 것</dt><dd>Amazon Aurora</dd></dl></div>
    <div class="entry"><div class="entry-head"><h3>Docker</h3><span class="plane n">실행 환경</span></div>
      <p class="what">위 프로그램들을 <strong>독립된 상자(컨테이너)</strong>에 담아 띄우는 도구.</p>
      <dl><dt>여기서</dt><dd><code>python run.py up</code> 한 줄로 네 컨테이너가 뜬다. Windows Home 에서는 WSL2 위에서 돈다.</dd></dl></div>
  </div>
</section>

<section>
  <div class="sec-head"><span class="sec-n">4</span><h2>핵심 개념 다섯</h2></div>

  <div class="concept full">
    <div class="text prose"><h3>① 판정과 사진을 따로 보낸다 — 도착 시간이 {ratio} 배 다르다</h3>
    <p>판정은 수백 바이트라 곧바로 간다. 사진은 한 장 약 {s_kb} KB 이고, 고른 사진만 보내도 <span class="num">{demand}</span> Mbps 가 필요한데
    차의 링크는 <span class="num">{link}</span> Mbps 다. 그래서 사진은 차 안의 큐에 쌓였다가 늦게 도착한다. 성격이 전혀 다른 두 흐름은
    같은 통로를 쓸 수 없다 — 이것이 "평면을 나눈다"는 말의 뜻이다.</p></div>
    <figure class="fig">{timescale}<figcaption>로그 축. 계약 파일(edge_contract.json)의 판정 지연 상한과 도착 지연 상한 L_max.</figcaption></figure>
  </div>

  <div class="concept full">
    <div class="text prose"><h3>② 차 안의 큐는 하루 주기로 차고 빈다 — 넘치면 즉시 버린다</h3>
    <p>주행 중에는 수요가 링크보다 커서 큐가 찬다. 주차 중에는 링크만 쓰며 비운다(주차 중에도 링크가 살아 있다는 것이 이 계약의 가장 강한 가정이다).
    하루 최대 <span class="num">{duty_h}</span> 시간 주행하면 큐가 정확히 <span class="num">{qmin_gb}</span> GB 까지 차고 남은 시간에 정확히 빈다 —
    큐 용량과 도착 지연 상한은 이 한 주기에서 <strong>유도된</strong> 값이다. 큐가 차면 새 사진을 <strong>기다리지 않고 버린다</strong>. 기다리게 하면 지연이 끝없이 늘어난다.
    <code>uplink.py</code> 의 토큰버킷이 이것을 흉내 낸다.</p></div>
    <figure class="fig">{daycycle}<figcaption>계약의 최대 듀티에서의 큐 점유. 실제 재생 한 번(약 20분 주행)은 이 곡선의 맨 앞 조각이라 큐가 수백 MB 에서 멈춘다.</figcaption></figure>
  </div>

  <div class="concept">
    <div class="text"><h3>③ PENDING 은 정상이다</h3>
    <p>판정은 먼저, 사진은 몇 시간 뒤에 온다. 그래서 클라우드 기록은 대부분 한동안 <strong>사진 대기(PENDING)</strong>에 머문다.
    이걸 오류로 경보하면 운영이 마비된다. 상태는 <strong>한 방향으로만</strong> 간다 — 되돌아가는 전이는 코드가 거부한다.</p>
    <p>누가 옮기나: 판정이 오면 <strong>수집기</strong>가 PENDING 으로 적는다. 사진 묶음이 오면 <strong>조정기</strong>가 UPLOADED 로 옮기고,
    사진을 잘라 지문(sha256)을 대조해 INDEXED 로 올린다. 지문이 다르면 QUARANTINED 로 격리한다.</p></div>
    <div class="fig"><div class="states"><span class="st pending">PENDING</span>→<span class="st ok">UPLOADED</span>→<span class="st ok">INDEXED</span>→<span class="st ok">IN_DATASET</span></div>
      <div class="states" style="margin-top:10px"><span class="st ok">UPLOADED</span>→<span class="st crit">QUARANTINED</span><span style="color:var(--muted);font-size:13px">지문 불일치 · 범위 초과 — 학습에 넣지 않는다</span></div>
      <div class="states" style="margin-top:10px"><span class="st pending">PENDING</span>→<span class="st drop">DROPPED</span><span style="color:var(--muted);font-size:13px">큐가 넘쳐 버려짐 — 텔레메트리로 보고</span></div>
      <div class="states" style="margin-top:14px;font-size:12.5px;color:var(--muted)">IN_DATASET 은 M2 스냅샷이 옮긴다.</div></div>
  </div>

  <div class="concept">
    <div class="text"><h3>④ 사진마다 전 세계에서 하나뿐인 번호</h3>
    <p>칩의 사진 번호는 차를 껐다 켜면 0부터 다시 센다. 그래서 네 가지를 섞어 고유 번호를 만든다. MQTT 가 같은 메시지를 두 번 보내도
    번호가 같으니 장부에는 한 번만 남는다 — <strong>멱등성</strong>.</p></div>
    <div class="fig"><div class="formula">frame_uuid = SHA256( 차량 ‖ 부팅 ID ‖ 주행 세션 ‖ 사진 번호 )</div></div>
  </div>

  <div class="concept">
    <div class="text"><h3>⑤ 고른 것만 재생하면 안 된다</h3>
    <p>고른 {budget}장만 흘리면 큐·토큰버킷·드롭이 한 번도 돌지 않는다 — 증명하려는 전송 계약이 검증되지 않는다.
    그래서 재생기는 <strong>후보 {n_pool}장 전부</strong>를 흘리고, 고른 것만 업로드한다. 고르지 않은 것의 정보는 {tel_min}분 집계로 올린다.</p></div>
    <div class="fig"><div class="states"><span class="st">후보 {n_pool}</span>→<span class="st ok">선별 {budget} · 판정·사진</span></div>
      <div class="states" style="margin-top:10px"><span class="st">나머지</span>→<span class="st">집계 텔레메트리</span></div></div>
  </div>
</section>

<section>
  <div class="sec-head"><span class="sec-n">5</span><h2>수집과 조정 — 장부가 맞춰지는 법</h2></div>
  <p class="lede prose">판정과 사진이 따로, 늦게, 때로는 두 번 도착한다. 그래도 장부에는 사진마다 정확히 한 줄이 남고
  끝내 INDEXED 에 닿아야 한다. M1 에서 만든 수집기와 조정기가 이것을 어떻게 지키는지가 지금 진행 현황을 읽는 열쇠다.</p>

  <div class="concept full">
    <div class="text prose"><h3>⑥ "최소 한 번 배달" + "여러 번 해도 같다" = 결과는 한 번</h3>
    <p>MQTT 는 메시지를 잃지 않는 대신 <strong>두 번 보낼 수 있다</strong>. 그래서 받는 쪽이 두 가지를 한다.
    처리를 <strong>다 마친 뒤에만</strong> "받았다(PUBACK)"를 보낸다 — 도중에 죽으면 브로커가 다시 보낸다.
    그리고 장부 쓰기를 <strong>멱등</strong>하게 만든다 — 같은 판정이 또 오면 "이미 있으면 쓰지 않는다(조건부 쓰기)"가 막는다.</p>
    <p>받는 쪽이 꺼져 있어도 메시지는 사라지지 않는다. 수집기·조정기는 고정 이름의 <strong>영속 세션</strong>으로 구독하고,
    브로커가 그 이름 앞으로 온 메시지를 보관했다가 다시 켜지면 넘긴다.</p></div>
    <div class="guar">
      <div><b>QoS 1</b><span>잃지 않는다. 대신 중복이 올 수 있다.</span></div>
      <div><b>영속 세션</b><span>구독자가 꺼져 있던 동안의 메시지를 브로커가 보관한다. 기본 한도 1,000건을 넘으면 경고 없이 버려서 100,000 으로 올렸다.</span></div>
      <div><b>수동 확인</b><span>처리를 마친 메시지만 PUBACK. 저장소가 실패하면 확인하지 않고 멈춘다 → 재시작 때 다시 온다.</span></div>
      <div><b>조건부 쓰기 · 집합 ADD</b><span>같은 것이 여러 번 와도 장부는 한 번 쓴 것과 같다.</span></div>
      <div class="eq"><b>= 정확히 한 번의 결과</b><span>배달은 여러 번일 수 있어도, 장부에 남는 결과는 한 번이다.</span></div>
    </div>
  </div>

  <div class="concept full">
    <div class="text prose"><h3>⑦ 판정과 사진, 어느 쪽이 먼저 와도 된다</h3>
    <p>보통은 판정이 먼저 온다. 하지만 재전송·재시작이 섞이면 사진 묶음이 먼저 올 수도 있다. 둘은 <strong>묶음마다 한 줄</strong>
    (<code>BUNDLE#</code>)에서 만난다. 수집기는 거기에 "이 묶음에 든 판정 목록"을 보태고, 조정기는 "도착했다"를 적는다.
    둘 다 쓴 직후의 한 줄 전체를 돌려받기 때문에, <strong>나중에 온 쪽이 반드시 먼저 온 쪽의 기록을 본다</strong>.</p></div>
    <div class="order">
      <div class="card"><h4>판정이 먼저 (보통)</h4><ol>
        <li>수집기: 판정을 PENDING 으로 적고, 묶음 줄에 판정 번호를 보탠다 — 아직 "도착" 표시가 없다.</li>
        <li>조정기: 묶음이 오면 "도착"을 적고, 돌려받은 판정 번호 전부를 UPLOADED → 지문 대조 → INDEXED.</li></ol></div>
      <div class="card"><h4>사진이 먼저</h4><ol>
        <li>조정기: "도착"을 적는다 — 판정 번호가 아직 없어 할 일이 없다.</li>
        <li>수집기: 판정 번호를 보태며 "도착" 표시를 본다 → 자기 판정을 바로 UPLOADED.</li>
        <li>조정기의 <strong>스윕</strong>이 UPLOADED 로 남은 것을 찾아 지문을 대조한다(⑧).</li></ol></div>
      <div class="card"><h4>사진이 끝내 안 온다</h4><ol>
        <li>판정은 PENDING 으로 남는다. 이것이 정상이다(③).</li>
        <li>알람 기준은 도착 지연 상한 L_max 의 1.5배를 넘겼을 때다 — 오래 머문 것 자체가 아니다.</li></ol></div>
    </div>
  </div>

  <div class="concept full">
    <div class="text prose"><h3>⑧ 조정기는 세 군데서 일을 받는다</h3>
    <p>"사진이 도착했다"는 소식(버킷 알림)만 믿으면, 소식이 빠지는 순간 그 사진은 영영 PENDING 에 남는다. 그래서 소식 말고도 두 길을 더 둔다.</p></div>
    <div class="tbl"><table class="inputs"><thead><tr><th>입력</th><th>언제</th><th>메우는 틈</th></tr></thead><tbody>
      <tr><td>버킷 알림</td><td>묶음이 올라올 때마다 — MinIO 가 MQTT 로 알린다</td><td>주 경로</td></tr>
      <tr><td>버킷 대조</td><td>조정기가 켜질 때 한 번</td><td>조정기가 없던 동안 올라온 묶음 · 잃어버린 알림</td></tr>
      <tr><td>UPLOADED 스윕</td><td>{sweep_s} 초마다</td><td>사진이 먼저 와서 수집기가 UPLOADED 로만 옮겨 둔 판정 · 검증 도중 죽은 경우</td></tr>
    </tbody></table></div>
  </div>

  <div class="concept">
    <div class="text"><h3>⑨ 지문 대조 — 사진이 정말 그 사진인가</h3>
    <p>판정은 차를 떠날 때 이미 "묶음 안 몇 번째 바이트부터 몇 바이트, 지문(sha256)은 이것"을 들고 있다. 조정기는 묶음을 한 번 받아
    그 구간을 잘라 지문을 다시 계산한다. 같으면 INDEXED, 다르거나 범위를 넘으면 QUARANTINED — 망가진 사진이 학습에 섞이지 않는다.
    묶음의 경로 앞머리(<code>vin=…/</code>)가 가리키는 차와 첨부 정보의 차가 다르면 그 소식 자체를 버린다 — 한 차가 남의 사진을 올릴 수 없게.</p></div>
    <div class="fig"><div class="formula">sha256( 묶음[ offset : offset + length ] ) == 판정.payload.sha256</div></div>
  </div>

  <div class="concept">
    <div class="text"><h3>⑩ 시계가 둘이다</h3>
    <p>재생은 12.5배속으로 돌 수 있다. 그래서 차가 적은 시각은 <strong>차량 시계</strong>(모의 시각)이고, 클라우드가 받은 시각은 <strong>벽시계</strong>다.
    도착 지연은 반드시 차량 시계끼리 뺀다 — 벽시계로 재면 가속 재생에서 12.5배 짧게 나온다. 장부의 <code>arrival_latency_s</code> 가 그 값이고,
    재생기가 스스로 잰 최대값과 같아야 한다.</p></div>
    <div class="fig"><div class="formula">arrival_latency_s = t_put_utc (묶음 업로드, 차량 시계) − t_frame_utc (사진, 차량 시계)</div></div>
  </div>

  {m1_proof}
</section>

<section>
  <div class="sec-head"><span class="sec-n">6</span><h2>로드맵 — 어디까지 왔나</h2></div>
  <p class="lede">단계 완료는 산출물 파일이 저장소에 있는지로 판정했다. 지금 먼저 할 일: {todo_head}.</p>
  <ul class="roadmap">{roadmap}</ul>
  <p class="pull prose"><strong>M3 이 이 프로젝트의 답이다.</strong> 숫자가 오프라인과 달라도 실패가 아니다 — 어느 단계가 차이를 흐리는지 드러나는 것 자체가
  결과다. 실패는 숫자가 <strong>나오지 않는</strong> 것이다.</p>
</section>

<section>
  <div class="sec-head"><span class="sec-n">7</span><h2>용어</h2></div>
  <div class="gloss">
    <div><b>엣지 · 엣지 가속기</b><span>데이터가 생기는 현장(차) 쪽. 거기서 도는 전용 칩.</span></div>
    <div><b>슬랙</b><span>본업(주행 AI)이 끝나고 다음 프레임까지 남는 시간. 채점은 이 틈에만 한다.</span></div>
    <div><b>타일</b><span>사진을 나눈 조각. 채점 단위. 많이 볼수록 더 잘 고른다.</span></div>
    <div><b>팔 (arm)</b><span>비교 실험의 한 쪽. STA_B(정적) · DYN_B(동적).</span></div>
    <div><b>mAP</b><span>물체 인식 정확도 점수. 0~1, 높을수록 좋다.</span></div>
    <div><b>시드</b><span>학습의 무작위성을 고정하는 번호. 시드 간 차이가 Δ 보다 커서 3개가 최소다.</span></div>
    <div><b>held-out</b><span>학습에 절대 넣지 않는 평가 전용 사진. 섞이면 모든 숫자가 무의미해진다.</span></div>
    <div><b>평면 (plane)</b><span>성격이 다른 데이터 흐름의 통로. 판정 평면과 페이로드 평면.</span></div>
    <div><b>번들</b><span>사진 {bundle}장을 묶은 파일 하나. 판정이 그 안의 위치를 가리킨다.</span></div>
    <div><b>토큰버킷</b><span>"초당 이만큼만 내보낸다"를 흉내 내는 장치. 링크 대역을 모델링한다.</span></div>
    <div><b>멱등</b><span>같은 일을 여러 번 해도 결과가 한 번 한 것과 같은 성질.</span></div>
    <div><b>계약</b><span><code>edge_contract.json</code>. 대역·지연·큐 수치의 유일한 출처. rev.9 에서 뽑아 고정했다.</span></div>
    <div><b>수집기</b><span>판정·텔레메트리를 받아 장부(DynamoDB)에 적는 프로그램. <code>ingest/collector.py</code>.</span></div>
    <div><b>조정기</b><span>사진 묶음의 도착을 받아 판정과 짝짓고 지문을 대조하는 프로그램. <code>ingest/reconciler.py</code>.</span></div>
    <div><b>영속 세션</b><span>구독자가 꺼져 있어도 브로커가 그 이름 앞으로 온 메시지를 보관하는 연결 방식.</span></div>
    <div><b>PUBACK</b><span>"이 메시지 처리했다"는 확인. 보내기 전에 죽으면 브로커가 다시 보낸다.</span></div>
    <div><b>조건부 쓰기</b><span>"이미 있으면 쓰지 않는다", "상태가 PENDING 일 때만 옮긴다"처럼 조건을 건 쓰기. 중복과 경쟁을 막는다.</span></div>
    <div><b>GSI</b><span>보조 색인. 주 키가 아닌 값(차 × 상태)으로 빠르게 찾는 길.</span></div>
    <div><b>버킷 알림</b><span>MinIO/S3 에 파일이 올라오면 그 사실을 다른 곳(여기선 MQTT)으로 알리는 기능.</span></div>
    <div><b>스윕</b><span>주기적으로 훑어 남은 일을 치우는 작업. 알림이 빠진 틈을 메운다.</span></div>
    <div><b>QUARANTINED</b><span>지문이 맞지 않아 격리한 사진. 학습에 넣지 않는다.</span></div>
    <div><b>차량 시계 · 벽시계</b><span>차가 적은 시각(모의) · 클라우드가 받은 실제 시각. 지연은 차량 시계로만 잰다.</span></div>
  </div>
</section>

<footer>
  <p class="thesis">한 문장으로 — 칩이 동적으로 더 잘 고른 데이터의 우위가, 실제 전송 경로를 지나 재학습까지 가도 유지되는지를 비용 0원으로 증명한다.</p>
  <p class="lede">다시 굽기: <code>python run.py guide</code></p>
</footer>
</div>
"""


def main(argv: list[str]) -> int:
    out = argv[0] if argv else OUT_DEFAULT
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    io.open(out, "w", encoding="utf-8", newline="\n").write(build())
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
