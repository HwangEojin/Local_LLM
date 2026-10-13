-- 0003: 파생 증적(예: MCP 응답에서 꺼낸 스크린샷 이미지)을 원본 증적과 연결한다.
ALTER TABLE evidence ADD COLUMN derived_from uuid REFERENCES evidence(id);
