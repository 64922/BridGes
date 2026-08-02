"""Public identity-service seams for same-device account switching."""

from datetime import timedelta

import pytest
from pydantic import SecretStr

from bridges.contracts.identity import AccountRegistration, LoginCredential
from bridges.identity import IdentityError, IdentityService


def _registration(username: str, qq_email: str) -> AccountRegistration:
    return AccountRegistration(
        username=username,
        qq_email=qq_email,
        password=SecretStr("correct-horse-12"),
    )


def _credentials(identifier: str, password: str) -> LoginCredential:
    return LoginCredential(identifier=identifier, password=SecretStr(password))


def test_reauthentication_locks_after_repeated_failures_and_records_safe_audit_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = IdentityService()
    registered = service.register(_registration("Alice", "111111@qq.com"))
    initial_now = service._now()

    for _ in range(5):
        with pytest.raises(IdentityError):
            service.reauthenticate(
                registered.account.id,
                registered.session.id,
                "wrong-password-12",
            )

    with pytest.raises(IdentityError, match="次数过多"):
        service.reauthenticate(
            registered.account.id,
            registered.session.id,
            "correct-horse-12",
        )
    assert all("password" not in event for event in service.security_audit_log)

    monkeypatch.setattr(service, "_now", lambda: initial_now + timedelta(minutes=6))
    service.reauthenticate(
        registered.account.id,
        registered.session.id,
        "correct-horse-12",
    )
    assert service.requires_recent_auth(registered.session.id) is False


def test_device_registry_only_switches_sessions_registered_on_the_same_device() -> None:
    service = IdentityService()
    alice = service.register(_registration("Alice", "111111@qq.com"))
    bob = service.register(_registration("Bob", "222222@qq.com"))
    device_token, _ = service.ensure_device(None, alice.session.id)
    service.attach_session_to_device(device_token, bob.session.id)

    accounts = service.list_device_accounts(device_token, alice.session.id)
    assert {item.username for item in accounts} == {"Alice", "Bob"}

    with pytest.raises(IdentityError, match="设备账户不可用"):
        service.activate_device_session(device_token, alice.account.id)

    assert service.authenticate(_credentials("Bob", "correct-horse-12")).account.username == "Bob"


def test_device_reauthentication_has_a_failure_limit_and_safe_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = IdentityService()
    alice = service.register(_registration("Alice", "111111@qq.com"))
    bob = service.register(_registration("Bob", "222222@qq.com"))
    device_token, _ = service.ensure_device(None, alice.session.id)
    service.attach_session_to_device(device_token, bob.session.id)
    service.revoke_session(bob.session.id)

    for _ in range(5):
        with pytest.raises(IdentityError):
            service.reauthenticate_device_session(
                device_token, bob.session.id, "wrong-password-12"
            )

    with pytest.raises(IdentityError, match="次数过多"):
        service.reauthenticate_device_session(
            device_token, bob.session.id, "correct-horse-12"
        )
    assert all("password" not in event for event in service.security_audit_log)

    initial_now = service._now()
    monkeypatch.setattr(service, "_now", lambda: initial_now + timedelta(minutes=6))
    restored = service.reauthenticate_device_session(
        device_token, bob.session.id, "correct-horse-12"
    )
    assert restored.account.username == "Bob"


def test_device_cookie_cannot_join_another_session_registry() -> None:
    service = IdentityService()
    alice = service.register(_registration("Alice", "111111@qq.com"))
    bob = service.register(_registration("Bob", "222222@qq.com"))
    alice_device, _ = service.ensure_device(None, alice.session.id)

    bob_device, _ = service.ensure_device(alice_device, bob.session.id)

    assert bob_device != alice_device
    alice_accounts = service.list_device_accounts(alice_device, alice.session.id)
    assert [item.username for item in alice_accounts] == ["Alice"]
    assert [item.username for item in service.list_device_accounts(bob_device, bob.session.id)] == [
        "Bob"
    ]
