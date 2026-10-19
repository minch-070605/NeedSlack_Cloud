# -*- coding: utf-8 -*-
"""분석보고서_Edge투Cloud아키텍처.md -> 디자인된 단일 HTML 아티팩트.

출력은 docs/_build/ 에 쓴다.
"""
import io, re, html, json, os

import os as _os
import sys as _sys
_D = _os.path.dirname(_os.path.abspath(__file__))
_PUB = _os.path.join(_D, "_build")
_os.makedirs(_PUB, exist_ok=True)

# 문서별 설정. 배포 파일 경로가 아티팩트 URL 을 결정하므로 바꾸지 말 것.
DOCS = {
    "arch": {
        "src":   "분석보고서_Edge투Cloud아키텍처.md",
        "dst":   "needslack_cloud.html",
        "title": "NeedSlack Edge-to-Cloud 아키텍처",
        "hero":  "arch",
    },
    "brief": {
        "src":   "온보딩_백엔드_도메인지식.md",
        "dst":   "needslack_brief.html",
        "title": "NeedSlack 백엔드 도메인 브리핑",
        "hero":  "brief",
    },
}
_KEY = _sys.argv[1] if len(_sys.argv) > 1 else "arch"
if _KEY not in DOCS:
    _sys.exit(f"알 수 없는 문서 키: {_KEY}. 가능한 값: {', '.join(DOCS)}")
CFG = DOCS[_KEY]
SRC = _os.path.join(_D, CFG["src"])
DST = _os.path.join(_PUB, CFG["dst"])

MERMAID_INIT = (
    "%%{init: {'theme':'base','themeVariables':{"
    "'fontFamily':'IBM Plex Sans KR, Malgun Gothic, sans-serif','fontSize':'13px',"
    "'primaryColor':'#F2EEE5','primaryTextColor':'#1B1916','primaryBorderColor':'#B9B0A0',"
    "'lineColor':'#6F6758','textColor':'#1B1916','secondaryColor':'#E8EEF5',"
    "'tertiaryColor':'#F7F5F0','clusterBkg':'#FAF8F4','clusterBorder':'#C9C1B1',"
    "'noteBkgColor':'#F6EDD8','noteTextColor':'#1B1916','noteBorderColor':'#C9A55E',"
    "'actorBkg':'#F2EEE5','actorBorder':'#B9B0A0','actorTextColor':'#1B1916',"
    "'signalColor':'#6F6758','signalTextColor':'#1B1916',"
    "'labelBoxBkgColor':'#F6EDD8','labelBoxBorderColor':'#C9A55E','labelTextColor':'#1B1916'"
    "}}}%%"
)

# ───────────────────────────────────────────────────────── inline
def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    return s

def cell_class(txt):
    t = txt.strip()
    if t.startswith("✅"): return " ok"
    if t.startswith("❌"): return " no"
    if t.startswith("🔶"): return " mid"
    return ""

# ───────────────────────────────────────────────────────── blocks
HEAD_RE  = re.compile(r"^(#{1,4})\s+(.*)$")
FENCE_RE = re.compile(r"^```(\w*)\s*$")
ROW_RE   = re.compile(r"^\s*\|.*\|\s*$")
SEP_RE   = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
ITEM_RE  = re.compile(r"^(\s*)(?:([-*])|(\d+)\.)\s+(.*)$")

toc = []
fig = [0]

def render(lines, top=False):
    out, i, n = [], 0, len(lines)
    while i < n:
        ln = lines[i]

        if not ln.strip():
            i += 1; continue

        # ── hr
        if ln.strip() == "---":
            out.append('<hr>'); i += 1; continue

        # ── fenced code / mermaid
        m = FENCE_RE.match(ln)
        if m:
            lang, body, i = m.group(1), [], i + 1
            while i < n and not FENCE_RE.match(lines[i]):
                body.append(lines[i]); i += 1
            i += 1
            src = "\n".join(body)
            if lang == "mermaid":
                fig[0] += 1
                out.append(
                    f'<figure class="plate"><div class="plate-scroll">'
                    f'<pre class="mermaid">{MERMAID_INIT}\n{html.escape(src, quote=False)}</pre>'
                    f'</div><figcaption>그림 {fig[0]}</figcaption></figure>')
            else:
                lab = {"sql": "SQL", "jsonc": "JSON", "": "", "text": ""}.get(lang, lang.upper())
                tag = f'<span class="lang">{lab}</span>' if lab else ""
                out.append(f'<div class="code">{tag}<pre>{html.escape(src, quote=False)}</pre></div>')
            continue

        # ── heading
        m = HEAD_RE.match(ln)
        if m:
            lvl, txt = len(m.group(1)), m.group(2).strip()
            if lvl == 1:
                i += 1; continue                      # 제목은 마스트헤드가 담당
            num = ""
            mm = re.match(r"^([\d]+(?:-[\d]+)?)\.\s+(.*)$", txt)
            if mm: num, txt = mm.group(1), mm.group(2)
            sid = f"s{len(toc)+1}"
            if lvl == 2:
                toc.append(("h2", sid, num, txt))
                eyebrow = f'<span class="eyebrow">{html.escape(num)}</span>' if num else ""
                out.append(f'<h2 id="{sid}">{eyebrow}<span>{inline(txt)}</span></h2>')
            elif lvl == 3:
                toc.append(("h3", sid, num, txt))
                nm = f'<span class="secno">{html.escape(num)}</span>' if num else ""
                out.append(f'<h3 id="{sid}">{nm}{inline(txt)}</h3>')
            else:
                out.append(f'<h4>{inline(txt)}</h4>')
            i += 1; continue

        # ── blockquote
        if ln.startswith(">"):
            buf = []
            while i < n and (lines[i].startswith(">") or
                             (lines[i].strip() and buf and not lines[i].startswith(("#", "|", "```", "---")))):
                if lines[i].startswith(">"):
                    buf.append(re.sub(r"^>\s?", "", lines[i]))
                else:
                    buf.append(lines[i])
                i += 1
            out.append('<blockquote>' + "".join(render(buf)) + '</blockquote>')
            continue

        # ── table
        if ROW_RE.match(ln) and i + 1 < n and SEP_RE.match(lines[i + 1]):
            def cells(r): return [c.strip() for c in r.strip().strip("|").split("|")]
            head = cells(ln); i += 2
            body = []
            while i < n and ROW_RE.match(lines[i]):
                body.append(cells(lines[i])); i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            tr = ""
            for row in body:
                tds = "".join(
                    f'<td class="c{k}{cell_class(c)}">{inline(c)}</td>'
                    for k, c in enumerate(row))
                tr += f"<tr>{tds}</tr>"
            out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead>'
                       f'<tbody>{tr}</tbody></table></div>')
            continue

        # ── list
        m = ITEM_RE.match(ln)
        if m:
            ordered = m.group(3) is not None
            items = []
            while i < n:
                mm = ITEM_RE.match(lines[i])
                if not mm: break
                if (mm.group(3) is not None) != ordered: break
                parts = [mm.group(4)]
                i += 1
                while i < n and lines[i].strip() and not ITEM_RE.match(lines[i]) \
                        and not lines[i].startswith(("#", ">", "|", "```", "---")):
                    parts.append(lines[i].strip()); i += 1
                items.append(" ".join(parts))
                if i < n and not lines[i].strip():
                    j = i
                    while j < n and not lines[j].strip(): j += 1
                    if j < n and ITEM_RE.match(lines[j]) and \
                       (ITEM_RE.match(lines[j]).group(3) is not None) == ordered:
                        i = j
                    else:
                        break
            tag = "ol" if ordered else "ul"
            lis = "".join(f"<li>{inline(x)}</li>" for x in items)
            out.append(f"<{tag}>{lis}</{tag}>")
            continue

        # ── paragraph
        buf = []
        while i < n and lines[i].strip() and not lines[i].startswith(("#", ">", "|", "```", "---")) \
                and not ITEM_RE.match(lines[i]):
            buf.append(lines[i].strip()); i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")
    return out

md = io.open(SRC, encoding="utf-8").read().split("\n")
body = "".join(render(md, top=True))

# ───────────────────────────────────────────────────────── TOC
nav = []
for kind, sid, num, txt in toc:
    label = re.sub(r"\s*—.*$", "", txt).strip()
    label = re.sub(r"[`*★⚠]", "", label).strip()
    nav.append(f'<a class="{kind}" href="#{sid}">'
               f'<span class="n">{html.escape(num)}</span>'
               f'<span class="t">{html.escape(label)}</span></a>')
nav = "".join(nav)

# ───────────────────────────────────────────────────────── 히어로 차트
import math
def lx(v, padl=46.0, w=428.0, lo=-2.0, hi=5.0):
    return padl + (math.log10(v) - lo) / (hi - lo) * w

ticks = [(1e-2, "10 ms"), (1e-1, "100 ms"), (1e0, "1 s"), (1e1, "10 s"),
         (1e2, "100 s"), (1e3, "1,000 s"), (1e4, "10,000 s"), (1e5, "100,000 s")]
tk = "".join(
    f'<line x1="{lx(v):.1f}" y1="74" x2="{lx(v):.1f}" y2="79" class="ax"/>'
    f'<text x="{lx(v):.1f}" y="92" class="tick" text-anchor="middle">{lab}</text>'
    for v, lab in ticks)
xa, xb = lx(0.05), lx(49030.0)

chart_a = f'''<svg viewBox="0 0 520 152" role="img" aria-label="판정 지연 50 ms 와 도착 지연 49,030 초의 로그 눈금 대조">
  <text x="{xa:.1f}" y="16" class="lab">판정 지연 ≤ 50 ms</text>
  <text x="{xa:.1f}" y="30" class="sub">RTL 계약 · stage2_scorer.v:168</text>
  <text x="{xb:.1f}" y="16" class="lab" text-anchor="end">도착 지연 ≤ 13.62 h</text>
  <text x="{xb:.1f}" y="30" class="sub" text-anchor="end">일주기 폐쇄 · 즉시 드롭</text>
  <line x1="46" y1="74" x2="474" y2="74" class="ax"/>{tk}
  <line x1="{xa:.1f}" y1="38" x2="{xa:.1f}" y2="74" class="stem"/>
  <line x1="{xb:.1f}" y1="38" x2="{xb:.1f}" y2="74" class="stem"/>
  <circle cx="{xa:.1f}" cy="74" r="4.5" class="dot"/>
  <circle cx="{xb:.1f}" cy="74" r="4.5" class="dot"/>
  <path d="M {xa:.1f} 112 L {xa:.1f} 118 L {xb:.1f} 118 L {xb:.1f} 112" class="brk"/>
  <text x="{(xa+xb)/2:.1f}" y="138" class="ratio" text-anchor="middle">두 상한의 비 ≈ 9.8 × 10⁵</text>
</svg>'''

BX, BW, BMAX, AXY = 8.0, 414.0, 24.0, 128.0
def bw(v): return v / BMAX * BW
bars = [("무선별 전량", "20 Hz × 144.5 KB · 선별 없음", 23.120, "fill"),
        ("NeedSlack 선별 10%", "rate 0.10 · 엣지 판정 후", 2.312, "fill"),
        ("링크 지속 용량", "UPLINK_BPS · 기준선", 1.000, "ref")]
rows = ""
for k, (name, note, v, kind) in enumerate(bars):
    y = 12 + k * 44
    w = max(bw(v), 2.0)
    cls = "bar" if kind == "fill" else "bar ref"
    rows += (f'<text x="{BX}" y="{y}" class="blab">{name}</text>'
             f'<text x="512" y="{y}" class="bsub" text-anchor="end">{note}</text>'
             f'<rect x="{BX}" y="{y+6}" width="{w:.1f}" height="13" rx="3" class="{cls}"/>'
             f'<text x="{BX+w+8:.1f}" y="{y+17}" class="bval">{v:.3f} Mbps</text>')
gx = "".join(f'<line x1="{BX+bw(t):.1f}" y1="6" x2="{BX+bw(t):.1f}" y2="{AXY}" class="grid"/>'
             f'<text x="{BX+bw(t):.1f}" y="146" class="tick" text-anchor="middle">{t:g}</text>'
             for t in (0, 6, 12, 18, 24))
chart_b = f'''<svg viewBox="0 0 520 168" role="img" aria-label="차량 한 대의 업링크 수요 막대 비교. 무선별 전량 23.120 Mbps, NeedSlack 선별 2.312 Mbps, 링크 지속 용량 1.000 Mbps">
  <defs><pattern id="hatch" width="5" height="5" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
    <line x1="0" y1="0" x2="0" y2="5" class="hl"/></pattern></defs>
  {gx}<line x1="{BX}" y1="{AXY}" x2="{BX+BW}" y2="{AXY}" class="ax"/>{rows}
  <text x="{BX}" y="164" class="tick">Mbps · 차량 1대 주행 중 순시 수요</text>
</svg>'''

CONSTS = [("f_cand", "20.0", "Hz", "후보 스트림"),
          ("rate", "0.10", "—", "순시 선별률"),
          ("S", "144.5", "KB", "프레임 페이로드"),
          ("R_up", "1.000", "Mbps", "링크 명목 대역"),
          ("δ_max", "0.4325", "—", "주행 듀티 상한"),
          ("Q_min", "6.13", "GB", "⑦ NVMe 큐")]
strip = "".join(
    f'<div class="k"><span class="kn">{html.escape(n)}</span>'
    f'<span class="kv">{v}<em>{u}</em></span>'
    f'<span class="kd">{d}</span></div>' for n, v, u, d in CONSTS)

BRIEF_CONSTS = [("code", "0", "줄", "서버 코드"),
                ("stores", "4", "계층", "저장소 분리"),
                ("pitfalls", "6", "건", "확장 시 함정"),
                ("open", "5", "건", "남은 결정"),
                ("p99", "300", "ms", "조회 API 목표"),
                ("L_max", "13.62", "h", "도착 지연 상한")]
brief_strip = "".join(
    f'<div class="k"><span class="kn">{html.escape(n)}</span>'
    f'<span class="kv">{v}<em>{u}</em></span>'
    f'<span class="kd">{d}</span></div>' for n, v, u, d in BRIEF_CONSTS)

if CFG["hero"] == "arch":
    MAST = f'''<p class="tagline">NeedSlack <i></i> 설계 분석 <i></i> 2026-09-20</p>
    <h1>버려지던 슬랙으로 고른 데이터를, 클라우드까지 옮기는 법</h1>
    <p class="dek">엣지 하드웨어 가속기가 프레임 안에서 내린 선별 판정을 AWS의 수집·저장·재학습·OTA
      루프로 잇는 통합 아키텍처. 새 가정을 만들지 않고, 프로젝트가 이미 닫아 놓은 업링크 예산을
      서비스 경계로 번역한다.</p>
    <p class="meta"><span>관점 <b>Cloud Solutions Architect / Backend Engineer</b></span>
      <span>근거 <b>갈래A_전환.md §6 · config.py · stage2_scorer.v</b></span>
      <span>대조군 <b>모드 A / 모드 B 분리</b></span></p>

    <div class="strip">{strip}</div>

    <div class="figs">
      <div class="fig">
        <h6>두 지연 상한은 같은 관을 쓸 수 없다</h6>
        <p>판정은 하드 실시간, 페이로드는 베스트에포트 — 로그 눈금에서 여섯 자리수 떨어져 있다.
           이것이 평면 분리의 근거다.</p>
        {chart_a}
      </div>
      <div class="fig">
        <h6>차량당 업링크 수요</h6>
        <p>엣지 선별이 1 Mbps 링크를 실현 가능한 운용점으로 끌어내린다. 빗금은 측정이 아니라
           링크 용량 기준선이다.</p>
        {chart_b}
      </div>
    </div>'''
else:
    MAST = f'''<p class="tagline">needslack-cloud <i></i> 온보딩 브리핑 <i></i> 2026-09-21</p>
    <h1>지시를 내리기 전에 알아야 할 것</h1>
    <p class="dek">목적은 기술 교육이 아니라 <strong>통제권</strong>이다. 어떤 결정이 이미
      내려졌고 그 근거가 무엇인지, 어떤 결정이 아직 리더의 몫인지를 가른다.
      서버 코드는 아직 0줄이다 — 가장 비싼 실수들이 아직 일어나지 않았다는 뜻이다.</p>
    <p class="meta"><span>독자 <b>프로젝트 리더</b></span>
      <span>범위 <b>needslack-cloud/ 실측 현황</b></span>
      <span>표기 <b>🔒 확정 · 🟡 미정 · ⚠️ 함정</b></span></p>

    <div class="strip">{brief_strip}</div>'''

TITLE = CFG["title"]

HTML = f'''<title>{TITLE}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Serif+KR:wght@400;600&display=swap">
<style>
:root{{
  --ground:#FAF8F4; --surface:#FFFFFF; --surface2:#F3EFE7; --plate:#FFFFFF;
  --ink:#1B1916; --ink2:#4E483F; --muted:#877E70;
  --rule:#E2DCD0; --rule2:#C9C1B1;
  --accent:#A2650A; --accent2:#7C4D05; --accentSoft:#F8EED6;
  --blue:#2B6CB0; --pass:#2D6E46; --fail:#A33F33; --mid:#8A6A1F;
  --shadow:0 1px 2px rgba(40,32,18,.05),0 8px 24px -14px rgba(40,32,18,.22);
  --sans:'IBM Plex Sans KR','Malgun Gothic','Apple SD Gothic Neo',system-ui,sans-serif;
  --mono:'IBM Plex Mono','D2Coding',ui-monospace,'Cascadia Mono',Consolas,monospace;
  --serif:'Noto Serif KR','Nanum Myeongjo',Georgia,serif;
}}
@media (prefers-color-scheme:dark){{
  :root:not([data-theme="light"]){{
    --ground:#131210; --surface:#1A1815; --surface2:#221F1A; --plate:#EFECE4;
    --ink:#EDE7DB; --ink2:#BCB4A4; --muted:#8E8674;
    --rule:#2E2A23; --rule2:#453F34;
    --accent:#DCA33F; --accent2:#EEBF6C; --accentSoft:#2B2317;
    --blue:#7DB0E0; --pass:#68B085; --fail:#D6897C; --mid:#C9A55E;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px -16px rgba(0,0,0,.7);
  }}
}}
:root[data-theme="dark"]{{
  --ground:#131210; --surface:#1A1815; --surface2:#221F1A; --plate:#EFECE4;
  --ink:#EDE7DB; --ink2:#BCB4A4; --muted:#8E8674;
  --rule:#2E2A23; --rule2:#453F34;
  --accent:#DCA33F; --accent2:#EEBF6C; --accentSoft:#2B2317;
  --blue:#7DB0E0; --pass:#68B085; --fail:#D6897C; --mid:#C9A55E;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px -16px rgba(0,0,0,.7);
}}

*{{box-sizing:border-box}}
body{{background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.72;-webkit-font-smoothing:antialiased}}
::selection{{background:var(--accentSoft);color:var(--ink)}}
a{{color:var(--blue)}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}}

.wrap{{max-width:1180px;margin:0 auto;padding-inline:20px;padding-block:0 72px}}

/* ── 마스트헤드 ── */
.mast{{padding-block:56px 32px;border-bottom:1px solid var(--rule2)}}
.tagline{{font-family:var(--mono);font-size:11px;letter-spacing:.18em;text-transform:uppercase;
  color:var(--accent);margin:0 0 18px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}}
.tagline i{{width:22px;height:1px;background:var(--rule2);display:inline-block;font-style:normal}}
.mast h1{{font-size:clamp(28px,4.4vw,46px);line-height:1.14;font-weight:700;margin:0;
  letter-spacing:-.02em;text-wrap:balance;max-width:19ch}}
.mast .dek{{margin:16px 0 0;max-width:62ch;color:var(--ink2);font-size:16px}}
.meta{{margin-top:22px;display:flex;flex-wrap:wrap;gap:8px 22px;
  font-family:var(--mono);font-size:11.5px;color:var(--muted)}}
.meta b{{color:var(--ink2);font-weight:500}}

/* ── 상수 스트립 (데이터시트 헤더) ── */
.strip{{display:grid;grid-template-columns:repeat(6,1fr);gap:0;margin-top:30px;
  border:1px solid var(--rule2);border-radius:3px;background:var(--surface);overflow:hidden}}
.k{{padding:12px 14px;border-left:1px solid var(--rule);display:flex;flex-direction:column;gap:2px}}
.k:first-child{{border-left:0}}
.kn{{font-family:var(--mono);font-size:11px;color:var(--accent);letter-spacing:.03em}}
.kv{{font-family:var(--mono);font-size:19px;font-weight:600;color:var(--ink);
  font-variant-numeric:tabular-nums;line-height:1.2}}
.kv em{{font-style:normal;font-size:11px;color:var(--muted);margin-left:4px;font-weight:400}}
.kd{{font-size:11.5px;color:var(--muted)}}

/* ── 히어로 차트 ── */
.figs{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px}}
.fig{{background:var(--surface);border:1px solid var(--rule);border-radius:3px;padding:16px 18px 12px}}
.fig h6{{margin:0 0 2px;font-size:13px;font-weight:600;letter-spacing:-.005em}}
.fig p{{margin:0 0 6px;font-size:12px;color:var(--muted);line-height:1.5}}
.fig svg{{width:100%;height:auto;display:block;margin-top:4px}}
svg .ax{{stroke:var(--rule2);stroke-width:1}}
svg .grid{{stroke:var(--rule);stroke-width:1}}
svg .stem{{stroke:var(--accent);stroke-width:1.5;stroke-dasharray:2 3}}
svg .dot{{fill:var(--accent)}}
svg .brk{{fill:none;stroke:var(--rule2);stroke-width:1}}
svg .lab{{font-family:var(--sans);font-size:12px;font-weight:600;fill:var(--ink)}}
svg .sub{{font-family:var(--mono);font-size:10px;fill:var(--muted)}}
svg .tick{{font-family:var(--mono);font-size:9.5px;fill:var(--muted);font-variant-numeric:tabular-nums}}
svg .ratio{{font-family:var(--mono);font-size:12px;font-weight:600;fill:var(--accent);
  font-variant-numeric:tabular-nums}}
svg .bar{{fill:var(--accent)}}
svg .bar.ref{{fill:url(#hatch);stroke:var(--rule2);stroke-width:1}}
svg .hl{{stroke:var(--rule2);stroke-width:1.6}}
svg .blab{{font-family:var(--sans);font-size:11.5px;font-weight:600;fill:var(--ink)}}
svg .bsub{{font-family:var(--mono);font-size:9.5px;fill:var(--muted)}}
svg .bval{{font-family:var(--mono);font-size:11.5px;font-weight:600;fill:var(--ink);
  font-variant-numeric:tabular-nums}}

/* ── 레이아웃 ── */
.cols{{display:grid;grid-template-columns:212px minmax(0,1fr);gap:48px;margin-top:44px;align-items:start}}
nav.rail{{position:sticky;top:20px;max-height:calc(100vh - 40px);overflow-y:auto;
  border-left:1px solid var(--rule);padding-left:0}}
nav.rail a{{display:flex;gap:8px;padding:4px 10px;text-decoration:none;color:var(--ink2);
  font-size:12.5px;line-height:1.45;border-left:2px solid transparent;margin-left:-1px}}
nav.rail a .n{{font-family:var(--mono);font-size:10.5px;color:var(--muted);flex:none;
  min-width:24px;padding-top:2px;font-variant-numeric:tabular-nums}}
nav.rail a.h2{{font-weight:600;color:var(--ink);margin-top:12px}}
nav.rail a.h2:first-child{{margin-top:0}}
nav.rail a.h3 .t{{color:var(--ink2)}}
nav.rail a:hover{{color:var(--accent);background:var(--accentSoft)}}
nav.rail a.on{{border-left-color:var(--accent);color:var(--accent2);background:var(--accentSoft)}}
nav.rail a.on .n{{color:var(--accent)}}
.railhead{{font-family:var(--mono);font-size:10px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);padding:0 10px 8px}}
.toggle{{display:none}}

article{{min-width:0}}
article > *{{max-width:70ch}}
article .tw,article .plate,article .code{{max-width:100%}}

h2{{font-size:clamp(22px,2.4vw,29px);font-weight:700;letter-spacing:-.018em;line-height:1.24;
  margin:72px 0 18px;padding-top:26px;border-top:2px solid var(--ink);
  display:flex;flex-direction:column;gap:8px;text-wrap:balance}}
h2 .eyebrow{{font-family:var(--mono);font-size:11px;letter-spacing:.2em;color:var(--accent);
  font-weight:600}}
h3{{font-size:17.5px;font-weight:600;margin:44px 0 12px;letter-spacing:-.008em;
  line-height:1.38;text-wrap:balance}}
h3 .secno{{font-family:var(--mono);font-size:12.5px;color:var(--accent);margin-right:9px;
  font-weight:500}}
h4{{font-size:14.5px;font-weight:600;margin:28px 0 10px;color:var(--ink);
  font-family:var(--mono);letter-spacing:-.01em}}
p{{margin:0 0 15px}}
strong{{font-weight:600;color:var(--ink)}}
hr{{border:0;border-top:1px solid var(--rule);margin:40px 0}}
h2 + hr,hr + h2{{display:none}}

ul,ol{{margin:0 0 16px;padding-left:22px}}
li{{margin-bottom:8px;padding-left:2px}}
li::marker{{color:var(--accent);font-family:var(--mono);font-size:.9em}}

code{{font-family:var(--mono);font-size:.855em;background:var(--surface2);
  padding:1.5px 5px;border-radius:2.5px;color:var(--accent2);
  border:1px solid var(--rule);white-space:nowrap}}

/* 코드 블록 */
.code{{position:relative;margin:0 0 22px;background:var(--surface2);
  border:1px solid var(--rule);border-left:3px solid var(--rule2);border-radius:3px;
  overflow:hidden}}
.code .lang{{position:absolute;top:0;right:0;font-family:var(--mono);font-size:9.5px;
  letter-spacing:.14em;color:var(--muted);background:var(--surface);
  border-left:1px solid var(--rule);border-bottom:1px solid var(--rule);
  padding:3px 8px;border-radius:0 0 0 3px}}
.code pre{{margin:0;padding:16px 18px;overflow-x:auto;font-family:var(--mono);
  font-size:12.5px;line-height:1.62;color:var(--ink2);
  font-variant-numeric:tabular-nums;tab-size:2}}

/* 인용/판정 블록 */
blockquote{{margin:0 0 22px;padding:16px 20px;background:var(--accentSoft);
  border:1px solid var(--rule);border-left:3px solid var(--accent);border-radius:3px;
  font-family:var(--serif);font-size:14.5px;line-height:1.74;color:var(--ink)}}
blockquote > *:last-child{{margin-bottom:0}}
blockquote code{{background:var(--surface);font-family:var(--mono)}}
blockquote .code{{font-family:var(--mono);background:var(--surface)}}
blockquote table{{font-family:var(--sans)}}

/* 표 — 데이터시트 */
.tw{{overflow-x:auto;margin:0 0 24px;border:1px solid var(--rule);border-radius:3px;
  background:var(--surface)}}
table{{border-collapse:collapse;width:100%;font-size:13px;line-height:1.55}}
thead th{{font-family:var(--mono);font-size:10px;letter-spacing:.11em;text-transform:uppercase;
  color:var(--muted);font-weight:500;text-align:left;padding:10px 14px;
  border-bottom:1px solid var(--rule2);white-space:nowrap;background:var(--surface2)}}
tbody td{{padding:11px 14px;border-bottom:1px solid var(--rule);vertical-align:top;
  color:var(--ink2);font-variant-numeric:tabular-nums}}
tbody tr:last-child td{{border-bottom:0}}
tbody td.c0{{color:var(--ink);font-weight:500;min-width:96px}}
tbody td strong{{color:var(--ink)}}
tbody td.ok{{color:var(--pass)}} tbody td.no{{color:var(--fail)}} tbody td.mid{{color:var(--mid)}}
tbody tr:hover td{{background:var(--surface2)}}
table code{{white-space:nowrap;font-size:.82em}}

/* 다이어그램 플레이트 */
.plate{{margin:0 0 26px;border:1px solid var(--rule2);border-radius:3px;
  background:var(--plate);box-shadow:var(--shadow);overflow:hidden}}
.plate-scroll{{overflow-x:auto;padding:22px 20px 14px;display:flex;justify-content:center}}
.plate pre.mermaid{{margin:0;background:none;border:0;min-width:min(100%,520px)}}
.plate svg{{max-width:100%;height:auto}}
.plate figcaption{{font-family:var(--mono);font-size:10px;letter-spacing:.16em;
  text-transform:uppercase;color:#5F594C;padding:0 20px 12px;text-align:center}}

/* 푸터 */
.foot{{margin-top:72px;padding-top:24px;border-top:1px solid var(--rule2);
  font-family:var(--mono);font-size:11.5px;color:var(--muted);
  display:flex;flex-wrap:wrap;gap:6px 20px}}

@media (max-width:1000px){{
  .cols{{grid-template-columns:1fr;gap:0}}
  nav.rail{{position:static;max-height:none;border-left:0;border:1px solid var(--rule);
    border-radius:3px;padding:12px;margin-bottom:32px;background:var(--surface);
    display:none;columns:2;column-gap:18px}}
  nav.rail.open{{display:block}}
  nav.rail a.h2{{break-inside:avoid}}
  .toggle{{display:block;width:100%;margin-top:28px;font-family:var(--mono);font-size:11px;
    letter-spacing:.16em;text-transform:uppercase;padding:11px 14px;cursor:pointer;
    background:var(--surface);color:var(--ink2);border:1px solid var(--rule2);border-radius:3px;
    text-align:left}}
  .figs{{grid-template-columns:1fr}}
  .strip{{grid-template-columns:repeat(3,1fr)}}
  .k:nth-child(4){{border-left:0}}
  .k:nth-child(-n+3){{border-bottom:1px solid var(--rule)}}
  article > *{{max-width:none}}
}}
@media (max-width:560px){{
  body{{font-size:14.5px}}
  .wrap{{padding-inline:16px}}
  .strip{{grid-template-columns:repeat(2,1fr)}}
  .k{{border-left:0;border-bottom:1px solid var(--rule)}}
  .k:nth-child(2n){{border-left:1px solid var(--rule)}}
  h2{{margin-top:52px}}
  nav.rail{{columns:1}}
}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style>

<div class="wrap">
  <header class="mast">
    {MAST}
  </header>

  <button class="toggle" id="tg" aria-expanded="false" aria-controls="rail">목차 펼치기</button>

  <div class="cols">
    <nav class="rail" id="rail"><div class="railhead">목차</div>{nav}</nav>
    <article>{body}</article>
  </div>

  <p class="foot"><span>needslack-cloud</span><span>docs/{CFG["src"]}</span>
    <span>수치 출처 schema/edge_contract.json</span></p>
</div>

<script>
(function(){{
  var tg=document.getElementById('tg'),rail=document.getElementById('rail');
  if(tg&&rail){{tg.addEventListener('click',function(){{
    var o=rail.classList.toggle('open');
    tg.setAttribute('aria-expanded',o?'true':'false');
    tg.textContent=o?'목차 접기':'목차 펼치기';
  }});}}
  var links=[].slice.call(document.querySelectorAll('nav.rail a'));
  var map={{}};links.forEach(function(a){{
    var el=document.getElementById(a.getAttribute('href').slice(1));
    if(el)map[el.id]=a;
  }});
  var heads=Object.keys(map).map(function(id){{return document.getElementById(id);}});
  if(!('IntersectionObserver' in window)||!heads.length)return;
  var vis={{}};
  var io=new IntersectionObserver(function(es){{
    es.forEach(function(e){{vis[e.target.id]=e.isIntersecting;}});
    var cur=null;
    for(var i=0;i<heads.length;i++){{if(vis[heads[i].id]){{cur=heads[i].id;break;}}}}
    if(!cur){{
      for(var j=heads.length-1;j>=0;j--){{
        if(heads[j].getBoundingClientRect().top<120){{cur=heads[j].id;break;}}
      }}
    }}
    links.forEach(function(a){{a.classList.remove('on');}});
    if(cur&&map[cur])map[cur].classList.add('on');
  }},{{rootMargin:'-80px 0px -70% 0px',threshold:0}});
  heads.forEach(function(h){{io.observe(h);}});
}})();
</script>
'''

io.open(DST, "w", encoding="utf-8", newline="\n").write(HTML)
print("wrote", DST, len(HTML), "chars")
print("h2/h3:", sum(1 for t in toc if t[0]=="h2"), "/", sum(1 for t in toc if t[0]=="h3"))
print("figures:", fig[0])
print("tables:", body.count("<table>"), " codeblocks:", body.count('class="code"'))
