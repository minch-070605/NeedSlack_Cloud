-- 001_init.sql — MVP 계보 스키마 (S2)
--
-- 이 스키마가 답하려는 질문은 하나다:
--   "이 mAP 숫자가 어느 데이터에서 어느 선별 기준으로 나왔는가" — 조인 한 번으로.
--
-- 범위: 보고서 §2-5 의 목표 스키마에서 MVP 에 필요한 5 테이블만.
--   · deployment 는 만들지 않는다 (OTA 가 MVP 범위 밖 — 분석보고서 §6-6).
--   · dataset_frame 에 reclaim_mode 를 더한다 (설계결정-0001 §4 층화 요구).
--
-- 되돌리기: 001_init.down.sql

BEGIN;

-- ─────────────────────────────────────────── 큐레이션 캠페인
-- 엣지에 배포되는 need_profile 한 벌. MVP 는 rev.9 의 프로파일 하나만 등록한다.
CREATE TABLE campaign (
    campaign_id     TEXT         PRIMARY KEY,
    profile_json    JSONB        NOT NULL,
    profile_sha     CHAR(16)     NOT NULL UNIQUE,   -- DynamoDB.profile_sha 와의 조인키
    select_rate     NUMERIC(4,3) NOT NULL CHECK (select_rate > 0 AND select_rate <= 1),
    value_th        INTEGER      NOT NULL,
    parity_verified BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- 파리티 미검증 프로파일은 어떤 데이터셋도 참조할 수 없다.
    -- 코드 리뷰가 아니라 스키마가 막는다.
    CONSTRAINT campaign_deployable CHECK (parity_verified)
);

-- ─────────────────────────────────────────── 불변 데이터셋 스냅샷
CREATE TABLE dataset_version (
    ds_id           TEXT        PRIMARY KEY,       -- ds-{ARM}
    arm             TEXT        NOT NULL CHECK (arm IN ('STA_B','DYN_B')),
    campaign_id     TEXT        NOT NULL REFERENCES campaign(campaign_id),
    manifest_s3_uri TEXT        NOT NULL,
    n_frames        INTEGER     NOT NULL CHECK (n_frames > 0),
    split_seed      INTEGER     NOT NULL,          -- rev.9 config.SPLIT_SEED = 20260827
    frozen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- ★ 데이터셋은 팔당 하나다. 시드는 선별이 아니라 학습에만 작용한다 —
    --   rev.9 g=1.0 이라 선별 배열이 시드와 무관하게 동일하다(schema/verdict.py vin_of).
    --   따라서 스냅샷 2개 · 학습 런 6개 가 된다.
    UNIQUE (campaign_id, arm)
);

CREATE TABLE dataset_frame (
    ds_id        TEXT     NOT NULL REFERENCES dataset_version(ds_id) ON DELETE CASCADE,
    frame_uuid   CHAR(32) NOT NULL,                -- DynamoDB 로의 논리적 참조 (FK 없음)
    frame_id     INTEGER  NOT NULL,                -- rev.9 전역 인덱스. 재현 대조용
    split        TEXT     NOT NULL CHECK (split IN ('tune','pool','held_out')),
    -- 설계결정-0001 §4: 기전이 다른 프레임을 한 학습셋에 섞지 않기 위해 층화 키로 싣는다
    reclaim_mode TEXT     NOT NULL,
    PRIMARY KEY (ds_id, frame_uuid)
);
CREATE INDEX dataset_frame_split_idx ON dataset_frame (ds_id, split);

-- ─────────────────────────────────────────── 학습 런
-- LOCKED_TRAIN_ARGS 를 컬럼으로 고정한다. rev.9 yolo_detector.py 의
-- assert_no_autotuning() 이 강제하던 것을 DB 제약으로 한 번 더 막는다.
CREATE TABLE model_run (
    run_id      TEXT    PRIMARY KEY,               -- {ARM}-s{SEED}
    ds_id       TEXT    NOT NULL REFERENCES dataset_version(ds_id),
    arch        TEXT    NOT NULL DEFAULT 'yolov8n',
    epochs      INTEGER NOT NULL CHECK (epochs > 0),
    batch       INTEGER NOT NULL CHECK (batch > 0),
    imgsz       INTEGER NOT NULL CHECK (imgsz > 0),
    seed        INTEGER NOT NULL,
    optimizer   TEXT    NOT NULL CHECK (optimizer <> 'auto'),
    mosaic      NUMERIC NOT NULL CHECK (mosaic = 0),
    amp         BOOLEAN NOT NULL CHECK (amp = FALSE),
    -- 팔·시드를 뺀 학습 인자의 해시. 6런이 전부 같아야 한다 (수용 기준 3).
    train_args_sha CHAR(16) NOT NULL,
    weights_uri TEXT,
    trained_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 같은 데이터셋·같은 조건의 중복 실행을 막는다 (사후 시드 선택 차단)
    UNIQUE (ds_id, seed, epochs, batch, imgsz, arch)
);

-- ─────────────────────────────────────────── 평가
CREATE TABLE eval_result (
    run_id       TEXT             NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    metric       TEXT             NOT NULL,        -- 'mAP50' | 'mAP50-95'
    value        DOUBLE PRECISION NOT NULL,
    n_held_out   INTEGER          NOT NULL CHECK (n_held_out > 0),
    evaluated_at TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, metric)
);

COMMIT;

-- ─────────────────────────────────────────── 계보 역추적 (수용 기준 4)
-- 이 질의가 조인 한 번에 돌아야 한다.
--
--   SELECT e.run_id, e.value AS map50, dv.arm, m.seed,
--          c.profile_sha, df.frame_uuid, df.frame_id
--     FROM eval_result   e
--     JOIN model_run     m  ON m.run_id  = e.run_id
--     JOIN dataset_version dv ON dv.ds_id = m.ds_id
--     JOIN campaign      c  ON c.campaign_id = dv.campaign_id
--     JOIN dataset_frame df ON df.ds_id = dv.ds_id AND df.split = 'pool'
--    WHERE e.run_id = $1 AND e.metric = 'mAP50';
