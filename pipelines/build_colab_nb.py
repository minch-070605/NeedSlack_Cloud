# -*- coding: utf-8 -*-
"""build_colab_nb.py — M3 Colab 노트북 `pipelines/colab_train.ipynb` 을 굽는다. 노트북을 손으로 고치지 않는다.

    python run.py notebook

무엇을 노트북에 굽는가 (설계결정-0002)
-------------------------------------------------------------------------------
    tar 3개의 sha256        data/snapshots/*.sha256 — MinIO snapshot.json 과 같은지 여기서 먼저 확인한다
    rev.9 학습·평가 코드     04_중앙AI/yolo_detector.py · apeval.py 원문 그대로,
                            y5_train_eval_axis2.py 의 collect_matches · ap_from_matches 원문 그대로
                            (각 원본의 sha256 을 함께 적는다 — 레시피가 바이트 단위로 같다는 증거)
    rev.9 config 값         CLASSES · YOLO_IMGSZ (값만. config.py 는 윈도우 경로를 담고 있어 옮기지 않는다)
    오프라인 결과           runs_yolo/axis2/eval_axis2_mc.json 의 STA_B · DYN_B 와 DYN_B_vs_STA_B

학습 인자는 rev.9 axis2 `_mc` 런의 args.yaml 과 같다 — epochs 16 · batch 32 · imgsz 640 · workers 2 ·
ybase last.pt 에서 시작. 나머지(SGD · lr0 · 증강 끔 · amp 끔 …)는 yolo_detector.LOCKED_TRAIN_ARGS 가 강제한다.
ultralytics 는 rev.9 와 같은 8.4.130 으로 고정한다(rev.9/08_이관/build_colab_nb.py).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (os.path.join(ROOT, "edge-agent"), os.path.join(ROOT, "schema"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT = os.path.join(_HERE, "colab_train.ipynb")
SNAP = os.path.join(ROOT, "data", "snapshots")
ARMS = ("STA_B", "DYN_B")
SEEDS = (1, 2, 3)
#: rev.9 runs_yolo/axis2/*_mc_s*/args.yaml 에서 읽은 값 (build 때 다시 대조한다)
TRAIN = {"epochs": 16, "batch": 32, "imgsz": 640, "workers": 2}
ULTRALYTICS = "8.4.130"
BOOT, BOOT_SEED = 400, 20260901      # rev.9 y5 의 --boot 기본값 · rng 시드


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rev9_root() -> str:
    import replay
    return replay.rev9_root()


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def fn_source(path: str, names: tuple[str, ...]) -> str:
    """파일에서 함수 정의만 원문 그대로 꺼낸다."""
    src = read(path)
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out.append(ast.get_source_segment(src, node))
    if len(out) != len(names):
        raise SystemExit(f"{path} 에서 {names} 를 모두 찾지 못했다")
    return "\n\n\n".join(out)


def expected_shas() -> dict[str, str]:
    """data/snapshots/*.sha256 을 읽고, 팔별 값은 MinIO snapshot.json 과 같은지 확인한다."""
    out = {}
    for name in ("eval-common.tar",) + tuple(f"ds-{a}.tar" for a in ARMS):
        p = os.path.join(SNAP, name + ".sha256")
        if not os.path.exists(p):
            raise SystemExit(f"{p} 가 없다 — `python run.py snapshot common|STA_B|DYN_B` 를 먼저 돌린다")
        out[name] = read(p).split()[0]
    try:
        from common import aws
        s3 = aws.s3_client()
        for a in ARMS:
            snap = json.loads(s3.get_object(Bucket=aws.dataset_bucket(),
                                            Key=f"datasets/ds-{a}/snapshot.json")["Body"].read())
            if snap["tar_sha256"] != out[f"ds-{a}.tar"]:
                raise SystemExit(f"ds-{a}.tar sha256 이 MinIO snapshot.json 과 다르다")
            if snap["eval_common_tar_sha256"] != out["eval-common.tar"]:
                raise SystemExit(f"ds-{a} 가 가리키는 eval-common.tar 가 지금 것과 다르다")
        print("  tar sha256 — data/snapshots 와 MinIO snapshot.json 일치")
    except ImportError:
        print("  (boto3 없음 — MinIO 대조를 건너뛴다)")
    return out


def check_train_args(r9: str) -> None:
    """굽는 값이 rev.9 가 실제로 쓴 args.yaml 과 같은지 — 여섯 런 전부."""
    for a in ARMS:
        for s in SEEDS:
            p = os.path.join(r9, "04_중앙AI", "runs_yolo", "axis2", f"{a}_mc_s{s}", "args.yaml")
            kv = dict(line.split(": ", 1) for line in read(p).splitlines() if ": " in line)
            for k, v in TRAIN.items():
                if kv.get(k, "").strip() != str(v):
                    raise SystemExit(f"{p}: {k}={kv.get(k)} — 노트북 값 {v} 와 다르다")
            if kv.get("seed", "").strip() != str(s):
                raise SystemExit(f"{p}: seed={kv.get('seed')}")


# ─────────────────────────────────────────────────────────── 셀
def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(keepends=True)}


def build() -> dict:
    r9 = rev9_root()
    cz = os.path.join(r9, "04_중앙AI")
    check_train_args(r9)
    shas = expected_shas()
    src = {
        "yolo_detector.py": read(os.path.join(cz, "yolo_detector.py")),
        "apeval.py": read(os.path.join(cz, "apeval.py")),
    }
    y5 = os.path.join(cz, "y5_train_eval_axis2.py")
    helpers = fn_source(y5, ("collect_matches", "ap_from_matches"))
    cfg = read(os.path.join(r9, "02_전처리", "config.py"))
    classes = next(l for l in cfg.splitlines() if l.startswith("CLASSES")).split("=", 1)[1].split("#")[0].strip()
    imgsz = next(l for l in cfg.splitlines() if l.startswith("YOLO_IMGSZ")).split("=", 1)[1].split("#")[0].strip()
    offline = json.loads(read(os.path.join(cz, "runs_yolo", "axis2", "eval_axis2_mc.json")))
    off = {"STA_B": offline["conditions"]["STA_B"], "DYN_B": offline["conditions"]["DYN_B"],
           "DYN_B_vs_STA_B": offline["equivalence"]["DYN_B_vs_STA_B"],
           "n_held_out": offline["n_held_out"], "source": "rev.9/04_중앙AI/runs_yolo/axis2/eval_axis2_mc.json"}
    prov = {"rev9_sources_sha256": {k: sha(v.encode("utf-8")) for k, v in src.items()},
            "rev9_y5_sha256": sha(read(y5).encode("utf-8"))}

    def put(t: str, **kw) -> str:
        for k, v in kw.items():
            t = t.replace(f"@@{k}@@", v)
        return t

    cells = [
        md(put("""
# M3 — 두 팔 × 3시드 재학습 · held-out 평가 (Colab T4)

**이 노트북은 `python run.py notebook` 이 구웠다. 손으로 고치지 않는다.** (설계결정-0002)

## 준비 — 사람이 할 일
1. Google Drive 의 한 폴더에 **네 파일**을 올린다 — `ds-STA_B.tar` · `ds-DYN_B.tar` · `eval-common.tar` · 이 노트북
2. 이 노트북을 Colab 으로 열고 **런타임 → 런타임 유형 변경 → T4 GPU**
3. **런타임 → 모두 실행.** Drive 접근을 묻는 창이 뜨면 허용한다
4. 끝나면 Drive 의 `m3_results/m3_eval.json` 을 내려받아 `needslack-cloud/data/m3/m3_eval.json` 에 둔다 → `python run.py compare`

## 무엇을 하나
| 셀 | 내용 | 멈추는 조건 |
|---|---|---|
| 1–2 | Drive 연결 · tar 3개 찾기 · **sha256 대조** | 스냅샷과 한 바이트라도 다르면 |
| 3 | 풀기 · 개수 점검 | 학습 2,449 · val 800 · held-out @@NHELD@@ 가 아니면 |
| 4 | ultralytics @@ULT@@ 고정 설치 | 버전이 다르면 |
| 5 | rev.9 학습·평가 코드를 **원문 그대로** 쓴다 | — |
| 6 | 6런 학습 — 런마다 가중치를 **즉시 Drive 에 저장** | — |
| 7 | held-out 평가 — 런마다 결과를 Drive 에 저장 | — |
| 8 | Δ mAP · 부트스트랩 CI(held-out 프레임 × 시드, @@BOOT@@회) · 오프라인 대조 → `m3_eval.json` | — |

**세션이 끊기면 그냥 다시 "모두 실행".** Drive 에 결과가 있는 런은 학습·평가를 건너뛴다.
""", NHELD=f"{off['n_held_out']:,}", ULT=ULTRALYTICS, BOOT=str(BOOT))),

        md("## 1. Drive 연결 · tar 찾기"),
        code("""
import glob, os
from google.colab import drive
drive.mount('/content/drive')

DRIVE_DIR = ''   # 자동 탐색이 실패하면 tar 3개가 든 폴더 경로를 여기에 적는다
if not DRIVE_DIR:
    hits = glob.glob('/content/drive/MyDrive/**/eval-common.tar', recursive=True)
    if len(hits) != 1:
        raise SystemExit(f'eval-common.tar 를 {len(hits)}곳에서 찾았다 — DRIVE_DIR 을 직접 적는다: {hits}')
    DRIVE_DIR = os.path.dirname(hits[0])
RESULTS = os.path.join(DRIVE_DIR, 'm3_results')
os.makedirs(RESULTS, exist_ok=True)
print('DRIVE_DIR', DRIVE_DIR)
print('RESULTS  ', RESULTS)
"""),
        md("## 2. sha256 대조 — 스냅샷과 같은 tar 인가"),
        code(put("""
import hashlib
EXPECTED = @@SHAS@@

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 24), b''):
            h.update(chunk)
    return h.hexdigest()

for name, want in EXPECTED.items():
    p = os.path.join(DRIVE_DIR, name)
    if not os.path.exists(p):
        raise SystemExit(f'{name} 이 {DRIVE_DIR} 에 없다')
    got = sha256_file(p)
    print(f'{name:18s} {got[:16]}…  ' + ('일치' if got == want else '★ 다르다'))
    if got != want:
        raise SystemExit(f'{name} 의 sha256 이 스냅샷과 다르다 — 올린 파일을 확인한다 (기대 {want})')
""", SHAS=json.dumps(shas, indent=1))),

        md("## 3. 풀기 · 개수 점검"),
        code(put("""
import subprocess, time
STAGE = '/content/stage'
os.makedirs(STAGE, exist_ok=True)
for name in EXPECTED:
    top = name[:-4]
    if os.path.isdir(os.path.join(STAGE, top)):
        continue
    t0 = time.time()
    subprocess.run(['tar', '-xf', os.path.join(DRIVE_DIR, name), '-C', STAGE], check=True)
    print(f'{name} 풀기 {time.time() - t0:.0f} s')

def count(d):
    return len(os.listdir(d))

N = {a: count(f'{STAGE}/ds-{a}/images/train') for a in ('STA_B', 'DYN_B')}
NV, NH = count(f'{STAGE}/eval-common/images/val'), count(f'{STAGE}/eval-common/images/held_out')
print('학습', N, '· val', NV, '· held-out', NH)
assert all(n == 2449 for n in N.values()), N
assert NV == 800 and NH == @@NHELD@@, (NV, NH)
for a in N:
    assert count(f'{STAGE}/ds-{a}/labels/train') == N[a]
""", NHELD=str(off["n_held_out"]))),

        md(put("## 4. 환경 — ultralytics @@ULT@@ 고정 (rev.9 와 같다)", ULT=ULTRALYTICS)),
        code(put("""
!pip -q install ultralytics==@@ULT@@
import torch, ultralytics
assert ultralytics.__version__ == '@@ULT@@', ultralytics.__version__
assert torch.cuda.is_available(), 'GPU 런타임이 아니다 — 런타임 유형을 T4 GPU 로 바꾼다'
ENV = {'ultralytics': ultralytics.__version__, 'torch': torch.__version__,
       'cuda': torch.version.cuda, 'gpu': torch.cuda.get_device_name(0)}
print(ENV)
""", ULT=ULTRALYTICS)),

        md("""
## 5. rev.9 학습·평가 코드 — 원문 그대로

`yolo_detector.py` · `apeval.py` 는 rev.9 파일 **전체**, `m3_helpers.py` 의 두 함수는 `y5_train_eval_axis2.py` 에서
**글자 그대로** 꺼낸 것이다. `config.py` 는 값 두 개만 옮긴다(원본은 윈도우 경로를 담고 있다).
"""),
        code(put("""
CODE = '/content/rev9code'
os.makedirs(CODE, exist_ok=True)
FILES = @@FILES@@
PROVENANCE = @@PROV@@
for name, text in FILES.items():
    with open(os.path.join(CODE, name), 'w', encoding='utf-8') as f:
        f.write(text)
for name, want in PROVENANCE['rev9_sources_sha256'].items():
    got = hashlib.sha256(FILES[name].encode('utf-8')).hexdigest()
    assert got == want, name
import sys
sys.path.insert(0, CODE)
import yolo_detector as YD
import m3_helpers as H
YD.assert_no_autotuning(YD.LOCKED_TRAIN_ARGS, where='notebook')
print('rev.9 코드 준비 —', ', '.join(FILES))
""", FILES=json.dumps({
            **src,
            "config.py": f"# rev.9 02_전처리/config.py 의 값 두 개 (build_colab_nb.py 가 옮김)\n"
                         f"CLASSES = {classes}\nYOLO_IMGSZ = {imgsz}\nDIR_LABELS = ''\n",
            "m3_helpers.py": "# rev.9 04_중앙AI/y5_train_eval_axis2.py 에서 원문 그대로 꺼낸 함수\n"
                             "import numpy as np\nfrom config import CLASSES\n\n\n" + helpers + "\n",
        }, ensure_ascii=False), PROV=json.dumps(prov, indent=1))),

        md(put("""
## 6. 학습 — 6런 (2팔 × 3시드)

`ybase` 에서 fine-tune. epochs @@EP@@ · batch @@BS@@ · imgsz @@IMG@@ · workers @@WK@@ — rev.9 axis2 `_mc` 런의 `args.yaml` 과 같다.
val 은 tune 앞 800(진행 표시용). 가중치는 **last.pt** 만 쓴다(best.pt 금지 — 고정 에폭 규율).
런이 끝날 때마다 `m3_results/runs/{ARM}-s{SEED}/` 에 가중치와 실제 쓰인 인자를 저장한다.
""", EP=str(TRAIN["epochs"]), BS=str(TRAIN["batch"]), IMG=str(TRAIN["imgsz"]), WK=str(TRAIN["workers"]))),
        code(put("""
import json, shutil
BASE_W = f'{STAGE}/eval-common/weights/ybase_last.pt'
TRAIN = @@TRAIN@@
RUNS = [(a, s) for a in ('STA_B', 'DYN_B') for s in (1, 2, 3)]

def write_list(path, paths):
    with open(path, 'w') as f:
        f.write(''.join(p + '\\n' for p in paths))
    return path

def abs_list(top, rel_txt):
    return [os.path.join(top, l.strip()) for l in open(os.path.join(top, rel_txt)) if l.strip()]

VAL = write_list('/content/val.txt', abs_list(f'{STAGE}/eval-common', 'lists/val.txt'))
YAML = {}
for a in ('STA_B', 'DYN_B'):
    tr = write_list(f'/content/train_{a}.txt', abs_list(f'{STAGE}/ds-{a}', 'lists/train.txt'))
    YAML[a] = f'/content/data_{a}.yaml'
    with open(YAML[a], 'w') as f:
        f.write(f'path: {STAGE}\\ntrain: {tr}\\nval: {VAL}\\nnames:\\n' +
                ''.join(f'  {i}: {n}\\n' for i, n in enumerate(YD.CLASSES)))

def args_sha(used):
    # 팔·시드·경로를 뺀 학습 인자의 해시 — 여섯 런이 전부 같아야 한다 (수용 기준 3)
    skip = {'seed', 'data', 'name', 'project', 'save_dir', 'model'}
    keep = {k: str(v) for k, v in used.items() if k not in skip}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16], keep

for a, s in RUNS:
    rid = f'{a}-s{s}'
    dst = os.path.join(RESULTS, 'runs', rid)
    if os.path.exists(os.path.join(dst, 'last.pt')) and os.path.exists(os.path.join(dst, 'train.json')):
        print(f'{rid} 이미 있음 — 건너뜀')
        continue
    t0 = time.time()
    _, last, used = YD.train_locked(BASE_W, YAML[a], '/content/runs', rid, epochs=TRAIN['epochs'],
                                    batch=TRAIN['batch'], imgsz=TRAIN['imgsz'], device=0,
                                    seed=s, workers=TRAIN['workers'])
    h, keep = args_sha(used)
    os.makedirs(dst, exist_ok=True)
    shutil.copy(last, os.path.join(dst, 'last.pt'))
    with open(os.path.join(dst, 'train.json'), 'w') as f:
        json.dump({'run_id': rid, 'arm': a, 'seed': s, 'train_args_sha': h, 'train_args': keep,
                   'weights_sha256': sha256_file(last), 'minutes': (time.time() - t0) / 60, 'env': ENV},
                  f, indent=1, ensure_ascii=False)
    print(f'{rid} 학습 {(time.time() - t0) / 60:.1f} 분 · args {h} → Drive 저장')
""", TRAIN=json.dumps(TRAIN))),

        md("## 7. held-out 평가 — rev.9 와 같은 AP 정의 (VOC all-points, IoU 0.5)"),
        code("""
import numpy as np
HELD_TOP = f'{STAGE}/eval-common'
held_paths = abs_list(HELD_TOP, 'lists/held_out.txt')
held_ids = [int(os.path.basename(p)[:5]) for p in held_paths]
gts = [YD.read_gt(fid, labels_dir=f'{HELD_TOP}/labels/held_out') for fid in held_ids]
def save_matches(path, pf, ngt):
    # 프레임별 (scores, cls, tp) 는 길이가 제각각이라 이어 붙이고 길이를 따로 둔다
    lens = np.array([len(p[0]) for p in pf], np.int64)
    cat = lambda j, dt: np.concatenate([p[j] for p in pf]).astype(dt) if len(pf) else np.zeros(0, dt)
    np.savez_compressed(path, S=cat(0, np.float32), C=cat(1, np.int8), T=cat(2, np.int8), lens=lens, ngt=ngt)

def load_matches(path):
    z = np.load(path)
    cut = np.cumsum(z['lens'])[:-1]
    pf = list(zip(np.split(z['S'], cut), np.split(z['C'], cut), np.split(z['T'], cut)))
    return pf, z['ngt']

CACHE = {}
for a, s in RUNS:
    rid = f'{a}-s{s}'
    dst = os.path.join(RESULTS, 'runs', rid)
    mp = os.path.join(dst, 'matches.npz')
    if os.path.exists(mp):
        CACHE[rid] = load_matches(mp)
    else:
        t0 = time.time()
        preds = YD.predict_boxes(os.path.join(dst, 'last.pt'), held_paths, device=0,
                                 imgsz=TRAIN['imgsz'], score_thresh=0.001, batch=64, progress_every=0)
        pf, ngt = H.collect_matches(preds, gts)
        save_matches(mp, pf, ngt)
        CACHE[rid] = load_matches(mp)          # 저장한 것을 다시 읽어 쓴다 — 저장·복원이 같음을 매번 거친다
        print(f'{rid} 평가 {(time.time() - t0) / 60:.1f} 분')
    aps, m = H.ap_from_matches(*CACHE[rid])
    print(f'{rid:9s} mAP50 {m:.4f}   ' + '  '.join(f'{k} {v:.3f}' for k, v in aps.items()))
"""),
        md(put("""
## 8. Δ mAP · 부트스트랩 CI · 오프라인 대조 → `m3_eval.json`

rev.9 y5 와 같은 절차 — held-out 프레임과 학습 시드를 **함께** 리샘플(paired), @@BOOT@@회, rng 시드 @@BS@@.
오프라인 기준은 `rev.9/04_중앙AI/runs_yolo/axis2/eval_axis2_mc.json`.
⚠ 숫자가 오프라인과 달라도 실패가 아니다 — 실패는 숫자가 **나오지 않는** 것이다(PRD).
""", BOOT=str(BOOT), BS=str(BOOT_SEED))),
        code(put("""
OFFLINE = @@OFFLINE@@
res = {'runs': {}, 'conditions': {}, 'env': ENV, 'provenance': PROVENANCE, 'tar_sha256': EXPECTED,
       'n_held_out': len(held_ids),
       'held_out_ids_sha256': hashlib.sha256(','.join(map(str, held_ids)).encode()).hexdigest(),
       'boot': @@BOOT@@, 'boot_seed': @@BSEED@@, 'offline': OFFLINE}
for a, s in RUNS:
    rid = f'{a}-s{s}'
    tj = json.load(open(os.path.join(RESULTS, 'runs', rid, 'train.json')))
    aps, m = H.ap_from_matches(*CACHE[rid])
    res['runs'][rid] = dict(tj, mAP50=m, ap_per_class=aps, weights_uri=f'drive:m3_results/runs/{rid}/last.pt')
shas = {r['train_args_sha'] for r in res['runs'].values()}
print('학습 인자 해시', shas, '— 여섯 런이 같다' if len(shas) == 1 else '★ 런마다 다르다')
for a in ('STA_B', 'DYN_B'):
    vals = [res['runs'][f'{a}-s{s}']['mAP50'] for s in (1, 2, 3)]
    res['conditions'][a] = dict(per_seed=vals, mean=float(np.mean(vals)), sd=float(np.std(vals, ddof=1)))

rng = np.random.default_rng(res['boot_seed'])
n, seeds = len(held_ids), (1, 2, 3)
diffs = []
for _ in range(res['boot']):
    idx = rng.integers(0, n, n)
    sidx = rng.integers(0, len(seeds), len(seeds))
    va, vb = [], []
    for si in sidx:
        sd = seeds[si]
        va.append(H.ap_from_matches(*CACHE[f'STA_B-s{sd}'], idx)[1])
        vb.append(H.ap_from_matches(*CACHE[f'DYN_B-s{sd}'], idx)[1])
    diffs.append(np.mean(vb) - np.mean(va))
lo, hi = np.percentile(diffs, [2.5, 97.5])
point = res['conditions']['DYN_B']['mean'] - res['conditions']['STA_B']['mean']
res['DYN_B_vs_STA_B'] = dict(delta=float(point), ci=[float(lo), float(hi)],
                             relative_pct=float(100 * point / res['conditions']['STA_B']['mean']))
with open(os.path.join(RESULTS, 'm3_eval.json'), 'w') as f:
    json.dump(res, f, indent=1, ensure_ascii=False)

print()
print(f"{'':10s}{'파이프라인':>22s}{'오프라인 (rev.9)':>22s}")
for a in ('STA_B', 'DYN_B'):
    c, o = res['conditions'][a], OFFLINE[a]
    print(f"{a:10s}{c['mean']:>14.4f} ± {c['sd']:.4f}{o['mean']:>14.4f} ± {o['sd']:.4f}")
e, oe = res['DYN_B_vs_STA_B'], OFFLINE['DYN_B_vs_STA_B']
print(f"{'Δ mAP50':10s}{e['delta']:>+14.4f}{'':8s}{oe['delta']:>+14.4f}")
print(f"{'95% CI':10s}  [{e['ci'][0]:+.4f}, {e['ci'][1]:+.4f}]    [{oe['ci'][0]:+.4f}, {oe['ci'][1]:+.4f}]")
print()
print('저장', os.path.join(RESULTS, 'm3_eval.json'), '— 이 파일을 내려받아 data/m3/ 에 둔다')
""", OFFLINE=json.dumps(off, indent=1), BOOT=str(BOOT), BSEED=str(BOOT_SEED))),
    ]
    return {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"provenance": []},
                                         "kernelspec": {"display_name": "Python 3", "name": "python3"},
                                         "language_info": {"name": "python"}},
            "nbformat": 4, "nbformat_minor": 5}


def main() -> int:
    nb = build()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"저장 {OUT} ({os.path.getsize(OUT) / 1e3:,.0f} KB, 셀 {len(nb['cells'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
