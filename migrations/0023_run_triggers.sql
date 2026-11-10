-- 0023: Triggers(F3b-1, UPGRADE_PLAN.md). LLM 을 부르지 않는 결정론적 규칙 — enrich.py 와 같은 패턴으로
-- planner.plan() 안에서만 평가되고, 만든 작업은 기존 backlog_items/policy.decide() 경로를 그대로 거친다.
-- 별도 스케줄러가 없으므로 Kill Switch·시간예산은 run_loop 가 planner.plan() 을 부르는 지점에서 그대로 걸린다.
CREATE TABLE run_triggers (
    id              bigserial PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name            text NOT NULL,
    condition_kind  text NOT NULL CHECK (condition_kind IN
                     ('interval', 'on_finding', 'on_goal_met', 'on_task_timeout', 'on_tool_call', 'on_task_create')),
    condition_args  jsonb NOT NULL DEFAULT '{}',
    tool            text NOT NULL,
    args            jsonb NOT NULL DEFAULT '{}',
    target_asset_id uuid REFERENCES assets(id),
    enabled         boolean NOT NULL DEFAULT true,
    last_checked_at timestamptz NOT NULL DEFAULT now(),
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX run_triggers_run_id_idx ON run_triggers (run_id);
