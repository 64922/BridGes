"""Module-interface tests for the identity service.

The seam under test: an account can be registered, authenticated, logged out,
and recovered; invalid attempts receive uniform, non-leaking errors; recovery
revokes prior sessions.
"""

import pytest
from pydantic import ValidationError

from science_companion.contracts.identity import (
    AccountRegistration,
    AuthMethod,
    LoginCredential,
    RecoveryRequest,
    RecoveryReset,
)
from science_companion.identity import IdentityError, IdentityService


@pytest.fixture
def service() -> IdentityService:
    return IdentityService()


def _registration(email: str = "user@example.com", password: str = "correct-horse-12") -> AccountRegistration:
    return AccountRegistration(
        email=email,
        password=password,
        agreed_to_terms=True,
    )


def _credentials(
    email: str = "user@example.com", password: str = "correct-horse-12"
) -> LoginCredential:
    return LoginCredential(email=email, password=password)


def test_register_creates_account_and_session(service: IdentityService) -> None:
    response = service.register(_registration())

    assert response.account.email == "user@example.com"
    assert response.session.account_id == response.account.id
    assert response.session.revoked_at is None
    assert response.session_token
    assert response.session.id


def test_register_requires_terms(service: IdentityService) -> None:
    request = AccountRegistration(email="user@example.com", password="correct-horse-12", agreed_to_terms=False)
    with pytest.raises(IdentityError, match="必须同意"):
        service.register(request)


def test_register_rejects_short_password(service: IdentityService) -> None:
    # The contract rejects passwords shorter than 12 characters before the service
    # even sees them, providing a clear 422 to API consumers.
    with pytest.raises(ValidationError, match="too_short"):
        AccountRegistration(email="user@example.com", password="short", agreed_to_terms=True)


def test_register_prevents_duplicate_email(service: IdentityService) -> None:
    service.register(_registration())
    with pytest.raises(IdentityError, match="无法完成注册"):
        service.register(_registration())


def test_authenticate_with_valid_credentials(service: IdentityService) -> None:
    registered = service.register(_registration())
    response = service.authenticate(_credentials())

    assert response.account.id == registered.account.id
    assert response.session.id != registered.session.id
    assert response.session_token


def test_authenticate_rejects_unknown_email(service: IdentityService) -> None:
    with pytest.raises(IdentityError, match="邮箱或密码不正确"):
        service.authenticate(_credentials())


def test_authenticate_rejects_wrong_password(service: IdentityService) -> None:
    service.register(_registration())
    with pytest.raises(IdentityError, match="邮箱或密码不正确"):
        service.authenticate(_credentials(password="wrong-password-12"))


def test_authenticate_errors_do_not_distinguish_email_from_password(service: IdentityService) -> None:
    # Both unknown email and wrong password produce the same message.
    with pytest.raises(IdentityError, match="邮箱或密码不正确"):
        service.authenticate(_credentials(email="unknown@example.com"))

    service.register(_registration())
    with pytest.raises(IdentityError, match="邮箱或密码不正确"):
        service.authenticate(_credentials(password="wrong-password-12"))


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


def test_recovery_flow_resets_password_and_revokes_sessions(service: IdentityService) -> None:
    registered = service.register(_registration())
    original_token = registered.session_token

    service.request_recovery(RecoveryRequest(email="user@example.com"))
    recovery_token = service.test_create_recovery_token("user@example.com")

    response = service.reset_password_with_recovery(
        RecoveryReset(token=recovery_token, new_password="new-stable-password-12")
    )

    assert response.account.id == registered.account.id
    assert response.session.id != registered.session.id

    # Old session is invalidated.
    with pytest.raises(IdentityError, match="会话已失效"):
        service.resolve_session(original_token)

    # Resolved session reports the correct auth method.
    resolved = service.resolve_session(response.session_token)
    assert resolved.subject.auth_method == AuthMethod.RECOVERY

    # New password works; old password does not.
    service.authenticate(_credentials(password="new-stable-password-12"))
    with pytest.raises(IdentityError, match="邮箱或密码不正确"):
        service.authenticate(_credentials(password="correct-horse-12"))


def test_recovery_request_is_silent_for_unknown_email(service: IdentityService) -> None:
    # Must not raise or leak that the email is unregistered.
    service.request_recovery(RecoveryRequest(email="missing@example.com"))


def test_recovery_rejects_expired_or_reused_token(service: IdentityService) -> None:
    service.register(_registration())
    token = service.test_create_recovery_token("user@example.com")

    # First use consumes the token.
    service.reset_password_with_recovery(RecoveryReset(token=token, new_password="new-stable-password-12"))

    # Reuse fails.
    with pytest.raises(IdentityError, match="恢复链接已过期或无效"):
        service.reset_password_with_recovery(RecoveryReset(token=token, new_password="another-password-12"))
