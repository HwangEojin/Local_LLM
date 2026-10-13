-- 0002: 실행별 허용 도구 목록. NULL 이면 등록된 도구 전체(정책 검사는 그대로 적용).
ALTER TABLE runs ADD COLUMN allowed_tools text[];
