from __future__ import annotations

import re
from time import perf_counter
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_insight_backend.config import Settings
from enterprise_insight_backend.errors import DomainError
from enterprise_insight_backend.models import ModelProfileRow
from enterprise_insight_backend.schemas import (
    ModelProfileCreate,
    ModelProfileTestView,
    ModelProfileUpdate,
    ModelProfileView,
    ModelProvider,
)
from enterprise_insight_backend.service_utils import now_utc


class LocalSecretVault:
    def __init__(self, settings: Settings) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        key_path = settings.data_dir / "model-profile.key"
        if key_path.exists():
            key = key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            key_path.write_bytes(key)
        self.fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise DomainError(
                "MODEL_SECRET_UNREADABLE",
                "本机模型密钥无法读取，请重新填写。",
                status_code=409,
            ) from exc


class ModelProfileService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.vault = LocalSecretVault(settings)

    def list(self) -> list[ModelProfileView]:
        rows = self.session.scalars(select(ModelProfileRow).order_by(ModelProfileRow.name)).all()
        return [self._view(row) for row in rows]

    def create(self, payload: ModelProfileCreate) -> ModelProfileView:
        if payload.provider != ModelProvider.MOCK and not payload.api_key:
            raise DomainError(
                "MODEL_API_KEY_REQUIRED",
                "非模拟模型必须提供API密钥。",
                status_code=422,
            )
        first_profile = self.session.scalar(select(ModelProfileRow.id).limit(1)) is None
        row = ModelProfileRow(
            name=payload.name,
            provider=payload.provider.value,
            base_url=payload.base_url.rstrip("/"),
            model=payload.model,
            encrypted_api_key=self.vault.encrypt(payload.api_key) if payload.api_key else None,
            temperature=payload.temperature,
            timeout_seconds=payload.timeout_seconds,
            is_default=payload.is_default or first_profile,
        )
        if row.is_default:
            self._clear_default()
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "MODEL_PROFILE_NAME_CONFLICT",
                "已经存在同名模型配置。",
                status_code=409,
            ) from exc
        return self._view(row)

    def update(self, profile_id: UUID, payload: ModelProfileUpdate) -> ModelProfileView:
        row = self.require(profile_id)
        values = payload.model_dump(exclude_unset=True)
        api_key = values.pop("api_key", None)
        make_default = values.pop("is_default", None)
        for key, value in values.items():
            setattr(row, key, value.rstrip("/") if key == "base_url" else value)
        if api_key:
            row.encrypted_api_key = self.vault.encrypt(api_key)
        if make_default:
            self._clear_default(except_id=row.id)
            row.is_default = True
        elif make_default is False and row.is_default:
            raise DomainError(
                "MODEL_DEFAULT_REQUIRED",
                "请先把另一个模型设为默认，再取消当前默认模型。",
                status_code=409,
            )
        if row.enabled is False and row.is_default:
            raise DomainError(
                "MODEL_DEFAULT_CANNOT_DISABLE",
                "默认模型不能直接禁用，请先选择另一个默认模型。",
                status_code=409,
            )
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise DomainError(
                "MODEL_PROFILE_NAME_CONFLICT",
                "已经存在同名模型配置。",
                status_code=409,
            ) from exc
        return self._view(row)

    def delete(self, profile_id: UUID) -> None:
        row = self.require(profile_id)
        was_default = row.is_default
        self.session.delete(row)
        self.session.flush()
        if was_default:
            replacement = self.session.scalar(
                select(ModelProfileRow)
                .where(ModelProfileRow.enabled.is_(True))
                .order_by(ModelProfileRow.created_at, ModelProfileRow.id)
                .limit(1)
            )
            if replacement is not None:
                replacement.is_default = True
                self.session.flush()

    def test(self, profile_id: UUID) -> ModelProfileTestView:
        row = self.require(profile_id)
        if row.provider == ModelProvider.MOCK.value:
            return ModelProfileTestView(
                profile_id=UUID(row.id),
                ok=True,
                message="模拟模型可用。",
                model=row.model,
                latency_ms=0,
                checked_at=now_utc(),
            )
        if not row.encrypted_api_key:
            raise DomainError("MODEL_API_KEY_REQUIRED", "模型配置没有API密钥。", status_code=422)
        key = self.vault.decrypt(row.encrypted_api_key)
        started = perf_counter()
        try:
            response = httpx.post(
                f"{row.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": row.model,
                    "messages": [{"role": "user", "content": "仅回复 OK"}],
                    "temperature": 0,
                    # Reasoning-capable models may consume a few tokens before
                    # producing the final content.  Eight tokens can therefore
                    # return HTTP 200 with an empty answer and falsely report
                    # an incompatible response format.
                    "max_tokens": 256,
                },
                timeout=min(row.timeout_seconds, 30),
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model response")
        except httpx.HTTPStatusError as exc:
            latency = int((perf_counter() - started) * 1000)
            detail = ""
            try:
                body = exc.response.json()
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, dict):
                    detail = str(error.get("message") or error.get("type") or "")
                elif isinstance(error, str):
                    detail = error
                detail = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", detail).strip()
            except (TypeError, ValueError):
                detail = ""
            message = f"连接失败：HTTP {exc.response.status_code}"
            if detail:
                message = f"{message}（{detail[:160]}）"
            return ModelProfileTestView(
                profile_id=UUID(row.id),
                ok=False,
                message=message,
                model=row.model,
                latency_ms=latency,
                checked_at=now_utc(),
            )
        except httpx.HTTPError as exc:
            latency = int((perf_counter() - started) * 1000)
            return ModelProfileTestView(
                profile_id=UUID(row.id),
                ok=False,
                message=f"连接失败：{type(exc).__name__}",
                model=row.model,
                latency_ms=latency,
                checked_at=now_utc(),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            latency = int((perf_counter() - started) * 1000)
            return ModelProfileTestView(
                profile_id=UUID(row.id),
                ok=False,
                message=f"模型返回格式不兼容：{type(exc).__name__}",
                model=row.model,
                latency_ms=latency,
                checked_at=now_utc(),
            )
        latency = int((perf_counter() - started) * 1000)
        return ModelProfileTestView(
            profile_id=UUID(row.id),
            ok=True,
            message="模型连接成功。",
            model=row.model,
            latency_ms=latency,
            checked_at=now_utc(),
        )

    def require(self, profile_id: UUID) -> ModelProfileRow:
        row = self.session.get(ModelProfileRow, str(profile_id))
        if row is None:
            raise DomainError("MODEL_PROFILE_NOT_FOUND", "模型配置不存在。", status_code=404)
        return row

    def _clear_default(self, except_id: str | None = None) -> None:
        rows = self.session.scalars(
            select(ModelProfileRow).where(ModelProfileRow.is_default.is_(True))
        ).all()
        for row in rows:
            if row.id != except_id:
                row.is_default = False

    @staticmethod
    def _view(row: ModelProfileRow) -> ModelProfileView:
        return ModelProfileView(
            id=UUID(row.id),
            name=row.name,
            provider=ModelProvider(row.provider),
            base_url=row.base_url,
            model=row.model,
            has_api_key=bool(row.encrypted_api_key),
            temperature=row.temperature,
            timeout_seconds=row.timeout_seconds,
            enabled=row.enabled,
            is_default=row.is_default,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
