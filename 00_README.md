# NeedSlack Cloud — 현재 상태와 다음 할 일

최종 갱신 **2026-09-22** · **현재 위치: M2 완료 · M3 노트북 준비됨 — Drive 업로드와 Colab 실행(사람) 대기**

> 세션을 이어서 시작한다면 **`CLAUDE.md` §0** 의 절차를 따른다.
> 도커 스택은 세션 사이에 내려 둔다 — 이어서 스택이 필요하면 Docker Desktop 을 켜고 `python run.py up`.

> NeedSlack 엣지 가속기가 선별한 데이터를 클라우드로 옮겨 저장·가공·재학습하는
> **Edge-to-Cloud 경로**를 실제로 만들어, rev.9 하드웨어 실험의 산업적 전제가
> 실재함을 보인다.

## ★ 포지셔닝 — 먼저 읽을 것

**이 프로젝트에는 실험이 하나뿐이다.**

```
주 실험   rev.9 하드웨어 · 동적 vs 정적 대조
          STA_B 1.00 타일/프레임  mAP50 0.2989
          DYN_B 1.85 타일/프레임  mAP50 0.3230
          Δ mAP +0.0241  95% CI [+0.0115, +0.0369]

클라우드  두 번째 실험이 아니다.
          첫 번째 실험의 산업적 전제가 실재함을 보이는 **계측 장치**다.
```

**필수 조건 — 대조가 파이프라인을 통과해야 한다.** 한 팔만 태우면 "데이터 파이프라인을
만들었다"가 되어 대조가 사라진다. **STA_B · DYN_B 두 팔을 모두** 통과시킨다.
두 팔의 선별 배열은 이미 산출물로 존재한다:

```
rev.9/04_중앙AI/selections_axis2/{STA_B,DYN_B}_mc_rate0.10_s{1,2,3}.npy
```

**증명할 명제**: rev.9 가 오프라인으로 낸 **Δ mAP +0.0241** 이, 엣지 판정 → 업링크 →
수집 → 데이터셋 → 재학습 → 평가라는 실제 경로를 통과해도 **재현된다.**
(재현되지 않아도 결과다 — 어느 단계가 대조를 흐리는지가 드러난다.)

상세는 보고서 **§5**. 이번 범위 밖으로 민 것(OTA 되먹임·폐루프 수렴성·대조 함대)은
§5-5 에 **향후 연구**로 한 번만 적혀 있다.

---

## 문서 규율 — 이력을 누적하지 않는다

> 문서를 갱신할 때 **폐기된 내용은 지우고 새 내용으로 교체한다.**
> "이전 안은 X였으나 폐기" 같은 기록을 남기지 않는다. 문서를 읽으면 **현재 상태가
> 무엇인지 모호하지 않아야** 한다.
>
> **왜**: rev.9 에서 세션별 기록을 전부 남긴 결과, 폐기된 기록을 현재 상태로 착각해
> 잘못된 결론을 도출한 사고가 반복됐다. 범위 밖으로 민 것은 이력이 아니라
> **"향후 연구"로 간결하게 한 번만** 적는다.

---

## 0. rev.9 와의 관계 — 읽기 전에 알아야 할 것

```
0718/0718/0718/
├ rev.9/              ← 하드웨어 연구. 논문/경진대회 산출물로 **동결**
└ needslack-cloud/    ← 이 프로젝트. 수명주기·툴체인이 다르다
```

**이 프로젝트는 `rev.9` 를 import 하지 않는다.** 대신 대역·지연·큐 계약을
`schema/edge_contract.json` 으로 **스냅샷**해 쓴다. 손으로 옮겨 적은 값이 아니라
`schema/gen_edge_contract.py` 가 `rev.9/02_전처리/config.py` 를 **실제로 실행해** 뽑은 값이다.

```bash
cd schema && python gen_edge_contract.py     # rev.9 가 바뀌면 다시 돌린다
```

생성기는 `config.py` 의 sha256 앞 16자를 기록하고, **자기 검증**을 한 번 한다 —
`uplink_drive_hours_until_full()` 이 `delta_max × 24 h` 와 같은지. 어긋나면 실패한다
(`갈래A_전환.md §6-3` 이 기각한 초안 상태를 다시 들이지 않기 위한 장치).

> **왜 스냅샷인가.** rev.9 의 재현성 규율(`gen_results_rev10.py` — "문서를 손으로 쓰지 말고
> 산출물에서 구워낸다")을 경계를 넘어 이어붙인 것이다. 수치를 바꾸려면 rev.9 를 고치고
> 생성기를 다시 돌려야 한다.

---

## 1. 현재 있는 것

| 경로 | 내용 | 상태 |
|---|---|---|
| `docs/분석보고서_Edge투Cloud아키텍처.md` | 수집·백엔드/DB·MLOps/OTA·비용 4부 설계 분석 | ✅ 초판 |
| `docs/PRD_쐐기.md` | **2주 쐐기** — 한 문장 · 성공 판정 · 암묵지 10건 · Day 흐름 | ✅ 초판 |
| `docs/구현설계_MVP.md` | **M1 착수 전 설계 틀** — 작업 순서 · 와이어 계약 · 스키마 · 런 모델 · 로컬 스택 · 검증 | ✅ 초판 |
| `docs/온보딩_백엔드_도메인지식.md` | 리더용 도메인 브리핑 — 확정/미정/함정 구분, 지시 가이드 | ✅ 초판 |
| `CLAUDE.md` | **작업 규칙.** 시작 전 읽을 문서 · 포지셔닝 · 수치 · 문서 · 의존성 · 커밋 · 경계 | ✅ |
| `docs/설계결정-0001-엣지-회수기전-중립성.md` | 선점·재개 ↔ 비선점 승인제어가 클라우드 계약에 미치는 영향 | ✅ 채택 |
| `docs/build_artifact.py` | 위 문서들의 웹 렌더러 (`arch` / `brief`) | ✅ |
| `schema/verdict.py` | **S1 와이어 계약** — 판정 레코드·frame_uuid·상태기계·런 모델 | ✅ |
| `schema/pg/001_init.sql` (+`.down`) | **S2** PostgreSQL 계보 5테이블 | ✅ |
| `schema/ddb_table.json` | **S2** DynamoDB 테이블/GSI1 정의 | ✅ |
| `schema/arm_signals.json` · `gen_arm_signals.py` | 팔별 가속기 산출 스냅샷(tiles_scored·tile_mask·value_score). rev.9 venv 로 생성, **두 팔 × 3시드 선별 재현을 자기검증** | ✅ |
| `edge-agent/contract.py` | `edge_contract.json` 런타임 로더 | ✅ |
| `edge-agent/npyread.py` | `.npy` 선별 배열 리더 (**stdlib만**. numpy 교차검증 통과) | ✅ |
| `edge-agent/frames.py` | `frames.bin` 랜덤접근 리더 (stdlib만) | ✅ |
| `edge-agent/uplink.py` | **토큰버킷·store&forward·즉시 드롭** — 계약 집행부 | ✅ |
| `edge-agent/replay.py` · `armsignals.py` | **차량 시뮬레이터** — pool 전량 재생 → 판정·텔레메트리(MQTT) · 번들(S3). 두 팔 dry-run 완주 · 로컬 스택 스모크 통과 | ✅ |
| `common/aws.py` | AWS 어댑터 — boto3·paho 는 여기서만 | ✅ |
| `infra/docker-compose.yml` · `mosquitto.conf` | 로컬 스택 4서비스. 이미지 **태그@다이제스트 고정**. MinIO 버킷 알림 → MQTT (`queue_dir` 보관). 오프라인 세션 큐 100,000건 | ✅ 4개 모두 healthy |
| `infra/bootstrap/00_check.ps1` | 환경 점검 (WSL · Docker Desktop · 엔진). **UTF-8 BOM 필수** — §5 | ✅ 통과 |
| `infra/bootstrap/01_create.py` | 버킷·버킷 알림 규칙·테이블·스키마 생성. **2회 실행으로 멱등 확인** | ✅ 실행 |
| `ingest/store.py` | **키 규약·상태 전이의 단일 구현.** `DynamoTable`(실제) · `MemoryTable`(테스트) 두 구현 위에 선다 | ✅ |
| `ingest/collector.py` | **수집기** — 판정 → `FRAME#` PENDING + 번들 색인, 텔레메트리 → `TEL#` + DROPPED | ✅ M1 완주 |
| `ingest/subscriber.py` | 수집기·조정기 공통 구독 루프 — 영속 세션 · 수동 PUBACK · **처리는 작업 스레드**(§5 함정) | ✅ |
| `tests/` | **110건 전부 통과** (stdlib unittest). 스택 없이 돈다. 스택이 떠 있으면 `test_store_dynamo` 가 같은 시나리오를 DynamoDB Local 에 돌려 두 구현을 대조한다(없으면 건너뜀, 약 50 초). `test_lineage` 는 PostgreSQL 에서 롤백 트랜잭션으로 역추적 조인을 검사한다 | ✅ |
| `.env.example` | 환경 의존값 예시 | ✅ |
| `run.py` | **단일 진입점** — `check` `up` `down` `bootstrap` `test` `contract` `replay` `collect` `reconcile` `status` `guide` | ✅ |
| `requirements.txt` | 승인된 의존성 — boto3 · psycopg[binary] · paho-mqtt | ✅ |
| `.venv/` · `.env` | 전용 파이썬 3.12.10 환경 · 로컬 환경값 (둘 다 커밋 안 됨) | ✅ |
| `ingest/reconciler.py` | **조정기** — S3 이벤트 · 버킷 대조 · UPLOADED 스윕 → 체크섬 → INDEXED / QUARANTINED | ✅ M1 완주 |
| 운영 테이블·버킷 | `ns-curation-index` · `ns-raw-payload` 에 **두 팔 각 1회분**(판정 2,449 · 번들 70씩, 502.1 + 524.9 MB). 판정은 전부 `IN_DATASET` | ✅ 두 팔 |
| `pipelines/snapshot.py` | **M2 스냅샷** — rev.9 선별·학습 목록 대조 → 번들에서 이미지 추출(sha256·원본 대조) → 결정적 tar · manifest · PG 계보 → `IN_DATASET`. 형식은 **설계결정-0002** | ✅ 두 팔 동결 |
| `data/snapshots/` | `ds-STA_B.tar` · `ds-DYN_B.tar` · `eval-common.tar` (+ `.sha256`) — 커밋하지 않는다. Drive 에 올릴 것 | ✅ 1.63 GB |
| `docs/설계결정-0002-M3-학습데이터-이관.md` | Colab 은 MinIO 에 닿지 않는다 → tar 로 묶어 Drive 로. rev.9 축 2 학습 레시피 고정 | ✅ 채택 |
| `pipelines/colab_train.ipynb` · `build_colab_nb.py` | **M3 노트북** — tar sha256 대조 → 6런 학습 → held-out 평가 → Δ · CI → `m3_eval.json`. rev.9 학습·평가 코드를 원문 그대로 싣는다. `python run.py notebook` 이 굽는다 | ✅ 실행 대기 |
| `pipelines/compare.py` | **M3 대조** — `m3_eval.json` 검증(tar sha256 · 인자 해시 6런 동일 · held-out · rev.9 코드) → `model_run` · `eval_result` 등록 → 오프라인과 시드별·Δ·CI 대조 → `data/m3/compare.json`. 판정 없이 사실만 | ✅ 결과 대기 |
| `docs/build_primer.py` | 마일스톤 입문 페이지(`python run.py primer m2|m3`) — 진행 현황 한 판을 읽는 데 필요한 개념만 | ✅ |
| `.git/` `.gitignore` `.gitattributes` | 저장소 (main 브랜치) | ✅ 기반선 커밋됨 |

**웹 렌더링본** (Mermaid 도면이 그려집니다)

| 문서 | 링크 | 재빌드 |
|---|---|---|
| 아키텍처 분석 보고서 | https://claude.ai/artifact/CccT619ASK9rxZz1hQubsD | `python docs/build_artifact.py arch` |
| 백엔드 도메인 브리핑 | https://claude.ai/artifact/1o3cfRanM6XqocH9wHddts | `python docs/build_artifact.py brief` |
| **진행 현황** (마일스톤 · 재생 실측 · 발견) | https://claude.ai/artifact/BmhfRsECuYyeSDFERS3GHr | `python run.py status` → `docs/_build/status.html` 재배포 |
| **도메인 입문** (MQTT · MinIO · 두 평면 · 핵심 개념) | https://claude.ai/artifact/MMcuW7w7tdUMronLtuQdnP | `python run.py guide` → `docs/_build/guide.html` 재배포 |

> 진행 현황·도메인 입문은 **갱신할 때마다 별도 아티팩트로 올린다** — 같은 URL 에 덮어쓰지 않는다(지난 판을 나란히 볼 수 있게).
> 위 두 링크는 M2 완료 시점 판이다. 이후 판: 진행 현황 M3 노트북 준비 https://claude.ai/artifact/WBzdoS84TonnXcDNnistcF ·
> 입문 M2 동결과 계보 https://claude.ai/artifact/Vts2wJszy5sg2kMsXRJNTs · 입문 M3 재학습과 판정 https://claude.ai/artifact/VoayadzQRBV7eVBBWPD7Hc. 판마다의 링크는 claude.ai 갤러리(`/artifacts`)에 제목으로 쌓인다. 배포는 별도 단계입니다.

---

## 2. 계약 요약 — 모든 설계가 여기서 떨어진다

출처는 전부 `schema/edge_contract.json`. 이 표는 읽기 편의용 사본이므로,
**인용할 때는 JSON 을 보라.**

| 항목 | 값 | 성격 |
|---|---|---|
| 후보 스트림 `f_cand` | 20.0 Hz | 유도 (`1000 / T_FRAME_MS`) |
| 순시 선별률 `rate` | 0.10 | 정책 |
| 프레임 페이로드 `S` | 144,500 B | 실측 |
| 링크 명목 대역 `R_up` | 1,000,000 bit/s | **유일한 자유 파라미터** |
| 업링크 수요 | 2,312,000 bit/s | 유도 |
| 지속 선별률 | 0.0433 | 유도 |
| 주행 듀티 상한 `δ_max` | 0.4325 (10.38 h/일) | 유도 |
| **판정 지연 상한** | ≤ 50 ms | RTL/FSM 이 강제 (기전은 구현별 — **설계결정-0001**) |
| **도착 지연 상한 `L_max`** | 49,030 s = 13.62 h | 유도 (즉시 드롭 + 일주기 폐쇄) |
| 큐 요구 용량 `Q_min` | 6.13 GB | 유도 |
| 드롭 정책 | `drop` | 정책 선언 |

### ★ 이 계약에서 아키텍처가 즉시 유도되는 지점

```
판정 지연 ≤ 50 ms   vs   도착 지연 ≤ 49,030 s        비 ≈ 9.8 × 10⁵
```

지연 SLO 가 여섯 자리수 다른 두 스트림은 **같은 전송 관을 쓸 수 없다.**
이것이 수집 파이프라인을 세 평면(판정 / 벌크 페이로드 / 안전 이벤트)으로 가르는 근거다.
상세는 보고서 §0-1, §1-2.

---

## 3. 이 프로젝트가 걸려 있는 가정

| # | 가정 | 왜 위험한가 |
|---|---|---|
| 1 | **`UPLINK_LINK_UP_WHEN_PARKED = True`** | 모뎀이 주차 중 잠들면 배출 항이 사라져 **어떤 듀티에서도 큐가 발산**한다. 계약 전체가 여기 걸려 있다 |
| 2 | **AWS 단가는 차수 추정** | 보고서 §4-2 의 절대액은 리전·약정·통신 계약에 따라 배수로 움직인다. 결론은 절대액이 아니라 **10배 비율**에 걸려 있다 |
| 3 | **`rate = 0.10` 은 2 Hz 에서만 검증됨** | 축 2 실험은 nuScenes 키프레임(2 Hz) 풀이었다. 실차 20 Hz 는 후보 밀도가 10배 → **근중복**이 발생하며, 클라우드 중복 제거로 흡수하지만 **효과는 미측정** |
| 4 | **품질 논거는 선점 입도 `k → ∞` 위** | Δ mAP +0.0241 은 `L_pre ≈ 0` 가정에서 측정됐다. 거친 입도에서는 부호가 뒤집힌다 — **설계결정-0001 §3-3** |
| 5 | **`S` = 144,500 B 는 전체 프레임 평균이다** | 재생 **실측**: 전체 34,149 장 평균 149,047 B(계약 근거와 일치)인데, 가치 기반 선별분은 **STA_B 214,340 B · DYN_B 205,005 B (+42~48%)**. 선별기가 복잡한 장면 = 큰 JPEG 를 고른다. 업링크 수요 · `δ_max` · `L_max` 가 이 만큼 낙관적이다. 이번 런 규모에서는 큐 최대 388 MB ≪ `Q_min` 6.13 GB 라 드롭 0 · 도착 지연 최대 3,106 s ≪ 49,030 s. **결정: 계약은 동결이라 고치지 않고, 실측 S 로 같은 식을 다시 돌린 값을 병기한다** — 수요 3.43 / 3.28 Mbps · δ_max 7.00 / 7.32 h · L_max 17.0 / 16.7 h · Q_min 7.65 / 7.51 GB (STA_B / DYN_B, 유도: `EdgeContract.rederive` × 재생 실측 평균. 진행 현황 페이지 "발견" 절이 매번 다시 굽는다) |

### 재생 실측 — 두 팔 dry-run (2026-09-22)

| | STA_B | DYN_B |
|---|---|---|
| 후보 · 선별 · 수락 · 드롭 | 24,491 · 2,449 · 2,449 · 0 | 24,491 · 2,449 · 2,449 · 0 |
| 판정 메시지 (1초 배치) | 633 | 732 |
| 번들 (35프레임) · 용량 | 70 · 524.9 MB | 70 · 502.1 MB |
| 큐 최대점유 | 387.9 MB | 349.5 MB |
| 도착 지연 최대 (번들 PUT 기준) | 3,106 s | 2,801 s |
| 모의시간 주행 + 주차 배출 | 1,224.5 + 3,100 s | 1,224.5 + 2,795 s |

### 완주 실측 — 두 팔, 운영 테이블 (2026-09-22)

수집기·조정기를 켠 채 `python run.py replay {ARM}`(12.5배속), 팔당 1회. 두 팔 모두 **재생 보고와 일치**했다.

| 항목 | STA_B | DYN_B |
|---|---|---|
| 판정 | 2,449 전부 **INDEXED** · QUARANTINED 0 | 2,449 전부 **INDEXED** · QUARANTINED 0 |
| 기전 필드 | `static_reserve` · 타일 1 | `preempt_resume` · 타일 1–3 |
| 번들 · S3 | 70 도착 · 524.9 MB | 70 도착 · 502.1 MB |
| 도착 지연 최대 (재생 보고) | 3,106.0 s (3,106 s) | 2,801.4 s (2,801 s) |
| 도착 지연 중앙값 · 최소 | 642.7 s · 0.8 s | 369.9 s · 5.8 s |
| 벽시계 (재생 · 전부 INDEXED 까지) | 346 s · 603 s | 323 s · 609 s |

> DYN_B 런에서 조정기가 120 s 마다 MQTT 재연결했다(이벤트 74 = 번들 70 + 재전송 4, 멱등이라 결과 무해).
> 원인과 수정은 §5 함정 표. STA_B 런은 수정본으로 돌아 끊김 0 이었다.
> STA_B 런에서는 수집기 적체(아래 🟡 1 의 잔여 메시지 347 건) 때문에 번들이 판정보다 먼저 도착해,
> 판정 2,449 건 전부가 **"번들 먼저" 경로**(수집기가 UPLOADED 로 옮기고 스윕이 검증)로 INDEXED 에 닿았다 — 수용 기준 6 의 한 경우가 운영에서 실제로 돌았다.

### 스냅샷 실측 — M2 (2026-09-22)

`python run.py snapshot common|STA_B|DYN_B`. 멈춤 조건(설계결정-0002 §4 · `pipelines/snapshot.py` 머리말)을 전부 통과했다.

| | STA_B | DYN_B | eval-common |
|---|---|---|---|
| 프레임 | 2,449 = rev.9 정본 선별 = rev.9 학습 목록 | 2,449 = 같음 | val 800 · held-out 2,826 |
| held-out 과 겹침 | 0 | 0 | — |
| 번들에서 자른 바이트 | 2,449 장 전부 sha256 · rev.9 원본과 일치 | 같음 | (rev.9 원본) |
| 기전 · 프로파일 | `static_reserve` · `db9ee30e4b9fd7bd` | `preempt_resume` · 같음 | — |
| tar | 530.6 MB · `e5976308ad26…` | 507.7 MB · `29000414531…` | 590.3 MB · `d20cf6fdde16…` |

- 두 팔이 함께 고른 프레임은 **2,043** 장이다 — 서로 다른 것은 406 장(16.6%)뿐이고, 대조는 이 차이에서 나온다.
- 다시 돌려도 sha256 이 같고 계보는 `same`(동결 확인). 전체 sha256 은 `data/snapshots/*.sha256` 과 MinIO
  `ns-datasets/datasets/ds-{ARM}/snapshot.json`.

> **발견 — rev.9 `frames.bin` 의 `conf_q8`·`det_count` 가 interim 현재본과 다르다**
> (pool 기준 불일치 conf_q8 24,274 · det_count 19,188 / 24,491. mean_p·kinematic_u8 은 0).
> 판정에는 보드 레코드(frames.bin)를 싣는다. need_profile 이 `w_conf = 0` 이라 value_score 는
> 불변(`gen_arm_signals.py` 가 검증)이고, 축 2 선별은 타일 YOLO 점수라 **대조와 무관**하다.
> rev.9 는 동결이므로 고치지 않는다. 기록: `schema/arm_signals.json` `_findings`.

---

## 4. 다음 할 일

### 🔴 먼저 — M3 Colab 학습 (결론)

1. **사람이 할 일** — Google Drive 의 한 폴더에 네 파일을 올리고 Colab 에서 **런타임 → T4 GPU → 모두 실행**
   - `data/snapshots/` 의 `ds-STA_B.tar` · `ds-DYN_B.tar` · `eval-common.tar` (합계 1.63 GB) + `pipelines/colab_train.ipynb`
   - 세션이 끊기면 다시 "모두 실행" — 끝난 런은 건너뛴다. 학습은 T4 에서 20 분 안팎(추정 — rev.9 ybase 실측에서 환산), 평가 몇 분
   - 끝나면 Drive `m3_results/m3_eval.json` 을 `data/m3/m3_eval.json` 으로 내려받는다
2. **`python run.py compare`** — 준비됐다. 결과 파일을 검증하고 계보에 등록한 뒤 오프라인 `eval_axis2_mc.json` 과 대조한다.
   CI 는 노트북이 rev.9 와 같은 절차로 계산한다 — 로컬 `.venv` 에 numpy 가 없어서다

```bash
python run.py up          # 스택 기동 (healthy 까지 기다림) — 볼륨에 두 팔 · 계보가 남아 있다
python run.py test        # 단위 테스트 (스택이 떠 있으면 DynamoDB · PG 대조 포함, 약 50 초)
python run.py notebook    # 노트북 다시 굽기 — 스냅샷이 바뀌었을 때만
```

### 🟡 열린 질문 — 사람이 정할 것

없음.

### 🔲 그다음 — 마일스톤 (보고서 §6-5)

**범위는 무료로 구현 가능한 것으로 고정.** 단위는 함대 대수가 아니라 **런**이다 —
재생은 **팔당 1회**(선별이 시드 독립) — `pool` 24,491 후보 1회 통과 → 2,449 프레임 → 약 0.5 GB(실측).
**최소 실험 = 재생 2회 + 학습 2 팔 × 3 시드 = 6 런** (S3 1.03 GB 실측 · 상시 무료 5 GB 의 21%).

| M | 목표 | 산출물 | 비용 |
|---|---|---|---|
| **M1** | 한 팔이 로컬을 통과 | `edge-agent/` 시뮬레이터(`frames.bin` 재생) · `infra/docker-compose.yml` · MQTT 수집기·조정기 · DynamoDB Local · MinIO | **$0** |
| **M2** | 계보 + 스냅샷 | PostgreSQL 스키마(보고서 §2-5 DDL) · 데이터셋 동결 · 역추적 질의 | **$0** |
| **M3** | ★ **두 팔 × 3시드 재현** | Colab 재학습(`rev.9/08_이관` 재사용) · 평가 · **Δ mAP 대조표** | **$0** |
| **M4** | AWS 수직 절단면 1개 | IoT 정책(차량 격리) → Credential Provider → S3 접두사 스코핑. **판정 평면만** | 상시 무료 한도 내 |

> **M3 이 이 프로젝트의 결론이다.** M4 는 보안 경계가 클라우드에서도 성립함을 보이는
> 마감 작업이고, 한 평면만 올려도 증명된다.

**로컬 우선 원칙** — 모든 대체재가 같은 API 를 쓴다(MinIO=S3, DynamoDB Local=DynamoDB,
PostgreSQL=Aurora, Mosquitto=IoT Core, DuckDB=Athena). `boto3(endpoint_url=...)` 하나로
클라우드에 올라가므로 **코드를 두 번 쓰지 않는다.**

> ⚠️ `edge-agent/` 는 `schema/edge_contract.json` 을 **런타임에 읽어야 한다.**
> 토큰버킷 = `UPLINK_BPS`, 즉시 드롭 정책. 상수 하드코딩 금지.

### 🔲 범위에서 뺀 것 (보고서 §6-6)

**OTA 되먹임**(재학습 모델 → 선별) · Fargate 상시 서비스 · Athena/Glue ·
SageMaker Pipelines · 실차 연동 · 함대 부하시험.
**PII 블러/GDPR 은 실제 주행 영상을 다루기 전에 반드시 되돌아올 것.**

> OTA 프로파일 배포 **경로는 만들되**(보고서 §3-4), 그 결과를 다시 선별에 되먹이지
> 않는다. rev.9 와 동일하게 0단 모델은 고정이다.

---

## 5. 환경 — 이 PC 에서 확인된 것

| 항목 | 상태 | 확인 방법 |
|---|---|---|
| Docker | Docker Desktop **사용자 영역 설치** (`%LOCALAPPDATA%/Programs/DockerDesktop`) · 엔진 29.8.0 | `00_check.ps1` |
| WSL | WSL2 (기본 배포 `docker-desktop`). Windows 11 **Home** 이라 Hyper-V 백엔드가 없어 **WSL2 가 유일한 경로** | `wsl --status` |
| 파이썬 | `.venv/` (3.12.10) + `requirements.txt`. `run.py` 가 알아서 이 인터프리터로 다시 실행한다 | `.venv/Scripts/python -m pip list` |
| 도커 이미지 | 4개 모두 태그@다이제스트 고정. MinIO 는 `quay.io` (Docker Hub 저장소 소멸) | `docker compose -f infra/docker-compose.yml ps` |

**엔진은 Docker Desktop 이 실행 중일 때만 뜬다.** 재부팅 뒤 자동 시작되지 않으면
`00_check.ps1` 이 "Docker Desktop 이 실행 중이 아니다" 로 실패한다 — 앱을 실행하고 1~2분 기다린다.

### 환경 함정 — 모르면 반드시 밟는 것

| 증상 | 원인 | 대응 |
|---|---|---|
| `.ps1` 실행 시 `UnauthorizedAccess` | 실행 정책이 스크립트를 막는다 | `powershell -ExecutionPolicy Bypass -File …` (이 프로세스에만 적용, 시스템 정책 불변) |
| `.ps1` 이 `TerminatorExpectedAtEndOfString` 파싱 오류 | PowerShell 5.1 이 BOM 없는 파일을 cp949 로 읽어 한글이 깨지고 따옴표 짝이 어긋난다 | 한글이 든 `.ps1` 은 **UTF-8 BOM** 으로 저장. `tests/test_scripts.py` 가 검사 |
| 파이썬 한글 출력이 `UnicodeEncodeError` | 콘솔이 cp949 | **`PYTHONUTF8=1`** — 선택이 아니다 |
| `wsl` 출력이 깨짐 | `wsl.exe` 가 UTF-16 으로 출력 | `$env:WSL_UTF8=1` |
| 구독자가 정확히 120 s 마다 재연결 | paho 는 keepalive(60 s) 뒤 보낸 PINGREQ 의 응답을 다음 keepalive 까지 못 읽으면 스스로 끊는다. 콜백 안에서 번들(약 8 s)을 처리하면 응답이 밀린다 | 처리를 작업 스레드로 — `ingest/subscriber.py`. 콜백은 큐에 넣기만 한다 |
| 임시 자원 점검의 메시지가 운영 테이블에 들어옴 | 판정·S3 이벤트 토픽은 공용이다. 운영 수집기·조정기의 **영속 세션이 꺼져 있어도** 점검 재생의 메시지가 그 세션에 쌓였다가 다음 기동 때 처리된다 | 수집기·조정기 모두 평면 2 버킷이 아닌 판정·이벤트를 거부한다(`00deb41`). 점검은 운영 버킷을 쓰지 않는다 |
| 수집기가 꺼진 동안 보낸 메시지가 1,000건에서 끊김 | Mosquitto `max_queued_messages` 기본 1,000 — 초과분을 **경고 없이** 버린다 | `mosquitto.conf` 에 100,000 으로 반영됨 |
| `minio/minio` pull 이 `pull access denied` | Docker Hub 저장소가 사라졌다 (로그인해도 같다) | `quay.io/minio/minio` — compose 에 반영됨 |
| 파이프로 받은 `.ps1` 출력이 깨짐 | PowerShell 출력 인코딩이 cp949 | 스크립트에서 `[Console]::OutputEncoding` 을 UTF-8 로 |
| `docker info` 가 오류 문구를 내고도 종료코드 0 | 엔진 기동 중 API 가 500 을 준다 | 종료코드만 믿지 말고 응답 형태까지 확인 (`00_check.ps1` 이 그렇게 한다) |

**rev.9 는 `C:\Users\minch\needslack_venv` 를 쓴다** (numpy 2.5.3 · matplotlib · pillow ·
openpyxl. torch/ultralytics 는 없다 — 학습은 Colab 에서 돌았다). `rev.9/ns.bat` 이
이 venv 를 절대경로로 고정하고 `PYTHONUTF8=1` 을 함께 박아 둔다.
이 프로젝트는 그 venv 를 **공유하지 않는다** — 의존성 수명주기를 rev.9 와 묶지 않기 위해
`needslack-cloud/.venv` 를 따로 둔다.

### 확정된 입력 자산 (전부 존재 확인)

| 경로 | 크기 / 내용 |
|---|---|
| `rev.9/05_데이터셋/board/frames.bin` | 559,907,020 B = 16 + 34,149 × 16,396 ✅ |
| `rev.9/05_데이터셋/board/ref_frame.bin` | 16,384 B (128×128) — **`value_score` 계산 가능** |
| `rev.9/04_중앙AI/selections_axis2/` | 44 파일. `{STA_B,DYN_B}_mc_rate0.10_s{1,2,3}.npy` 포함 |
| `rev.9/05_데이터셋/yolo_ds/` | `images` `labels` `lists` `manifest.json` — 학습 데이터 |
| `rev.9/05_데이터셋/splits/` | `frame_index.csv` `scene_split.json` |
