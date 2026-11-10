-- 0024: run_triggers.target_asset_id 를 필수로 만든다. backlog_items.asset_id 가 NOT NULL 이라, 트리거가
-- 만드는 작업도 다른 모든 작업처럼 대상 하나에 묶여야 한다("전역" 트리거는 이 스키마에서 표현할 수 없음 —
-- 웹 API 가 처음부터 필수로 받게 고쳤고, 이 제약은 그 보장을 DB 레벨에서도 건다).
ALTER TABLE run_triggers ALTER COLUMN target_asset_id SET NOT NULL;
