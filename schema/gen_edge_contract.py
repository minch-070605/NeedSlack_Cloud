# -*- coding: utf-8 -*-
"""edge_contract.json 생성 — rev.9 config.py 에서 업링크 계약을 스냅샷으로 고정한다.

왜 스냅샷인가
-------------------------------------------------------------------------------
이 클라우드 프로젝트는 rev.9 를 **import 하지 않는다.** rev.9 는 논문/경진대회
산출물로 동결됐고, 클라우드는 수명주기가 다른 별도 프로젝트다. 그러나 대역·지연·
큐 용량은 rev.9 가 유도한 값이 유일한 진실이므로, 손으로 옮겨 적는 대신 이 스크립트가
**config.py 를 실제로 실행해** 값을 뽑아 JSON 으로 굳힌다.

rev.9 의 "문서를 빌드 산출물로 취급" 규율(gen_results_rev10.py)과 같은 장치다 —
수치를 바꾸려면 rev.9 를 고치고 이 스크립트를 다시 돌려야 한다.

사용
-------------------------------------------------------------------------------
    python gen_edge_contract.py                 # 기본 경로로 rev.9 탐색
    python gen_edge_contract.py <config.py 디렉터리>

REV9 경로가 바뀌면 --root 로 넘기거나 REV9_PREPROC 환경변수를 쓴다.
"""
import hashlib
import io
import json
import os
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PREPROC = os.path.abspath(
    os.path.join(_HERE, "..", "..", "rev.9", "02_전처리"))
OUT = os.path.join(_HERE, "edge_contract.json")

SCALARS = [
    ("UPLINK_BPS",                 "bit/s", "링크 명목 대역. 이 계약의 유일한 자유 파라미터"),
    ("UPLINK_PAYLOAD_BYTES",       "B",     "업로드 1프레임 페이로드. nuScenes 1600x900 JPEG 실측"),
    ("CAND_RATE_HZ",               "Hz",    "실차 후보 스트림. 1000 / T_FRAME_MS 에서 유도"),
    ("CAND_RATE_HZ_POOL",          "Hz",    "축 2 실험이 실제로 돌아간 주파수 (nuScenes 키프레임)"),
    ("UPLINK_DUTY_PERIOD_S",       "s",     "일주기 폐쇄의 주기. 튜닝값이 아니다"),
    ("UPLINK_DROP_POLICY",         "",      "토큰 고갈 시 정책. 'drop' 이라야 지연이 유계다"),
    ("UPLINK_LINK_UP_WHEN_PARKED", "",      "★ 이 모델 전체가 걸려 있는 유일한 강한 가정"),
    ("T_FRAME_MS",                 "ms",    "프레임 주기 = 판정 지연 상한"),
    ("D_RT_MS",                    "ms",    "멀티캠 트레이스 p99 = 정적 예약의 WCET 근거"),
    ("C_MAIN_MS",                  "ms",    "전경 평균 실행시간 (2대 운용점)"),
    ("SCORE_TILE",                 "px",    "타일 한 변. PE 어레이 규격"),
    ("SCORE_N_TILES",              "",      "프레임당 타일 수"),
    ("SPLIT_SEED",                 "",      "split 난수 시드. 데이터셋 재현성 앵커"),
]

DERIVED = [
    ("uplink_demand_bps",             "bit/s", "rate · f_cand · S · 8"),
    ("uplink_sustainable_rate",       "",      "링크가 지속 감당하는 선별률. SELECT_RATE 와 다른 양"),
    ("uplink_max_drive_duty",         "",      "delta_max. 큐가 발산하지 않는 주행 듀티 상한"),
    ("uplink_queue_growth_bps",       "B/s",   "주행 중 대역 결손"),
    ("uplink_max_arrival_latency_s",  "s",     "L_max. 도착 지연 상한 = (1 − delta_max) · 24 h"),
    ("uplink_queue_min_bytes",        "B",     "Q_min. ⑦ NVMe 요구 용량"),
    ("uplink_drive_hours_until_full", "h",     "Q_min 을 채우는 주행시간. delta_max × 24 와 일치해야 한다"),
]


def main():
    root = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("REV9_PREPROC", DEFAULT_PREPROC))
    cfg_path = os.path.join(root, "config.py")
    if not os.path.exists(cfg_path):
        sys.exit(f"config.py 를 찾지 못했다: {cfg_path}\n"
                 f"경로를 인자나 REV9_PREPROC 로 넘겨라.")

    sys.path.insert(0, root)
    import config as c                                    # noqa: E402

    src_sha = hashlib.sha256(
        io.open(cfg_path, "rb").read()).hexdigest()[:16]

    doc = {
        "_schema": "needslack.edge_contract/1",
        "_generated": str(date.today()),
        "_generator": "schema/gen_edge_contract.py",
        "_source": {
            "repo": "rev.9",
            "file": "02_전처리/config.py",
            "sha256_16": src_sha,
            "derivation": "01_문서/갈래A_전환.md §6 (업링크 예산 폐쇄)",
        },
        "_warning": "손으로 고치지 말 것. rev.9 를 고치고 생성기를 다시 돌려라.",
        "select_rate_inst": c.SELECT_RATE[0],
        "select_rate_p3": c.SELECT_RATE[1],
        "constants": {},
        "derived": {},
        "enforced_bounds": {
            "decision_latency_ms": {
                "value": c.T_FRAME_MS,
                "mechanism_preemptive": "stage2_scorer.v frame_start 가 partial_bank/preempt_pend 리셋",
                "mechanism_nonpreemptive": "slack_scheduler.v CHECK_S 승인 테스트 cost <= close_tick - elapsed",
                "note": "★ 상한은 두 구현에서 같고 강제 기전이 다르다. ADR-0001 참조",
            },
            "arrival_latency_s": {
                "value": c.uplink_max_arrival_latency_s(),
                "mechanism": "즉시 드롭 + 일주기 폐쇄",
            },
        },
    }

    for name, unit, note in SCALARS:
        doc["constants"][name] = {
            "value": getattr(c, name), "unit": unit, "note": note}
    for name, unit, note in DERIVED:
        doc["derived"][name] = {
            "value": getattr(c, name)(), "unit": unit, "formula": note}

    # 자기 검증 — 두 유도 경로가 일치해야 계약이 정합적이다 (갈래A_전환.md §6-3)
    a = doc["derived"]["uplink_drive_hours_until_full"]["value"]
    b = doc["derived"]["uplink_max_drive_duty"]["value"] * 24.0
    assert abs(a - b) < 1e-6, (
        f"큐 용량이 듀티 상한과 불일치: {a:.4f} h vs {b:.4f} h. "
        f"rev.9 §6-3 이 기각한 초안 상태다.")
    doc["_selfcheck"] = {
        "queue_vs_duty_hours": round(a, 6), "passed": True}

    io.open(OUT, "w", encoding="utf-8", newline="\n").write(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"저장: {OUT}")
    print(f"  config.py sha[:16] = {src_sha}")
    print(f"  자기검증 통과: 큐 용량 ↔ 듀티 상한 {a:.4f} h")


if __name__ == "__main__":
    main()
