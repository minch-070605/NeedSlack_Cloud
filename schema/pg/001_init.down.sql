-- 001_init.down.sql — 001_init.sql 되돌리기
--
-- docs/작업규칙.md §6: 마이그레이션은 되돌리는 마이그레이션을 함께 만든다.
-- 의존 역순으로 지운다.

BEGIN;

DROP TABLE IF EXISTS eval_result;
DROP TABLE IF EXISTS model_run;
DROP TABLE IF EXISTS dataset_frame;
DROP TABLE IF EXISTS dataset_version;
DROP TABLE IF EXISTS campaign;

COMMIT;
