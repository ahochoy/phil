import json
from pathlib import Path

from pydantic import BaseModel

from phil.contracts import Plan


def artifact_name(node: str, task_id: str | None, attempt: int) -> str:
    return f"{node}-{task_id or 'run'}-{attempt}"


class ArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def _file(self, relative: str) -> Path:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_plan(self, plan: Plan) -> Path:
        path = self._file("plan.json")
        path.write_text(plan.model_dump_json(indent=2))
        return path

    def read_plan(self) -> Plan:
        return Plan.model_validate_json((self.run_dir / "plan.json").read_text())

    def write(self, subdir: str, name: str, contract: BaseModel) -> Path:
        path = self._file(f"{subdir}/{name}.json")
        path.write_text(contract.model_dump_json(indent=2))
        return path

    def read[T: BaseModel](self, path: Path, model: type[T]) -> T:
        return model.model_validate_json(path.read_text())

    def append_assumptions(self, *, node: str, task_id: str | None, assumptions: list[str]) -> None:
        path = self._file("assumptions.jsonl")
        with path.open("a") as handle:
            for assumption in assumptions:
                entry = {"node": node, "task_id": task_id, "assumption": assumption, "status": "open"}
                handle.write(json.dumps(entry) + "\n")

    def read_assumptions(self) -> list[dict]:
        path = self.run_dir / "assumptions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def write_log(self, name: str, text: str) -> Path:
        path = self._file(f"logs/{name}.log")
        path.write_text(text)
        return path
