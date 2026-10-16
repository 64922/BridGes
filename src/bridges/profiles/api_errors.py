"""画像 API 的错误到 HTTP 响应映射（四维、原子与进行中事项路由共用）。"""

from __future__ import annotations

from fastapi import HTTPException, status

from bridges.contracts.profiles import ProfileError
from bridges.profiles.atomic import AtomicProfileError


def profile_error(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=ProfileError(error=error, message=message).model_dump(),
    )


def atomic_error(exc: AtomicProfileError, failure_code: str) -> HTTPException:
    message = str(exc)
    if "对象不存在" in message or "访问权限" in message:
        return profile_error(
            status.HTTP_404_NOT_FOUND, "atomic_profile_not_found", message
        )
    if "版本冲突" in message or "已删除" in message or "已存在内容相同" in message:
        return profile_error(
            status.HTTP_409_CONFLICT, "atomic_profile_conflict", message
        )
    return profile_error(
        status.HTTP_422_UNPROCESSABLE_CONTENT, failure_code, message
    )


__all__ = ["atomic_error", "profile_error"]
