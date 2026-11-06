-- 0021: 에이전트 프롬프트 버전 이력(artex 의 버전 저장/복원 참고, UPGRADE_PLAN.md F3a, 2026-11).
-- 저장·기본값 복원·버전 복원마다 그 시점 전문을 한 행 남긴다. finding_review_history 와 같은 용도의
-- 이력 테이블이지만, 여긴 추가 전용 트리거를 걸지 않는다(프롬프트 버전은 보안 판정 이력이 아니라
-- 운영 편의용 되돌리기 목록이라 그 정도 보존 강도가 필요 없음 — 과한 제약 추가 안 함).

CREATE TABLE agent_prompt_versions (
    id          bigserial PRIMARY KEY,
    name        text        NOT NULL,
    text        text        NOT NULL,
    updated_by  text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_prompt_versions_name_idx ON agent_prompt_versions (name, created_at DESC);
