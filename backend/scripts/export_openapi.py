from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root / "src"))
    from enterprise_insight_backend.app import create_app
    from enterprise_insight_backend.config import Settings

    output = backend_root.parent / "contracts" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        data_dir=backend_root / ".contract-local", database_url="sqlite:///:memory:"
    )
    document = create_app(settings).openapi()
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(output)


if __name__ == "__main__":
    main()
