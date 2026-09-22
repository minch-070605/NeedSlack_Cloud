# MVP 구현 설계 틀 — M1 착수 전에 고정할 것

> 이 문서는 **코드를 쓰기 전에 고정해야 하는 인터페이스와 순서**만 담는다.
> 근거와 대안 분석은 `분석보고서_Edge투Cloud아키텍처.md` 에 있다 — 여기서 반복하지 않는다.
>
> **무엇을 고정하는가**: 와이어 계약 · 저장 스키마 · 런 식별 · 로컬 스택 · 검증 수단.
> 이 다섯은 나중에 바꾸면 이미 쓴 코드를 버려야 하므로 먼저 정한다.

## 0. 범위 제약 — 비용 $0

**이 프로젝트는 무료로 구현 가능한 선에서만 진행한다.** 설계 판단이 갈릴 때 이 제약이
우선한다.

| | |
|---|---|
| **M1~M3** | **전액 $0.** 전부 로컬(Docker) + Colab 무료 GPU |
| **M4** | AWS **상시 무료 한도 안에서만.** 판정 평면 하나만 올린다 |
| **금지** | 과금이 발생하는 서비스 도입 — Fargate · Athena/Glue · SageMaker 학습 · Aurora · RDS |
| **절차** | 유료 서비스가 필요하다고 판단되면 **도입 전에 반드시 묻는다** (`CLAUDE.md §4`) |

**로컬 우선 원칙**: MinIO=S3 · DynamoDB Local=DynamoDB · PostgreSQL=Aurora ·
Mosquitto=IoT Core · Colab=SageMaker. 전부 같은 API 를 쓰므로 `endpoint_url` 하나로
클라우드에 올라간다 — **같은 기능을 두 번 구현하지 않는다**(§5-1).

> ⚠️ AWS 프리티어 한도를 **설계 근거로 삼지 않는다.** 계정 유형·리전마다 다르다.
> 한도는 점검 대상이지 전제가 아니다. (보고서 §6-1)

---

## 1. 작업 순서

### Phase 0 — 설계 틀 (코드 0줄)

| # | 항목 | 왜 이 순서인가 | 의존 |
|---|---|---|---|
| **S1** | **와이어 계약** — 판정 레코드 · `frame_uuid` · 상태기계 | **가장 먼저.** 레코드 형태가 정해져야 스키마도 테스트도 나온다. 그리고 와이어는 나중에 바꾸기 가장 비싸다(필드 삭제·의미 변경 금지) | — |
| **S2** | **저장 스키마** — DynamoDB 키/GSI · PostgreSQL DDL | 레코드가 정해져야 키가 나온다 | S1 |
| **S3** | **런 모델** — `run_id` · `vin` · 격리 규약 | 6런을 어떻게 구별하고 섞이지 않게 하는지 | S1 |
| **S4** | **로컬 스택** — `docker-compose` 서비스·포트·부트스트랩 | 스키마가 정해져야 부트스트랩이 나온다 | S2 |
| **S5** | **검증 전략** — 수용 기준 7개 ↔ 테스트 매핑 | 무엇을 통과로 볼지 먼저 정한다 | S1–S3 |

### Phase 1~4 — 구현

```
M1  한 팔이 로컬을 통과
    1. infra/          docker-compose + bootstrap (버킷·테이블·스키마 생성)
    2. edge-agent/     재생기 — frames.bin + 선별 인덱스
    3. edge-agent/     업링크 — 토큰버킷 · store&forward · 즉시 드롭
    4. ingest/         수집기 — MQTT 구독 → DynamoDB 조건부 쓰기
    5. ingest/         조정기 — S3 이벤트 → 상태 전이
    6.                 DYN_B 재생 1회 완주 확인 (판정 → INDEXED)

M2  계보 + 스냅샷
    7. schema/pg/      PostgreSQL 스키마 적용
    8. pipelines/      스냅샷 — DynamoDB → manifest.jsonl + PG 등록
    9. tests/          역추적 질의 (조인 한 번)

M3  두 팔 × 3시드 재현   ← 이 프로젝트의 결론
   10. pipelines/      Colab 노트북 — manifest → 학습
   11. pipelines/      평가 — held-out mAP + 부트스트랩 CI
   12.                 6런 실행 → Δ mAP 대조표

M4  AWS 수직 절단면
   13. infra/aws/      IoT 정책(토픽 정책변수 격리) + Credential Provider
   14.                 엔드포인트 전환 — 판정 평면만 올림
```

> **M1 의 1→6 은 순서를 바꿀 수 없다.** 각 단계가 앞 단계의 출력을 입력으로 받는다.
> M2 이후는 M1 이 끝나야 시작한다.

---

## 2. S1 — 와이어 계약

### 2-1. 판정 레코드

원형은 `rev.9/02_전처리/s7_export_board.py` 의 `REC_FMT = "<II4B"` (12 B) 이고,
거기에 전역화 필드와 `설계결정-0001 §4` 의 기전 필드를 더한다.

| 필드 | 형 | 출처 | 비고 |
|---|---|---|---|
| `schema_version` | u16 | 고정 `1` | **모든 레코드에 반드시** |
| `vin` | str | 런 모델(§4) | `sim-{ARM}` |
| `boot_id` | str | 재생기 생성 | UUIDv4. 세션마다 새로 |
| `drive_session` | u32 | 재생기 | 시동 주기 |
| `frame_id` | u32 | `frames.bin` | **로컬 인덱스. 전역 유일 아님** |
| `t_frame_mono_ms` | u32 | `frames.bin` | 차량 단조 시계 |
| `t_frame_utc` | str | 재생기 환산 | ISO8601 |
| `conf_q8` `mean_p` `kinematic_u8` `det_count` | u8 ×4 | `frames.bin` 원형 | 실측 신호 |
| `value_score` | i32 | 재생기 계산 | `value_score.py` 와 정수 일치 |
| `tile_mask` `tiles_scored` | u8 ×2 | 재생기 | |
| `reclaim_mode` | enum | 런 설정 | `preempt_resume` / `admission_nonpreempt` / `static_reserve` |
| `granule_k` `l_pre_cyc` `resume_cnt` | u8 \| null / u32 / u16 | 런 설정 | 설계결정-0001 §4. MVP 는 `granule_k = null` · `l_pre_cyc = 0` — 아래 |
| `rtl_build` | str | 런 설정 | 비트스트림 식별자 |
| `selected` | bool | 선별 배열 | **이 프레임을 올리는가** |
| `profile_sha` | str(16) | `need_profile.json` 해시 | 재현성 앵커 |
| `payload` | obj \| null | 업링크 | `{bucket, key, offset, len, sha256}` |

**MVP 의 기전 필드.** 선별 배열은 rev.9 `y4_select_axis2.py` 의 파이썬 슬랙 모형이
만들었다. 팔마다 기전이 다르다 — `DYN_B` 는 `preempt_resume`(창에서 `L_pre` 를 빼지 않는
`k → ∞` 선점형의 이상화), `STA_B` 는 `static_reserve`(고정 창). `granule_k` 는 **`null`** —
헤드라인이 `k → ∞` 가정 위에 있어(설계결정-0001 §3-3) 정수로 적을 값이 없고, `null` 이
"입도 미지정"의 정식 표현이다. 같은 가정에서 `l_pre_cyc = 0`, `resume_cnt = 0`.
`rtl_build` 는 비트스트림이 아니라 `y4sim-<y4 sha 8자>` 다. 값은 `schema/arm_signals.json`
이, 필드 정의는 `schema/verdict.py` 가 단일 소스다.

**배치 봉투** — 메시지 1건에 N개 판정을 담는다.

```jsonc
{ "schema_version": 1, "vin": "sim-DYN_B", "sent_at": "...", "verdicts": [ /* … */ ] }
```

### 2-2. 키

```
frame_uuid = SHA256( vin ‖ boot_id ‖ drive_session ‖ frame_id )[:16]
```

`frame_id` 는 u32 로컬 인덱스라 재부팅·세션 경계에서 충돌한다. 위 복합키가 **멱등 키**를
겸한다 — 재전송은 확률이 아니라 확실성이므로 DynamoDB 조건부 쓰기로 흡수한다.

### 2-3. 상태기계 — MVP 축소판

```
PENDING ──(S3 이벤트)──▶ UPLOADED ──(체크섬 통과)──▶ INDEXED ──▶ IN_DATASET
   └──(TTL 만료 / 드롭 보고)──▶ DROPPED
```

> **`LABELED` 가 없다.** MVP 는 nuScenes 정답을 그대로 쓰므로 라벨링 단계가 존재하지 않는다.
> `AutoLabel`·`GroundTruth` 도 M1~M3 범위 밖이다.

- `PENDING` 은 **정상 상태**다. 오래 머무는 것으로 알람하지 않는다.
- 상태 전이는 **단조**. 역순 도착은 UPSERT 로 흡수한다.

### 2-4. 재생 규약 — 후보 전량을 재생하고, 선별분만 올린다

```
선별 파일  rev.9/04_중앙AI/selections_axis2/{ARM}_mc_rate0.10_s{SEED}.npy
           → 정렬된 int64 전역 프레임 인덱스 배열 (약 2,449개). 불리언 마스크가 아니다.
원본       rev.9/05_데이터셋/board/frames.bin
           → 16 B 헤더 + 34,149 × (12 B 레코드 + 128×128 픽셀)
```

재생기(`edge-agent/replay.py`)는 **`pool` 후보 24,491건 전부를 `f_cand` 로 흘리고**,
선별 배열에 든 프레임만 업링크 큐에 넣는다. 재생은 **팔당 1회**다(선별이 시드 독립).

> ⚠️ **선별분만 재생하면 안 된다.** 그러면 토큰버킷·드롭 정책·도착 지연이 **한 번도
> 실행되지 않는다.** 계약(§2 README)이 검증되는 지점이 정확히 거기다.

| 항목 | 규약 |
|---|---|
| 팔별 신호 | `tiles_scored`·`tile_mask`·`value_score` 는 `schema/arm_signals.json` 에서 읽는다. rev.9 가 numpy 난수로 순위 정합한 C_main 에서만 나오므로, `schema/gen_arm_signals.py` 가 rev.9 venv 로 한 번 굳힌다(두 팔 × 3시드 선별 재현을 자기검증) |
| 판정 평면 | **선별분만** 판정으로 발행. 모의 1초 배치(보고서 §1-2) |
| 텔레메트리 | 5분 주기 집계 — 후보 수 · `value_score>>5` 분포 · 타일 수 분포 · 큐 점유. 주차 배출 끝에 `final=true` 1건. 형식은 `verdict.Telemetry` |
| 드롭 보고 | 즉시 드롭된 선별 프레임은 판정을 내지 않고 텔레메트리 `dropped_frame_uuids` 로 알린다. 조정기가 DROPPED 를 기록한다. 판정의 `selected ⇒ payload` 불변식을 지킨다 |
| 페이로드 | rev.9 `yolo_ds/images/all/<frame_id:05d>.jpg` **실물 JPEG**. 큐는 실제 바이트 수를 과금한다 |
| 번들 | 35프레임(보고서 §1-2). 큐 투입 시점에 `key/offset/length/sha256` 이 정해져 판정에 실린다. 객체는 **마지막 프레임이 링크를 빠져나간 시각**에 PUT — 클라우드가 관측하는 도착 지연의 기준. 키 `vin={vin}/boot={boot_id}/session={NNNN}/bundle-{NNNNN}.bin` |
| 시각 | 차량이 내보내는 시각(`t_frame_utc` · `sent_at` · 번들 메타 `t-put-utc`)은 전부 **모의 시각의 UTC 환산**이다. 가속 재생에서 클라우드 벽시계로 도착 지연을 재면 틀린다 |
| 주차 | 주행이 끝나면 `UPLINK_LINK_UP_WHEN_PARKED` 가정 아래 큐가 빌 때까지 배출한다 |

---

## 3. S2 — 저장 스키마

### 3-1. DynamoDB `ns-curation-index` (MVP)

| 용도 | 키 |
|---|---|
| 프레임 단건 | `PK = FRAME#{frame_uuid}` · `SK = META` |
| 차량별 시계열 | `PK = VIN#{vin}` · `SK = TS#{t_frame_utc}#{frame_id}` |
| 상태별 조회 | **GSI1** `GSI1PK = VIN#{vin}#STATE#{state}` · `GSI1SK = {t_ingest_utc}` |
| 번들 → 판정 목록 | `PK = BUNDLE#{payload.key}` · `SK = FRAME#{frame_uuid}` — 수집기가 선별 판정마다 쓴다 |
| 번들 도착 표시 | `PK = BUNDLE#{payload.key}` · `SK = ARRIVED` — 조정기가 S3 이벤트마다 쓴다 |

**적재 단위는 `vin`(= 팔)이다.** 재생이 팔당 1회라 적재 시점에는 `run_id`(팔×시드)가
존재하지 않는다. `run_id` 는 학습 단계의 식별자이고 PostgreSQL `model_run` 이 가진다.

**조정 단위는 번들이다.** S3 이벤트 1건이 판정 35건을 UPLOADED 로 옮기므로, 번들 키에서
판정을 찾는 경로가 `BUNDLE#` 아이템이다. 판정과 번들 중 어느 쪽이 먼저 와도 나중에 온 쪽이
상대 행을 보고 전이한다(수용 기준 6 의 스큐 3경우). `BUNDLE#` 아이템은 `GSI1PK` 를 싣지
않아 GSI1 에 들어가지 않는다. 와이어 형식(번들 본문·판정 필드)은 바뀌지 않는다.

> **GSI2(점수 버킷 마이닝)는 MVP 에서 만들지 않는다.** 선별이 이미 오프라인에서
> 끝나 있어 클라우드가 점수로 고를 일이 없다. 보고서 §2-4 의 GSI2 는 목표 아키텍처용이다.
> **샤드 접미사도 불필요하다** — 차량이 2대(팔당 1대)이고 각자 다른 `vin` 을 쓰므로 핫 파티션이 없다.

### 3-2. PostgreSQL (MVP 5 테이블)

`campaign` · `dataset_version` · `dataset_frame` · `model_run` · `eval_result`.
DDL 은 보고서 §2-5 를 그대로 쓰되 두 가지만 조정한다:

- `deployment` 테이블은 **만들지 않는다** (OTA 가 MVP 범위 밖).
- `dataset_frame` 에 **`reclaim_mode` 를 추가**한다 — 설계결정-0001 §4 의 층화 요구.

마이그레이션은 `schema/pg/001_init.sql` 부터 번호순. **되돌리는 마이그레이션을 함께 만든다**
(`001_init.down.sql`).

---

## 4. S3 — 런 모델

```
ARM   ∈ {STA_B, DYN_B}          팔
SEED  ∈ {1, 2, 3}               시드
vin    = sim-{ARM}              팔마다 별개의 "차량" — 재생·적재 단위 (2대)
run_id = {ARM}-s{SEED}          예: DYN_B-s1 — 학습 단위 (6런)
```

**팔을 차량으로 모델링하는 것이 핵심 결정이다.** 선별이 시드 독립이라(`schema/verdict.py` `vin_of`) 같은 데이터를 시드마다 올리지 않는다. 이렇게 하면:

| 얻는 것 | 어떻게 |
|---|---|
| S3 접두사 격리 | `s3://ns-raw/vin={vin}/...` 가 자연히 갈린다 |
| DynamoDB 파티션 분산 | `vin` 이 파티션 키에 들어간다 |
| M4 IoT 정책 검증 | 토픽 정책변수 `${iot:Connection.Thing.ThingName}` 격리를 **그대로** 시험할 수 있다 |
| 대조 오염 방지 | 두 팔이 물리적으로 다른 접두사에 쌓인다 |

**두 팔에 주는 것이 완전히 같아야 한다** — 동일 레시피·동일 시드·동일 held-out.
다른 것은 선별 배열 하나뿐이다. (보고서 §6-4 수용 기준 3)

---

## 5. S4 — 로컬 스택

| 서비스 | 이미지 | 포트 | 대체 대상 |
|---|---|---|---|
| `mosquitto` | `eclipse-mosquitto:2` | 1883 | AWS IoT Core |
| `minio` | `quay.io/minio/minio` | 9000 / 9001 | S3 |
| `dynamodb-local` | `amazon/dynamodb-local` | 8000 | DynamoDB |
| `postgres` | `postgres:16.15-alpine` | 5432 | Aurora PostgreSQL |
| `ingest` | 자체 (파이썬 프로세스) | 없음 — MQTT 구독자 | Lambda |

이미지는 전부 **태그@다이제스트로 고정**한다(`infra/docker-compose.yml`). 이미지도
의존성이므로 올릴 때 승인받는다. MinIO 는 Docker Hub 저장소가 사라져 공식 미러
`quay.io` 에서 받는다.

**부트스트랩**(`infra/bootstrap/`)이 버킷·테이블·PG 스키마를 만든다. **멱등이어야 한다** —
두 번 돌려도 같은 결과.

### 5-1. 엔드포인트 추상화 — M4 를 위해 지금 정한다

모든 AWS 접근은 **어댑터 모듈 하나**를 통과한다. 로컬↔AWS 전환이 환경변수 하나여야 한다.

```
AWS_ENDPOINT_S3=http://localhost:9000      # 비우면 실제 AWS
AWS_ENDPOINT_DYNAMODB=http://localhost:8000
MQTT_HOST=localhost                         # M4 에서 IoT Core 엔드포인트로
```

> **같은 기능을 두 번 구현하지 않기 위한 장치다.** 어댑터를 건너뛰고 `boto3` 를 직접
> 부르는 코드가 하나라도 생기면 M4 에서 전부 찾아 고쳐야 한다.

### 5-2. 설정 주입

- **계약값**(`UPLINK_BPS`, `S`, `f_cand`, `rate`, `L_max`, `Q_min`)은
  `schema/edge_contract.json` 에서 **런타임에 읽는다.** 환경변수로 덮어쓰지 않는다 —
  계약이지 설정이 아니다.
- **환경 의존값**(엔드포인트·자격·경로)만 `.env`. 예시는 `.env.example` 로 커밋.
- 재생 가속 배수(`REPLAY_SPEED`)는 환경변수. 기본 `1.0`(실시간), 상한 `12.5`.

---

## 6. S5 — 검증 전략

보고서 §6-4 수용 기준 7개를 **각각 실행 가능한 검사**로 내린다.

| # | 수용 기준 | 검사 |
|---|---|---|
| 1 | 두 팔이 파이프라인 통과 | `python run.py run-all` 이 6런 완주, 종료코드 0 (명령은 M3 에서 추가) |
| 2 | **Δ mAP 재현** | `compare.py` 가 Δ 와 부트스트랩 CI 출력 → 오프라인 **+0.0241 [+0.0115, +0.0369]** 와 대조 |
| 3 | 파이프라인이 대조를 왜곡 안 함 | 6런의 학습 인자 해시가 **팔·시드를 빼고 전부 동일** |
| 4 | 계보 보존 | 단일 SQL 조인이 `eval_result → model_run → dataset_version → dataset_frame → frame_uuid` 를 반환 |
| 5 | 멱등성 | 같은 배치를 3회 발행 → DynamoDB 아이템 수 불변 |
| 6 | 스큐 처리 | 페이로드를 판정보다 **늦게 / 먼저 / 아예 안** 보내는 3 시나리오 모두 상태기계가 수용 |
| 7 | 평가셋 무오염 | `held_out(2,826) ∩ 학습셋 = ∅` 를 6런 전부에서 검사 |

> 5·6·7 은 **자동 테스트**(`tests/`)로, 1·2·3·4 는 **실행 산출물**로 증명한다.

---

## 7. 리포지토리 레이아웃

```
needslack-cloud/
├ CLAUDE.md  00_README.md
├ run.py  requirements.txt       ← 단일 진입점(make 대신) · 승인된 의존성
├ docs/
├ schema/
│   ├ edge_contract.json  gen_edge_contract.py
│   ├ arm_signals.json  gen_arm_signals.py   ← 팔별 가속기 산출 스냅샷 (rev.9 venv 로 생성)
│   ├ verdict.py                  ← 판정 레코드 정의 + frame_uuid (단일 소스)
│   ├ ddb_table.json              ← DynamoDB 테이블/GSI 정의
│   └ pg/001_init.sql  001_init.down.sql
├ edge-agent/                     ← 차량 시뮬레이터
│   ├ replay.py                   pool 전량 재생 → 판정·텔레메트리(MQTT) · 번들(S3)
│   ├ uplink.py                   토큰버킷 · store&forward · 즉시 드롭
│   ├ contract.py                 edge_contract.json 로더
│   ├ armsignals.py               arm_signals.json 로더
│   └ frames.py  npyread.py       rev.9 frames.bin · .npy 리더 (stdlib)
├ ingest/                         ← ★ 신설 디렉터리
│   ├ collector.py                MQTT 구독 → DynamoDB
│   └ reconciler.py               S3 이벤트(MinIO 버킷 알림 → MQTT) → 상태 전이
├ pipelines/
│   ├ snapshot.py                 DynamoDB → manifest.jsonl + PG
│   ├ colab_train.ipynb           재학습 (rev.9/08_이관 재사용)
│   ├ evaluate.py                 mAP + 부트스트랩 CI
│   └ compare.py                  Δ 대조표
├ infra/
│   ├ docker-compose.yml
│   ├ bootstrap/                  버킷·테이블·스키마 생성 (멱등)
│   └ aws/                        M4. IoT 정책 · 역할별칭
├ common/aws.py                   ← AWS 어댑터 (§5-1). boto3·paho 는 여기서만 부른다
└ tests/                          ← ★ 신설
```

> `ingest/` · `common/` · `tests/` 는 신설이다. README 의 디렉터리 목록도 함께 갱신한다.
> **`schema/verdict.py` 가 레코드 정의의 단일 소스**이고, 재생기·수집기·스냅샷이 전부
> 이것을 import 한다. 세 곳에 필드를 따로 적으면 반드시 갈라진다.

---

## 8. 의존성 — 확정

`CLAUDE.md §4` 에 따라 승인된 것만 쓴다. 파이썬 의존성은 `requirements.txt` 가 단일
소스이고, 이 프로젝트 전용 `.venv` 에 설치한다(rev.9 venv 와 공유하지 않는다).

| # | 항목 | 결정 | 이유 |
|---|---|---|---|
| 1 | **와이어 포맷** | **JSON + `schema_version`** (protobuf 아님) | 보고서 §1-2 가 protobuf 를 고른 근거는 **10만대 건당 과금**이다. MVP 는 2팔 판정 메시지 1,365 건(실측)이라 그 논거가 적용되지 않는다. 의존성 0 · 디버깅 쉬움 · 구버전 차량 없음. **필드 이름과 의미는 proto 와 1:1 로 정의**해 두고, 실제 IoT Core 를 쓰는 M4 에서 전환 |
| 2 | **MQTT 클라이언트** | `paho-mqtt` | 사실상 표준. Mosquitto·IoT Core 양쪽 호환 |
| 3 | **AWS SDK** | `boto3` | MinIO·DynamoDB Local 이 같은 API. `endpoint_url` 로 전환 |
| 4 | **PostgreSQL 접근** | `psycopg[binary]` **(ORM 없음)** | 테이블 5개 · 조인 몇 개다. `[binary]` — Windows 에 libpq 가 없다 |
| 5 | **테스트** | stdlib `unittest` (의존성 없음) | 테스트가 `.venv` 없이도 돌아야 계약 점검(`CLAUDE.md §0-2`)이 항상 가능하다 |
| 6 | **진입점** | `run.py` (stdlib) | Windows 에 `make` 가 없다. `.venv` 재실행 · `.env` 로드 · `PYTHONUTF8=1` 을 한곳에서 처리 |

**웹 프레임워크는 넣지 않는다.** 판정 레코드는 stdlib `dataclass`(`schema/verdict.py`)로
정의돼 있고, M1 의 수집기·조정기는 둘 다 MQTT 구독자라 HTTP 가 쓰일 곳이 없다.
**MinIO 버킷 알림은 MQTT 로 받는다 — 이 스택에서 확인했다.** 대상은 compose 의 MinIO
환경변수(대상 ID `NS` · QoS 1 · `queue_dir` 전용 볼륨)가, `ObjectCreated` 규칙은 부트스트랩이
만든다. 토픽 `TOPIC_S3_EVENTS` 와 ARN 은 `schema/verdict.py` 가 단일 소스다. 웹훅용 HTTP 는
필요 없다. 확인된 동작:

- boto3 PUT 1건 → `s3:ObjectCreated:Put` 이벤트 1건. `userMetadata` 에 번들 메타
  (`X-Amz-Meta-Vin` · `X-Amz-Meta-N-Frames` …)가 실린다.
- 본문의 `Records[0].s3.object.key` 는 **URL 인코딩돼 있다**(`vin%3Dsim-…`). 최상위 `Key` 는
  `{bucket}/{key}` 원문이다.
- **브로커가 내려가 있는 동안의 PUT 도 잃지 않는다** — `queue_dir` 에 쌓였다가 재접속 후
  전달된다. 단, 받는 쪽이 **영속 세션**(`clean_session=False` · 고정 client_id · QoS 1)이어야
  조정기가 꺼져 있던 동안의 이벤트를 브로커가 보관한다. 그래도 조정기는 이벤트만 믿지 않고
  버킷 목록 대조로 보완한다.

**나중에 필요해지면 묻는 것**: `duckdb`(M2 분석 질의 — DynamoDB 스캔으로 부족할 때).

**로컬에 `torch`/`ultralytics` 를 설치하지 않는다.** 학습·평가는 Colab 에서만 돈다.
