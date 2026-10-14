-- 0004: 실행별 자율성 스위치. 진단 시작 전에 운영자가 정하고 이후 바꾸지 않는다(scope 와 같은 방식으로 고정).
--   approval: 기본값. read 는 허용, write/dangerous 는 매번 사람 승인이 필요하다.
--   partial : write 는 범위 내에서 자동 허용, dangerous 는 여전히 매번 승인이 필요하다.
--   full    : write/dangerous 모두 범위 내에서 자동 허용한다(범위·제외·중지 스위치 검사는 그대로 적용된다).
ALTER TABLE runs ADD COLUMN autonomy text NOT NULL DEFAULT 'approval'
    CHECK (autonomy IN ('approval', 'partial', 'full'));
