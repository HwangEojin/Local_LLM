-- 0027: run_triggers 가 고정 tool+args 대신 커스텀 에이전트(0026)에게 선택을 맡길 수 있게 한다.
-- custom_agent_id 가 있으면 발동 시 tool/args 는 비워 두고(triggers.py::_agent_decide 가 그 자리를 메움),
-- 없으면 지금처럼 결정론적으로 그대로 쓴다.
ALTER TABLE run_triggers ALTER COLUMN tool DROP NOT NULL;
ALTER TABLE run_triggers ADD COLUMN custom_agent_id bigint REFERENCES custom_agents(id);
ALTER TABLE run_triggers ADD CONSTRAINT run_triggers_tool_or_agent
    CHECK ((tool IS NOT NULL) OR (custom_agent_id IS NOT NULL));
