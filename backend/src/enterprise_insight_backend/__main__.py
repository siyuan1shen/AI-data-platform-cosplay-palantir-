from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "enterprise_insight_backend.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8012,
        reload=False,
    )


if __name__ == "__main__":
    main()
