"""테스트 도우미: 프로젝트 설정/체크리스트/예시 대상의 격리된 복사본."""
import json
import shutil
import tempfile
from pathlib import Path

from audit import config
from audit.adapters import build_registry
from audit.checklist_engine import Checklists
from audit.evidence import EvidenceStore
from audit.orchestrator import Orchestrator
from audit.planner import validate_plan
from audit.policy import Policy
from audit.storage import Storage

ROOT = Path(__file__).resolve().parents[1]

PLAN = {"plan_id": "test-plan", "checklist_id": "sample-local-config",
        "checklist_version": "0.1.0", "targets": ["sample-app"], "use_llm": False}


class Home:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        for d in ("config", "examples"):
            shutil.copytree(ROOT / d, self.path / d)
        # 체크리스트는 테스트 전용 fixture 를 사용한다(실제 checklist/ 와 완전히 분리)
        shutil.copytree(ROOT / "tests" / "fixtures" / "checklist", self.path / "checklist")
        self.settings = config.load_settings(self.path)
        self.storage = Storage(self.settings.path("db"))
        self.policy = Policy(config.load_scope(self.path), self.path)
        self.checklists = Checklists(self.settings.path("checklist_dir"))
        self.registry = build_registry(self.settings, {})
        self.evidence = EvidenceStore(self.storage, self.settings.path("evidence_dir"))

    def resolve(self, plan=None):
        return validate_plan(plan or PLAN, self.policy, self.checklists, self.registry)

    def orchestrator(self, llm=None, approver=None):
        return Orchestrator(self.storage, self.evidence, self.policy, self.registry,
                            self.settings, llm=llm, approver=approver)

    def close(self):
        self.storage.close()
        self._tmp.cleanup()
