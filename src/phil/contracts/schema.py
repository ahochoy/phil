import json
from pathlib import Path

from phil.contracts import ALL_CONTRACTS


def export_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for model in ALL_CONTRACTS:
        path = out_dir / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        paths.append(path)
    return paths
