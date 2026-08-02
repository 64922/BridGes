"""Module-interface tests for the identity service.

The seam under test: an account can be registered with a username plus a strict
QQ mailbox, authenticated by either identifier, logged out, and recovered;
usernames stay unique after case-insensitive normalization and remain mutable;
invalid attempts receive uniform, non-leaking errors; recovery revokes prior
sessions.
"""

import pytest
from pydantic import SecretStr, ValidationError

from bridges.contracts.identity import (
    AccountRegistration,
    AuthMethod,
    LoginCredential,
    RecoveryRequest,
    RecoveryReset,
)
from bridges.identity import IdentityError, IdentityService


@pytest.fixture
def service() -> IdentityService:
    return IdentityService()


def _registration(
    username: str = "桥桥",
    qq_email: str = "123456@qq.com",
    password: str = "correct-horse-12",
) -> AccountRegistration:
    return AccountRegistration(
        username=username,
        qq_email=qq_email,
        password=SecretStr(password),
    )


def _credentials(
    identifier: str = "桥桥", password: str = "correct-horse-12"
) -> LoginCredential:
    return LoginCredential(identifier=identifier, password=SecretStr(password))


def test_register_creates_account_and_session(service: IdentityService) -> None:
    result = service.register(_registration(username="BridgeUser"))

    assert result.account.username == "BridgeUser"
    assert result.account.qq_email == "123456@qq.com"
    assert result.session.account_id == result.account.id
    assert result.session.revoked_at is None
    assert result.session_token
    assert result.session.id


def test_register_rejects_short_password(service: IdentityService) -> None:
    # The contract rejects passwords shorter than 12 characters before the service
    # even sees them, providing a clear 422 to API consumers.
    with pytest.raises(ValidationError, match="too_short"):
        AccountRegistration(
            username="桥桥", qq_email="123456@qq.com", password=SecretStr("short")
        )


@pytest.mark.parametrize(
    "qq_email",
    [
        "abc@qq.com",  # non-digit local part
        "123abc@qq.com",  # mixed local part
        "123456@QQ.com.evil.cn",  # domain suffix attack
        "123456@foxmail.com",  # wrong domain
        "123456@gmail.com",  # wrong domain
        "@qq.com",  # empty local part
        "not-an-email",
    ],
)
def test_register_rejects_non_qq_mailbox(service: IdentityService, qq_email: str) -> None:
    with pytest.raises(IdentityError, match="QQ 邮箱"):
        service.register(_registration(qq_email=qq_email))


@pytest.mark.parametrize(
    "username",
    ["   ", "has space", "name@qq.com", "白\n换行"],
)
def test_register_rejects_invalid_username(service: IdentityService, username: str) -> None:
    with pytest.raises(IdentityError, match="用户名"):
        service.register(_registration(username=username))


def test_register_rejects_overlong_username_at_contract() -> None:
    with pytest.raises(ValidationError, match="too_long"):
        AccountRegistration(
            username="a" * 33, qq_email="123456@qq.com", password=SecretStr("correct-horse-12")
        )


def test_register_prevents_duplicate_qq_email(service: IdentityService) -> None:
    service.register(_registration())
    with pytest.raises(IdentityError, match="无法完成注册"):
        service.register(_registration(username="另一个用户"))


def test_register_username_uniqueness_is_case_insensitive(service: IdentityService) -> None:
    service.register(_registration(username="BridgeUser"))
    with pytest.raises(IdentityError, match="无法完成注册"):
        service.register(_registration(username="bridgeuser", qq_email="654321@qq.com"))
    with pytest.raises(IdentityError, match="无法完成注册"):
        service.register(_registration(username="BRIDGEUSER", qq_email="654321@qq.com"))


def test_authenticate_with_username(service: IdentityService) -> None:
    registered = service.register(_registration(username="BridgeUser"))
    result = service.authenticate(_credentials(identifier="bridgeuser"))

    assert result.account.id == registered.account.id
    assert result.session.id != registered.session.id
    assert result.session_token


def test_authenticate_with_qq_email(service: IdentityService) -> None:
    registered = service.register(_registration())
    result = service.authenticate(_credentials(identifier="123456@qq.com"))

    assert result.account.id == registered.account.id


def test_authenticate_rejects_unknown_identifier(service: IdentityService) -> None:
    with pytest.raises(IdentityError, match="用户名或密码不正确"):
        service.authenticate(_credentials())


def test_authenticate_rejects_wrong_password(service: IdentityService) -> None:
    service.register(_registration())
    with pytest.raises(IdentityError, match="用户名或密码不正确"):
        service.authenticate(_credentials(password="wrong-password-12"))


def test_authenticate_errors_do_not_distinguish_identifier_from_password(
    service: IdentityService,
) -> None:
    # Unknown username, unknown QQ mailbox and wrong password all produce the
    # same uniform message.
    for identifier in ("unknown-user", "999999@qq.com"):
        with pytest.raises(IdentityError, match="用户名或密码不正确"):
            service.authenticate(_credentials(identifier=identifier))

    service.register(_registration())
    with pytest.raises(IdentityError, match="用户名或密码不正确"):
        service.authenticate(_credentials(password="wrong-password-12"))


def test_change_username_keeps_stable_account_id(service: IdentityService) -> None:
    registered = service.register(_registration(username="BridgeUser"))

    updated = service.change_username(registered.account.id, "新桥桥")

    assert updated.id == registered.account.id
    assert updated.username == "新桥桥"

    # Old username no longer logs in; new username does.
    with pytest.raises(IdentityError, match="用户名或密码不正确"):
        service.authenticate(_credentials(identifier="BridgeUser"))
    result = service.authenticate(_credentials(identifier="新桥桥"))
    assert result.account.id == registered.account.id

    # QQ mailbox login still works after the rename.
    result = service.authenticate(_credentials(identifier="123456@qq.com"))
    assert result.account.id == registered.account.id


def test_change_username_rejects_taken_name(service: IdentityService) -> None:
    first = service.register(_registration(username="BridgeUser"))
    service.register(_registration(username="另一个用户", qq_email="654321@qq.com"))

    with pytest.raises(IdentityError, match="无法修改用户名"):
        service.change_username(first.account.id, "另一个用户")


def test_change_username_allows_own_name_in_different_case(
    service: IdentityService,
) -> None:
    registered = service.register(_registration(username="BridgeUser"))

    updated = service.change_username(registered.account.id, "bridgeuser")

    assert updated.username == "bridgeuser"
    result = service.authenticate(_credentials(identifier="BRIDGEUSER"))
    assert result.account.id == registered.account.id


def test_resolve_session_returns_subject(service: IdentityService) -> None:
    registered = service.register(_registration())
    resolved = service.resolve_session(registered.session_token)

    assert resolved.subject.account_id == registered.account.id
    assert resolved.subject.session_id == registered.session.id
    assert resolved.subject.auth_method == AuthMethod.PASSWORD
    assert resolved.account.id == registered.account.id


def test_resolve_session_rejects_revoked_session(service: IdentityService) -> None:
    registered = service.register(_registration())
    service.revoke_session(registered.session.id)

    with pytest.raises(IdentityError, match="会话已失效"):
        service.resolve_session(registered.session_token)


def test_resolve_session_rejects_unknown_token(service: IdentityService) -> None:
    with pytest.raises(IdentityError, match="会话无效"):
        service.resolve_session("not-a-real-token")


def test_logout_revokes_session(service: IdentityService) -> None:
    registered = service.register(_registration())
    service.revoke_session(registered.session.id)

    with pytest.raises(IdentityError, match="会话已失效"):
        service.resolve_session(registered.session_token)


def test_recovery_flow_resets_password_and_revokes_sessions(
    service: IdentityService,
) -> None:
    registered = service.register(_registration())
    original_token = registered.session_token

    service.request_recovery(RecoveryRequest(qq_email="123456@qq.com"))
    recovery_token = service.test_create_recovery_token("123456@qq.com")

    result = service.reset_password_with_recovery(
        RecoveryReset(token=recovery_token, new_password=SecretStr("new-stable-password-12"))
    )

    assert result.account.id == registered.account.id
    assert result.session.id != registered.session.id

    # Old session is invalidated.
    with pytest.raises(IdentityError, match="会话已失效"):
        service.resolve_session(original_token)

    # Resolved session reports the correct auth method.
    resolved = service.resolve_session(result.session_token)
    assert resolved.subject.auth_method == AuthMethod.RECOVERY

    # New password works; old password does not.
    service.authenticate(_credentials(password="new-stable-password-12"))
    with pytest.raises(IdentityError, match="用户名或密码不正确"):
        service.authenticate(_credentials(password="correct-horse-12"))


def test_recovery_request_is_silent_for_unknown_qq_email(service: IdentityService) -> None:
    # Must not raise or leak that the QQ mailbox is unregistered.
    service.request_recovery(RecoveryRequest(qq_email="999999@qq.com"))


def test_recovery_rejects_expired_or_reused_token(service: IdentityService) -> None:
    service.register(_registration())
    token = service.test_create_recovery_token("123456@qq.com")

    # First use consumes the token.
    service.reset_password_with_recovery(
        RecoveryReset(token=token, new_password=SecretStr("new-stable-password-12"))
    )

    # Reuse fails.
    with pytest.raises(IdentityError, match="恢复链接已过期或无效"):
        service.reset_password_with_recovery(
            RecoveryReset(token=token, new_password=SecretStr("another-password-12"))
        )
