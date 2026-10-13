-- 0001: 진단 워크벤치 초기 스키마. 시간은 모두 UTC(timestamptz). 파괴적 변경 없음.

-- ---------------------------------------------------------------- 체크리스트(버전 고정)
CREATE TABLE checklist_versions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    checklist_id  text NOT NULL,
    version       text NOT NULL,
    guide_name    text NOT NULL,
    guide_version text NOT NULL,
    official      boolean NOT NULL,
    notice        text NOT NULL DEFAULT '',
    sha256        text NOT NULL,              -- 가져온 JSON 원본의 해시(활성화 시 고정된 값과 동일)
    sources       jsonb NOT NULL DEFAULT '[]', -- 원본 xlsx/pdf 이름과 해시
    review_status text NOT NULL DEFAULT 'active',
    imported_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (checklist_id, version, sha256)
);

CREATE TABLE checklist_items (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    checklist_version_id uuid NOT NULL REFERENCES checklist_versions(id),
    check_id             text NOT NULL,       -- 원본 항목 ID
    title                text NOT NULL,
    severity             text NOT NULL,
    automation_level     text NOT NULL CHECK (automation_level IN
                           ('AUTOMATABLE', 'PARTIALLY_AUTOMATABLE', 'MANUAL_REVIEW')),
    body                 jsonb NOT NULL,      -- 항목 원문 전체(판정 기준, 증적 요구사항, source_data)
    UNIQUE (checklist_version_id, check_id)
);

-- ---------------------------------------------------------------- 실행
CREATE TABLE runs (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id        text NOT NULL,
    operator             text NOT NULL,
    checklist_version_id uuid NOT NULL REFERENCES checklist_versions(id),
    scope                jsonb NOT NULL,      -- 실행 생성 시점의 scope.json 사본
    budget               jsonb NOT NULL,      -- max_tasks, max_tool_calls, timeout_s, max_explorations
    use_llm              boolean NOT NULL DEFAULT false,
    status               text NOT NULL DEFAULT 'CREATED' CHECK (status IN
                           ('CREATED', 'RUNNING', 'PAUSED', 'COMPLETED', 'CANCELLED', 'FAILED')),
    stop_reason          text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    ended_at             timestamptz
);

-- ---------------------------------------------------------------- 자산 그래프(전역 공유)
CREATE TABLE assets (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind              text NOT NULL,          -- path | host | url | service | endpoint ...
    value             text NOT NULL,
    platform          text,
    source            text NOT NULL,          -- scope | tool:<name> | operator
    verification      text NOT NULL DEFAULT 'UNVERIFIED' CHECK (verification IN
                        ('SCOPE_DECLARED', 'OBSERVED', 'UNVERIFIED')),
    first_seen_run_id uuid REFERENCES runs(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, value)
);

CREATE TABLE asset_edges (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    src_id     uuid NOT NULL REFERENCES assets(id),
    dst_id     uuid NOT NULL REFERENCES assets(id),
    rel        text NOT NULL,
    run_id     uuid REFERENCES runs(id),
    source     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (src_id, dst_id, rel)
);

CREATE TABLE run_assets (                     -- 실행이 진단하는 대상(scope.json 의 target_id 와 연결)
    run_id    uuid NOT NULL REFERENCES runs(id),
    asset_id  uuid NOT NULL REFERENCES assets(id),
    target_id text NOT NULL,
    PRIMARY KEY (run_id, asset_id),
    UNIQUE (run_id, target_id)
);

CREATE TABLE run_checklist_items (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            uuid NOT NULL REFERENCES runs(id),
    checklist_item_id uuid NOT NULL REFERENCES checklist_items(id),
    asset_id          uuid NOT NULL REFERENCES assets(id),
    verdict           text NOT NULL DEFAULT 'NOT_RUN' CHECK (verdict IN
                        ('PASS', 'FAIL', 'NOT_APPLICABLE', 'NEEDS_REVIEW', 'INCONCLUSIVE', 'NOT_RUN')),
    decided_by        text CHECK (decided_by IN ('rule', 'llm_suggestion', 'reviewer')),
    reason            text NOT NULL DEFAULT '',
    rule_outcome      jsonb,
    llm_outcome       jsonb,                  -- 제안일 뿐 판정이 아니다
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, checklist_item_id, asset_id)
);
CREATE INDEX ON run_checklist_items (run_id, verdict);

-- ---------------------------------------------------------------- 탐색 그래프(실행별)
CREATE TABLE exploration_nodes (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id     uuid NOT NULL REFERENCES runs(id),
    kind       text NOT NULL CHECK (kind IN ('goal', 'intent', 'fact', 'question', 'finding')),
    state      text NOT NULL DEFAULT 'OPEN',
    origin     text NOT NULL,                 -- planner | llm | worker | operator
    payload    jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON exploration_nodes (run_id, kind);

CREATE TABLE exploration_edges (
    src_id uuid NOT NULL REFERENCES exploration_nodes(id),
    dst_id uuid NOT NULL REFERENCES exploration_nodes(id),
    rel    text NOT NULL CHECK (rel IN
             ('spawns', 'yields', 'derived_from', 'supports', 'proves', 'related_to')),
    PRIMARY KEY (src_id, dst_id, rel)
);

-- ---------------------------------------------------------------- 공유 TodoList(작업 큐)
CREATE TABLE backlog_items (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                uuid NOT NULL REFERENCES runs(id),
    kind                  text NOT NULL CHECK (kind IN ('checklist', 'exploration')),
    run_checklist_item_id uuid REFERENCES run_checklist_items(id),
    exploration_node_id   uuid REFERENCES exploration_nodes(id),
    asset_id              uuid NOT NULL REFERENCES assets(id),
    priority              integer NOT NULL DEFAULT 100,   -- 작을수록 먼저
    status                text NOT NULL DEFAULT 'PENDING' CHECK (status IN
                            ('PENDING', 'READY', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED',
                             'NEEDS_REVIEW', 'CANCELLED')),
    idempotency_key       text NOT NULL,      -- 같은 입력에서 같은 작업이 다시 만들어지지 않게 한다
    reason                text NOT NULL,      -- 생성 이유
    payload               jsonb NOT NULL DEFAULT '{}',
    policy_class          text,               -- ALLOW | ALLOW_WITH_APPROVAL | DENY | NEEDS_SCOPE_CONFIRMATION
    blocked_reason        text,
    attempts              integer NOT NULL DEFAULT 0,
    max_attempts          integer NOT NULL DEFAULT 2,
    lease_owner           text,
    lease_until           timestamptz,
    last_error            text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, idempotency_key),
    CHECK ((kind = 'checklist') = (run_checklist_item_id IS NOT NULL)),
    CHECK ((kind = 'exploration') = (exploration_node_id IS NOT NULL))
);
CREATE INDEX ON backlog_items (run_id, status, priority);

CREATE TABLE backlog_deps (
    item_id       uuid NOT NULL REFERENCES backlog_items(id),
    depends_on_id uuid NOT NULL REFERENCES backlog_items(id),
    PRIMARY KEY (item_id, depends_on_id),
    CHECK (item_id <> depends_on_id)
);

CREATE TABLE worker_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES runs(id),
    backlog_item_id uuid NOT NULL REFERENCES backlog_items(id),
    worker          text NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    outcome         text,
    error           text
);
CREATE INDEX ON worker_runs (backlog_item_id);

-- ---------------------------------------------------------------- 증적 / 도구 호출(Activity)
CREATE TABLE evidence (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                uuid NOT NULL REFERENCES runs(id),
    asset_id              uuid NOT NULL REFERENCES assets(id),
    run_checklist_item_id uuid REFERENCES run_checklist_items(id),
    backlog_item_id       uuid REFERENCES backlog_items(id),
    requirement_id        text NOT NULL,
    media_type            text NOT NULL DEFAULT 'application/octet-stream',
    tool                  text NOT NULL,
    tool_version          text NOT NULL DEFAULT '',
    args                  jsonb NOT NULL,     -- 마스킹된 인자
    collected_at          timestamptz NOT NULL DEFAULT now(),
    rel_path              text,               -- evidence_dir 기준 상대 경로
    sha256                text,
    size                  bigint,
    truncated             boolean NOT NULL DEFAULT false,
    success               boolean NOT NULL,
    error                 text,
    CHECK (success = (rel_path IS NOT NULL AND sha256 IS NOT NULL))
);
CREATE INDEX ON evidence (run_id);
CREATE INDEX ON evidence (sha256);

-- 도구 호출 한 건 = Worker Activity 한 단계. 정책에서 거부된 호출도 기록한다.
CREATE TABLE tool_invocations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES runs(id),
    worker_run_id   uuid REFERENCES worker_runs(id),
    backlog_item_id uuid REFERENCES backlog_items(id),
    asset_id        uuid REFERENCES assets(id),
    step_no         integer NOT NULL DEFAULT 1,
    tool            text NOT NULL,
    tool_version    text NOT NULL DEFAULT '',
    args            jsonb NOT NULL,           -- 마스킹된 인자
    policy_decision text NOT NULL CHECK (policy_decision IN
                      ('ALLOW', 'ALLOW_WITH_APPROVAL', 'DENY', 'NEEDS_SCOPE_CONFIRMATION')),
    policy_reason   text NOT NULL DEFAULT '',
    status          text NOT NULL CHECK (status IN ('SKIPPED', 'SUCCEEDED', 'FAILED')),
    error           text,
    evidence_id     uuid REFERENCES evidence(id),
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz
);
CREATE INDEX ON tool_invocations (run_id, tool);
CREATE INDEX ON tool_invocations (backlog_item_id, step_no);

-- ---------------------------------------------------------------- 발견 사항 / 판정 이력
CREATE TABLE findings (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                uuid NOT NULL REFERENCES runs(id),
    asset_id              uuid NOT NULL REFERENCES assets(id),
    run_checklist_item_id uuid REFERENCES run_checklist_items(id),
    title                 text NOT NULL,
    severity              text NOT NULL,
    status                text NOT NULL DEFAULT 'OPEN',
    rationale             text NOT NULL,
    remediation           text NOT NULL DEFAULT '',
    verification          text NOT NULL CHECK (verification IN ('RULE_CONFIRMED', 'REVIEWER_CONFIRMED')),
    duplicate_of          uuid REFERENCES findings(id),
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, run_checklist_item_id)
);

CREATE TABLE finding_evidence (
    finding_id  uuid NOT NULL REFERENCES findings(id),
    evidence_id uuid NOT NULL REFERENCES evidence(id),
    PRIMARY KEY (finding_id, evidence_id)
);

CREATE TABLE verdict_history (
    id                    bigserial PRIMARY KEY,
    run_checklist_item_id uuid NOT NULL REFERENCES run_checklist_items(id),
    old_verdict           text,
    new_verdict           text NOT NULL,
    reason                text NOT NULL,
    actor                 text NOT NULL,
    evidence_ids          uuid[] NOT NULL DEFAULT '{}',
    changed_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE exploration_anchors (            -- 탐색 노드 -> 실제 존재하는 레코드
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id           uuid NOT NULL REFERENCES exploration_nodes(id),
    asset_id          uuid REFERENCES assets(id),
    checklist_item_id uuid REFERENCES checklist_items(id),
    evidence_id       uuid REFERENCES evidence(id),
    finding_id        uuid REFERENCES findings(id),
    CHECK (num_nonnulls(asset_id, checklist_item_id, evidence_id, finding_id) = 1)
);
CREATE INDEX ON exploration_anchors (node_id);

-- ---------------------------------------------------------------- 승인 / 감사 / 변경 이벤트 / 모델 호출
CREATE TABLE approvals (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      uuid NOT NULL REFERENCES runs(id),
    asset_id    uuid NOT NULL REFERENCES assets(id),
    tool        text NOT NULL,
    args_sha256 text NOT NULL,                -- 정규화된 인자의 해시: 승인은 정확한 인자에 묶인다
    approver    text NOT NULL,
    decision    text NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
    decided_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX ON approvals (run_id, tool, args_sha256);

CREATE TABLE audit_events (
    id      bigserial PRIMARY KEY,
    run_id  uuid REFERENCES runs(id),
    ts      timestamptz NOT NULL DEFAULT now(),
    event   text NOT NULL,
    details jsonb NOT NULL
);
CREATE INDEX ON audit_events (run_id, id);

-- 변경 이벤트는 Planner 깨우기 신호일 뿐 작업 데이터가 아니다. 유실돼도 큐/판정 테이블에서 복구된다.
CREATE TABLE change_events (
    id           bigserial PRIMARY KEY,
    run_id       uuid NOT NULL REFERENCES runs(id),
    kind         text NOT NULL,
    ref_id       text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX ON change_events (run_id) WHERE processed_at IS NULL;

CREATE TABLE model_calls (
    id                bigserial PRIMARY KEY,
    run_id            uuid REFERENCES runs(id),
    ts                timestamptz NOT NULL DEFAULT now(),
    model             text NOT NULL,
    purpose           text NOT NULL,
    prompt_tokens     integer,
    completion_tokens integer,
    token_source      text NOT NULL CHECK (token_source IN ('reported', 'estimated', 'unavailable')),
    duration_ms       integer,
    ok                boolean NOT NULL,
    error             text
);

-- ---------------------------------------------------------------- 추가 전용 강제
CREATE FUNCTION forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;
CREATE TRIGGER evidence_append_only BEFORE UPDATE OR DELETE ON evidence
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER verdict_history_append_only BEFORE UPDATE OR DELETE ON verdict_history
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
