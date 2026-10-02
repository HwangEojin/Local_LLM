"""체크리스트 로딩, 검증, 버전 관리. 데이터는 checklist/ 에, 로직은 이 모듈에 둔다."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import schemas
from .config import load_json
from .models import ConfigError, Target, now_iso


def validate_content(data) -> list[str]:
    try:
        errs = schemas.validate(data, schemas.CHECKLIST)
    except schemas.SchemaError as e:
        return [str(e)]
    if errs:
        return errs
    if not data["official"] and not data["notice"].strip():
        errs.append("비공식 체크리스트는 그 사실을 알리는 notice 가 필요함")
    seen = set()
    for it in data["items"]:
        cid = it["check_id"]
        if cid in seen:
            errs.append(f"{cid}: check_id 중복")
        seen.add(cid)
        req_ids = [r["id"] for r in it["evidence_requirements"]]
        if len(set(req_ids)) != len(req_ids):
            errs.append(f"{cid}: 증거 요구사항 id 중복")
        vm = it["verification_method"]
        if vm["type"] == "rule":
            rule = vm.get("rule")
            if not rule:
                errs.append(f"{cid}: verification_method 'rule' 에는 rule 이 필요함")
            else:
                if rule["evidence"] not in req_ids:
                    errs.append(f"{cid}: 규칙이 알 수 없는 증거 '{rule['evidence']}' 를 참조함")
                try:
                    re.compile(rule["pattern"])
                except re.error as e:
                    errs.append(f"{cid}: 잘못된 정규식: {e}")
        elif "rule" in vm:
            errs.append(f"{cid}: rule 이 있으나 방법은 {vm['type']} 임")
        if it["item_status"] == "complete":
            for f in ("source_reference", "pass_criteria", "fail_criteria"):
                if not it[f].strip():
                    errs.append(f"{cid}: complete 항목의 '{f}' 가 비어 있음 (incomplete 로 표시할 것)")
            if vm["type"] != "manual" and not req_ids:
                errs.append(f"{cid}: 자동 판정 항목에는 evidence_requirements 가 필요함")
    return errs


def is_applicable(item: dict, target: Target) -> bool:
    return target.type in item["target_types"]


def platform_match(item: dict, target: Target) -> bool:
    """platforms 가 없는 항목은 모든 대상에, 있으면 대상의 platform 이 목록에 있을 때만 적용한다."""
    return not item.get("platforms") or target.platform in item["platforms"]


def render_args(args: dict, target: Target) -> dict:
    return {k: v.replace("{target}", target.value) if isinstance(v, str) else v for k, v in args.items()}


class Checklists:
    def __init__(self, checklist_dir: Path):
        self.dir = Path(checklist_dir)
        self.manifest_path = self.dir / "manifest.json"
        # manifest 는 가져오기/활성화로 생성되는 상태 파일이다. 없으면 설치된 체크리스트가 없는 것으로 본다.
        self.manifest = (load_json(self.manifest_path, schemas.MANIFEST) if self.manifest_path.exists()
                         else {"checklists": []})

    def entries(self):
        for cl in self.manifest["checklists"]:
            for v in cl["versions"]:
                yield cl["checklist_id"], v

    def _entry(self, checklist_id, version):
        for cid, v in self.entries():
            if cid == checklist_id and v["version"] == version:
                return v
        raise ConfigError(f"체크리스트 {checklist_id}@{version} 이(가) manifest 에 없음")

    def _read(self, entry) -> tuple[dict, str, bytes]:
        raw = (self.dir / entry["path"]).read_bytes()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"{entry['path']}: 잘못된 JSON: {e}") from None
        return data, hashlib.sha256(raw).hexdigest(), raw

    def check_version(self, checklist_id, version) -> list[str]:
        entry = self._entry(checklist_id, version)
        try:
            data, sha, _ = self._read(entry)
        except (OSError, ConfigError) as e:
            return [str(e)]
        errs = validate_content(data)
        if not errs and data["checklist_id"] != checklist_id:
            errs.append(f"파일의 checklist_id {data['checklist_id']} 가 manifest 의 {checklist_id} 와 다름")
        if entry["status"] in ("active", "superseded"):
            if entry.get("sha256") != sha:
                errs.append("내용의 SHA-256 이 manifest 와 다름 (활성화 후 수정됨)")
        return errs

    def load(self, checklist_id, version, for_run=True) -> tuple[dict, str]:
        """검증된 내용과 sha256을 반환한다. 실행은 활성 상태이고 해시가 고정된 버전만 사용한다."""
        entry = self._entry(checklist_id, version)
        if for_run and entry["status"] != "active":
            raise ConfigError(f"체크리스트 {checklist_id}@{version} 의 상태는 '{entry['status']}' 이며 active 가 아님")
        errs = self.check_version(checklist_id, version)
        if errs:
            raise ConfigError(f"체크리스트 {checklist_id}@{version} 이(가) 유효하지 않음:\n  " + "\n  ".join(errs))
        data, sha, _ = self._read(entry)
        return data, sha

    def activate(self, checklist_id, version) -> str:
        entry = self._entry(checklist_id, version)
        data, sha, _ = self._read(entry)
        errs = validate_content(data)
        if errs:
            raise ConfigError("유효하지 않은 체크리스트는 활성화할 수 없음:\n  " + "\n  ".join(errs))
        for cid, v in self.entries():
            if cid == checklist_id and v["status"] == "active" and v is not entry:
                v["status"] = "superseded"  # 이전 버전과 그 결과는 보존한다
        entry.update(status="active", sha256=sha, activated_at=now_iso())
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.manifest_path)
        return sha
