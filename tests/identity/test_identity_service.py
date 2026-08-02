"""Module-interface tests for the identity service.

The seam under test: an account can be registered with a username plus a strict
QQ mailbox, authenticated by either identifier, logged out, and recovered;
usernames stay unique after case-insensitive normalization and remain mutable;
invalid attempts receive uniform, non-leaking errors; recovery revokes prior
sessions.
"""

import base64
import zlib
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from bridges.contracts.identity import (
    AccountProfileUpdate,
    AccountRegistration,
    AuthMethod,
    AvatarChoice,
    LoginCredential,
    RecoveryRequest,
    RecoveryReset,
)
from bridges.identity import IdentityError, IdentityService
from bridges.persistence import SqliteStateStore
from bridges.storage import (
    BridgesDatabase,
    BridgesObjectRepository,
    EncryptedFileObjectStore,
)

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return len(data).to_bytes(4, "big") + chunk_type + data + checksum.to_bytes(4, "big")


def _apng_from_fixture() -> bytes:
    # Insert the APNG animation-control chunk immediately after IHDR.
    return _PNG_1X1[:33] + _png_chunk(b"acTL", b"\x00\x00\x00\x01\x00\x00\x00\x00") + _PNG_1X1[33:]


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


def test_update_profile_keeps_stable_identity_and_changes_avatar_choice(
    service: IdentityService,
) -> None:
    registered = service.register(_registration(username="BridgeUser"))

    updated = service.update_profile(
        registered.account.id,
        AccountProfileUpdate(username="新桥桥", avatar_choice=AvatarChoice.KNOWLEDGE),
    )

    assert updated.id == registered.account.id
    assert updated.qq_email == registered.account.qq_email
    assert updated.username == "新桥桥"
    assert updated.avatar_choice == AvatarChoice.KNOWLEDGE
    assert updated.has_uploaded_avatar is False


def test_avatar_upload_validates_detected_type_size_and_account_ownership(
    service: IdentityService,
) -> None:
    registered = service.register(_registration())

    with pytest.raises(IdentityError, match="类型"):
        service.store_avatar(registered.account.id, _PNG_1X1, "image/jpeg")
    with pytest.raises(IdentityError, match="2 MiB"):
        service.store_avatar(
            registered.account.id,
            _PNG_1X1 + b"x" * (2 * 1024 * 1024),
            "image/png",
        )
    with pytest.raises(IdentityError, match="访问权限"):
        service.store_avatar("guessed-account-id", _PNG_1X1, "image/png")

    updated = service.store_avatar(
        registered.account.id,
        _PNG_1X1,
        "image/png",
    )
    avatar = service.get_avatar(registered.account.id)

    assert updated.id == registered.account.id
    assert updated.qq_email == registered.account.qq_email
    assert updated.avatar_choice == AvatarChoice.UPLOADED
    assert updated.has_uploaded_avatar is True
    assert avatar.content == _PNG_1X1
    assert avatar.media_type == "image/png"


@pytest.mark.parametrize(
    "content, media_type",
    [
        (_apng_from_fixture(), "image/png"),
        (_PNG_1X1[:-1] + bytes([_PNG_1X1[-1] ^ 1]), "image/png"),
        (b"\xff\xd8\xff\xc0\x00\x08\x08\x00\x01\x00\x01\x01\xff\xd9", "image/jpeg"),
    ],
)
def test_avatar_upload_rejects_animation_and_malformed_images(
    service: IdentityService,
    content: bytes,
    media_type: str,
) -> None:
    registered = service.register(_registration())

    with pytest.raises(IdentityError, match="静态"):
        service.store_avatar(registered.account.id, content, media_type)


def test_avatar_bytes_use_the_encrypted_account_object_repository(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "bridges.db"
    encryption_key = "avatar-object-test-key"
    state_store = SqliteStateStore(database_path, encryption_key=encryption_key)
    repository = BridgesObjectRepository(
        BridgesDatabase(database_path),
        EncryptedFileObjectStore(tmp_path / "object-data", encryption_key),
    )
    first = IdentityService(state_store=state_store, object_repository=repository)
    registered = first.register(_registration())
    first.store_avatar(registered.account.id, _PNG_1X1, "image/png")

    avatar_state = state_store.load("identity")["avatars"][registered.account.id]
    assert set(avatar_state) == {"media_type", "object_id"}
    stored_object = repository.list_objects(registered.account.id)[0]
    encrypted_path = (
        tmp_path
        / "object-data"
        / "objects"
        / stored_object.content_hash[:2]
        / stored_object.content_hash
    )
    assert _PNG_1X1 not in encrypted_path.read_bytes()

    reloaded = IdentityService(
        state_store=SqliteStateStore(database_path, encryption_key=encryption_key),
        object_repository=BridgesObjectRepository(
            BridgesDatabase(database_path),
            EncryptedFileObjectStore(tmp_path / "object-data", encryption_key),
        ),
    )
    assert reloaded.get_avatar(registered.account.id).content == _PNG_1X1


def test_sensitive_settings_require_recent_password_reauthentication(
    service: IdentityService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = service.register(_registration())

    assert service.requires_recent_auth(registered.session.id) is False
    initial_now = service._now()
    monkeypatch.setattr(service, "_now", lambda: initial_now + timedelta(minutes=6))
    assert service.requires_recent_auth(registered.session.id) is True

    with pytest.raises(IdentityError, match="密码不正确"):
        service.reauthenticate(
            registered.account.id,
            registered.session.id,
            "wrong-password-12",
        )
    service.reauthenticate(
        registered.account.id,
        registered.session.id,
        "correct-horse-12",
    )
    assert service.requires_recent_auth(registered.session.id) is False


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
