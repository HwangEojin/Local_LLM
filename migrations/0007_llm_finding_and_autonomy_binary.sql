-- 0007: CLAUDE.md §1/§7/§9 정책에 필요한 스키마 변경 두 가지.
--
-- (1) LLM이 자신이 인용한 실존 증거를 근거로 finding을 직접 CONFIRMED로 기록할 수 있게 verification 값을
--     추가한다('LLM_CONFIRMED'). 기존 RULE_CONFIRMED/REVIEWER_CONFIRMED 행은 그대로 둔다.
ALTER TABLE findings DROP CONSTRAINT findings_verification_check;
ALTER TABLE findings ADD CONSTRAINT findings_verification_check CHECK (
    verification IN ('RULE_CONFIRMED', 'REVIEWER_CONFIRMED', 'LLM_CONFIRMED'));

-- (2) 자율성 레벨을 approval/partial/full 3단계에서 approval/full 이분법으로 단순화한다("승인 필요"
--     아니면 "즉시 실행"뿐인 이분법). 기존 'partial' 실행은 더 안전한 쪽인 'approval'로
--     백필한다 — 애매할 때 기본 거부(CLAUDE.md §8-4)라는 원칙은 이 백필에도 그대로 적용한다.
UPDATE runs SET autonomy = 'approval' WHERE autonomy = 'partial';
ALTER TABLE runs DROP CONSTRAINT runs_autonomy_check;
ALTER TABLE runs ADD CONSTRAINT runs_autonomy_check CHECK (autonomy IN ('approval', 'full'));
