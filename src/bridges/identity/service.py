"""Identity and session domain service.

This module implements the deep module boundary for account registration,
authentication, session lifecycle, and credential recovery. It hides password
hashing, token generation, and revocation semantics.

BridGes uses a stable internal account ID as the ownership key for all user
data (ADR-0003). The username and the QQ mailbox are mutable login identifiers
only. The QQ mailbox is strictly ``<digits>@qq.com``; no verification code is
sent at registration time — mailbox control is verified later through personal
SMTP self-send verification (ADR-0017).
"""

from __future__ import annotations

import hashlib
import secrets
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from bridges.contracts.identity import (
    QQ_EMAIL_PATTERN,
    USERNAME_PATTERN,
    Account,
    AccountProfileUpdate,
    AccountRegistration,
    AuthMethod,
    AuthResponse,
    AvatarChoice,
    DeviceAccountProjection,
    DeviceAccountStatus,
    LoginCredential,
    RecoveryRequest,
    RecoveryReset,
    Session,
    SessionResponse,
    SubjectContext,
    normalize_qq_email,
    normalize_username,
)
from bridges.persistence import StateStore
from bridges.storage.errors import StorageError

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# Conservative session lifetime; the cookie contract mirrors this value.
_SESSION_TTL = timedelta(hours=8)
_RECOVERY_TTL = timedelta(minutes=30)
_RECENT_AUTH_TTL = timedelta(minutes=5)
_REAUTH_FAILURE_LIMIT = 5
_REAUTH_LOCKOUT = timedelta(minutes=5)
_MAX_SESSION_TOKEN_ALIASES = 16
_MAX_SECURITY_AUDIT_EVENTS = 1000
MAX_AVATAR_BYTES = 2 * 1024 * 1024


class IdentityError(Exception):
    """Domain exception for authentication failures.

    The message is safe to expose to callers; it never leaks internal state such
    as whether a username or QQ mailbox is registered. ``code`` classifies the
    failure so the API layer can pick a status code without parsing messages:
    ``validation`` (bad input), ``conflict`` (duplicate identifier),
    ``credentials`` (authentication failed), ``session`` (session problem).
    """

    def __init__(self, message: str, *, code: str = "validation") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class AuthResult:
    """Internal authentication outcome.

    The raw session token exists only here and is handed to the API layer so it
    can be placed into the secure HttpOnly cookie. It is never serialized into
    a public JSON contract.
    """

    account: Account
    session: Session
    session_token: str

    def public_response(self) -> AuthResponse:
        return AuthResponse(account=self.account, session=self.session)


@dataclass
class _StoredAccount:
    account: Account
    password_hash: str


@dataclass
class _StoredSession:
    session: Session
    token_hash: str
    auth_method: AuthMethod
    reauthenticated_at: datetime
    token_hashes: set[str] = field(default_factory=set)
    reauth_failures: int = 0
    reauth_locked_until: datetime | None = None


@dataclass
class _StoredDevice:
    token_hash: str
    session_ids: list[str] = field(default_factory=list)
    last_device_operation: int = 0


@dataclass(frozen=True)
class AvatarContent:
    """Authorized avatar bytes returned without a host filesystem path."""

    content: bytes
    media_type: str


@dataclass
class _StoredAvatar:
    object_id: str
    media_type: str


class _StoredObjectRef(Protocol):
    @property
    def object_id(self) -> str: ...


class AccountObjectRepository(Protocol):
    """Minimal account-owned object seam required by the identity domain."""

    def ensure_account(self, account_id: str, email: str) -> None: ...

    def create_object(
        self, account_id: str, original_filename: str, content: bytes
    ) -> _StoredObjectRef: ...

    def get_content(self, account_id: str, object_id: str) -> bytes: ...

    def delete_object(self, account_id: str, object_id: str) -> _StoredObjectRef: ...


@dataclass
class _RecoveryState:
    account_id: str
    token_hash: str
    expires_at: datetime


class IdentityService:
    """Account, session and recovery service backed by a pluggable StateStore.

    The interface intentionally mirrors a database-backed adapter so that a
    later relational implementation can swap in without changing callers.
    """

    def __init__(
        self,
        state_store: StateStore | None = None,
        object_repository: AccountObjectRepository | None = None,
    ) -> None:
        self._accounts: dict[str, _StoredAccount] = {}
        self._sessions: dict[str, _StoredSession] = {}
        self._username_to_account: dict[str, str] = {}
        self._qq_email_to_account: dict[str, str] = {}
        self._avatars: dict[str, _StoredAvatar] = {}
        self._memory_avatar_content: dict[str, bytes] = {}
        self._recovery_states: dict[str, _RecoveryState] = {}
        self._devices: dict[str, _StoredDevice] = {}
        self.security_audit_log: list[dict[str, str]] = []
        self._state_store = state_store
        self._object_repository = object_repository
        self._load_state()

    def _load_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load("identity") or {}
        self._accounts = {
            account_id: _StoredAccount(
                account=Account.model_validate(value["account"]),
                password_hash=str(value["password_hash"]),
            )
            for account_id, value in state.get("accounts", {}).items()
        }
        self._sessions = {
            session_id: _StoredSession(
                session=Session.model_validate(value["session"]),
                token_hash=str(value["token_hash"]),
                auth_method=AuthMethod(value["auth_method"]),
                reauthenticated_at=datetime.fromisoformat(
                    value.get(
                        "reauthenticated_at",
                        value["session"]["created_at"],
                    )
                ),
                token_hashes=set(value.get("token_hashes", [value["token_hash"]])),
                reauth_failures=int(value.get("reauth_failures", 0)),
                reauth_locked_until=(
                    datetime.fromisoformat(value["reauth_locked_until"])
                    if value.get("reauth_locked_until")
                    else None
                ),
            )
            for session_id, value in state.get("sessions", {}).items()
        }
        self._username_to_account = {
            str(username): str(account_id)
            for username, account_id in state.get("username_to_account", {}).items()
        }
        self._qq_email_to_account = {
            str(qq_email): str(account_id)
            for qq_email, account_id in state.get("qq_email_to_account", {}).items()
        }
        self._avatars = {
            account_id: _StoredAvatar(
                object_id=str(value["object_id"]),
                media_type=str(value["media_type"]),
            )
            for account_id, value in state.get("avatars", {}).items()
        }
        self._recovery_states = {
            account_id: _RecoveryState(
                account_id=str(value["account_id"]),
                token_hash=str(value["token_hash"]),
                expires_at=datetime.fromisoformat(value["expires_at"]),
            )
            for account_id, value in state.get("recovery_states", {}).items()
        }
        self._devices = {
            device_id: _StoredDevice(
                token_hash=str(value["token_hash"]),
                session_ids=[str(session_id) for session_id in value.get("session_ids", [])],
                last_device_operation=int(
                    value.get(
                        "last_device_operation", value.get("last_switch_operation", 0)
                    )
                ),
            )
            for device_id, value in state.get("devices", {}).items()
        }
        self.security_audit_log = [
            {
                key: str(event[key])
                for key in ("action", "account_id", "session_id", "outcome", "at")
                if key in event
            }
            for event in state.get("security_audit_log", [])
            if isinstance(event, dict)
        ]

    def _persist(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save(
            "identity",
            {
                "accounts": {
                    account_id: {
                        "account": stored.account.model_dump(mode="json"),
                        "password_hash": stored.password_hash,
                    }
                    for account_id, stored in self._accounts.items()
                },
                "sessions": {
                    session_id: {
                        "session": stored.session.model_dump(mode="json"),
                        "token_hash": stored.token_hash,
                        "token_hashes": sorted(stored.token_hashes or {stored.token_hash}),
                        "auth_method": stored.auth_method.value,
                        "reauthenticated_at": stored.reauthenticated_at.isoformat(),
                        "reauth_failures": stored.reauth_failures,
                        "reauth_locked_until": (
                            stored.reauth_locked_until.isoformat()
                            if stored.reauth_locked_until
                            else None
                        ),
                    }
                    for session_id, stored in self._sessions.items()
                },
                "username_to_account": self._username_to_account,
                "qq_email_to_account": self._qq_email_to_account,
                "avatars": {
                    account_id: {
                        "object_id": avatar.object_id,
                        "media_type": avatar.media_type,
                    }
                    for account_id, avatar in self._avatars.items()
                },
                "recovery_states": {
                    account_id: {
                        "account_id": state.account_id,
                        "token_hash": state.token_hash,
                        "expires_at": state.expires_at.isoformat(),
                    }
                    for account_id, state in self._recovery_states.items()
                },
                "devices": {
                    device_id: {
                        "token_hash": device.token_hash,
                        "session_ids": device.session_ids,
                        "last_device_operation": device.last_device_operation,
                    }
                    for device_id, device in self._devices.items()
                },
                "security_audit_log": self.security_audit_log[-_MAX_SECURITY_AUDIT_EVENTS:],
            },
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _hash_token(self, token: str) -> str:
        # Tokens are opaque high-entropy secrets; a simple hash is sufficient to
        # prevent timing leaks and to allow revocation without storing plaintext.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _audit_security_event(
        self, action: str, account_id: str, session_id: str, outcome: str
    ) -> None:
        """Record security metadata without passwords, tokens, or mailbox secrets."""
        self.security_audit_log.append(
            {
                "action": action,
                "account_id": account_id,
                "session_id": session_id,
                "outcome": outcome,
                "at": self._now().isoformat(),
            }
        )

    @staticmethod
    def _validate_username(username: str) -> str:
        stripped = username.strip()
        if not USERNAME_PATTERN.fullmatch(stripped):
            raise IdentityError(
                "用户名需为 1-32 个字符，不能包含空格或 @ 符号。"
            )
        return stripped

    @staticmethod
    def _validate_qq_email(qq_email: str) -> str:
        normalized = normalize_qq_email(qq_email)
        if not QQ_EMAIL_PATTERN.fullmatch(normalized):
            raise IdentityError("QQ 邮箱应为纯数字 QQ 号加 @qq.com。")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12:
            raise IdentityError("密码长度不足。")

    def register(self, request: AccountRegistration) -> AuthResult:
        """Create a new account and return an authenticated session."""
        username = self._validate_username(request.username)
        qq_email = self._validate_qq_email(request.qq_email)
        self._validate_password(request.password.get_secret_value())

        normalized_username = normalize_username(username)
        if (
            normalized_username in self._username_to_account
            or qq_email in self._qq_email_to_account
        ):
            # Uniform conflict message: do not reveal which identifier exists.
            raise IdentityError("无法完成注册。", code="conflict")

        now = self._now()
        account = Account(
            id=secrets.token_urlsafe(16),
            username=username,
            qq_email=qq_email,
            created_at=now,
            updated_at=now,
        )
        stored = _StoredAccount(
            account=account,
            password_hash=_PASSWORD_HASHER.hash(request.password.get_secret_value()),
        )
        if self._object_repository is not None:
            try:
                self._object_repository.ensure_account(account.id, account.qq_email)
            except StorageError as exc:
                raise IdentityError("账户对象归属初始化失败，请稍后重试。") from exc
        self._accounts[account.id] = stored
        self._username_to_account[normalized_username] = account.id
        self._qq_email_to_account[qq_email] = account.id

        result = self._create_session(account, AuthMethod.PASSWORD)
        self._persist()
        return result

    def authenticate(self, credentials: LoginCredential) -> AuthResult:
        """Validate credentials and create a new session.

        The identifier may be the account's current username or its QQ mailbox.
        The failure message is uniform so callers cannot enumerate accounts.
        """
        identifier = credentials.identifier.strip()
        if "@" in identifier:
            account_id = self._qq_email_to_account.get(normalize_qq_email(identifier))
        else:
            account_id = self._username_to_account.get(normalize_username(identifier))
        if account_id is None:
            raise IdentityError("用户名或密码不正确。", code="credentials")

        stored = self._accounts[account_id]
        try:
            _PASSWORD_HASHER.verify(
                stored.password_hash, credentials.password.get_secret_value()
            )
        except VerifyMismatchError:
            raise IdentityError("用户名或密码不正确。", code="credentials") from None

        # Rehash if parameters have improved.
        if _PASSWORD_HASHER.check_needs_rehash(stored.password_hash):
            stored.password_hash = _PASSWORD_HASHER.hash(
                credentials.password.get_secret_value()
            )

        stored.account.updated_at = self._now()
        result = self._create_session(stored.account, AuthMethod.PASSWORD)
        self._persist()
        return result

    def change_username(self, account_id: str, new_username: str) -> Account:
        """Change an account's username.

        The stable account ID is untouched, so all data ownership stays intact.
        The new username must satisfy the same format and uniqueness rules as
        registration.
        """
        stored = self._accounts.get(account_id)
        if stored is None:
            raise IdentityError("账户不存在。")
        self._apply_username_update(stored, new_username)
        stored.account.updated_at = self._now()
        self._persist()
        return stored.account

    def _apply_username_update(
        self, stored: _StoredAccount, new_username: str
    ) -> None:
        """Validate and update the single username index without persisting."""
        username = self._validate_username(new_username)
        account_id = stored.account.id
        normalized = normalize_username(username)
        existing_owner = self._username_to_account.get(normalized)
        if existing_owner is not None and existing_owner != account_id:
            raise IdentityError("无法修改用户名。", code="conflict")

        old_normalized = normalize_username(stored.account.username)
        self._username_to_account.pop(old_normalized, None)
        self._username_to_account[normalized] = account_id
        stored.account.username = username

    def update_profile(
        self,
        account_id: str,
        request: AccountProfileUpdate,
    ) -> Account:
        """Update owner-scoped profile fields without changing account ownership."""
        stored = self._accounts.get(account_id)
        if stored is None:
            raise IdentityError("账户不存在或没有访问权限。")

        if (
            request.avatar_choice == AvatarChoice.UPLOADED
            and account_id not in self._avatars
        ):
            raise IdentityError("尚未上传可用头像，请先选择图片。")

        self._apply_username_update(stored, request.username)
        if request.avatar_choice is not None:
            stored.account.avatar_choice = request.avatar_choice
        stored.account.updated_at = self._now()
        self._persist()
        return stored.account

    def store_avatar(
        self,
        account_id: str,
        content: bytes,
        declared_media_type: str,
    ) -> Account:
        """Validate and store a static avatar owned by ``account_id``."""
        stored = self._accounts.get(account_id)
        if stored is None:
            raise IdentityError("账户不存在或没有访问权限。")
        if not content:
            raise IdentityError("头像文件为空，请重新选择。")
        if len(content) > MAX_AVATAR_BYTES:
            raise IdentityError("头像不能超过 2 MiB。", code="too_large")

        detected_media_type = self._detect_avatar_media_type(content)
        normalized_declared_type = declared_media_type.split(";", 1)[0].strip().lower()
        if detected_media_type is None or normalized_declared_type != detected_media_type:
            raise IdentityError("头像真实类型与声明类型不一致，仅支持静态 PNG 或 JPEG。")

        old_avatar = self._avatars.get(account_id)
        object_id = self._create_avatar_object(stored.account, content, detected_media_type)
        self._avatars[account_id] = _StoredAvatar(
            object_id=object_id, media_type=detected_media_type
        )
        now = self._now()
        stored.account.avatar_choice = AvatarChoice.UPLOADED
        stored.account.has_uploaded_avatar = True
        stored.account.avatar_updated_at = now
        stored.account.updated_at = now
        self._persist()
        if old_avatar is not None and old_avatar.object_id != object_id:
            self._delete_avatar_object(account_id, old_avatar.object_id)
        return stored.account

    def remove_avatar(self, account_id: str) -> Account:
        """Remove the uploaded avatar object and fall back to a static choice.

        Only the owning account can remove its avatar; the uploaded object is
        deleted from the encrypted object store so it is no longer recoverable.
        """
        stored = self._accounts.get(account_id)
        if stored is None:
            raise IdentityError("账户不存在或没有访问权限。")
        avatar = self._avatars.get(account_id)
        if avatar is None:
            raise IdentityError("该账户尚未上传头像。")

        self._delete_avatar_object(account_id, avatar.object_id)
        del self._avatars[account_id]
        now = self._now()
        stored.account.has_uploaded_avatar = False
        stored.account.avatar_updated_at = now
        if stored.account.avatar_choice == AvatarChoice.UPLOADED:
            stored.account.avatar_choice = AvatarChoice.INITIALS
        stored.account.updated_at = now
        self._persist()
        return stored.account

    def get_avatar(self, account_id: str) -> AvatarContent:
        """Return avatar bytes only when they belong to the authorized account."""
        if account_id not in self._accounts:
            raise IdentityError("头像不存在或没有访问权限。")
        avatar = self._avatars.get(account_id)
        if avatar is None:
            raise IdentityError("头像不存在或没有访问权限。")
        try:
            if self._object_repository is not None:
                content = self._object_repository.get_content(account_id, avatar.object_id)
            else:
                content = self._memory_avatar_content[avatar.object_id]
        except (KeyError, StorageError) as exc:
            raise IdentityError("头像不存在或没有访问权限。") from exc
        return AvatarContent(content=content, media_type=avatar.media_type)

    def _create_avatar_object(
        self, account: Account, content: bytes, media_type: str
    ) -> str:
        extension = "png" if media_type == "image/png" else "jpg"
        if self._object_repository is None:
            object_id = secrets.token_urlsafe(16)
            self._memory_avatar_content[object_id] = content
            return object_id
        try:
            self._object_repository.ensure_account(account.id, account.qq_email)
            stored = self._object_repository.create_object(
                account.id, f"avatar.{extension}", content
            )
        except StorageError as exc:
            raise IdentityError("头像保存失败，请稍后重试。") from exc
        return stored.object_id

    def _delete_avatar_object(self, account_id: str, object_id: str) -> None:
        if self._object_repository is None:
            self._memory_avatar_content.pop(object_id, None)
            return
        try:
            self._object_repository.delete_object(account_id, object_id)
        except StorageError as exc:
            raise IdentityError("新头像已保存，但旧头像清理失败，请稍后重试。") from exc

    @staticmethod
    def _detect_avatar_media_type(content: bytes) -> str | None:
        """Fully decode a bounded, structurally valid static PNG/JPEG."""
        png_signature = b"\x89PNG\r\n\x1a\n"
        if content.startswith(png_signature) and len(content) >= 45:
            offset = len(png_signature)
            width = height = 0
            first_chunk = True
            valid_static_png = False
            while offset + 12 <= len(content):
                chunk_length = int.from_bytes(content[offset : offset + 4], "big")
                chunk_end = offset + 12 + chunk_length
                if chunk_end > len(content):
                    break
                chunk_type = content[offset + 4 : offset + 8]
                chunk_data = content[offset + 8 : offset + 8 + chunk_length]
                expected_crc = int.from_bytes(content[chunk_end - 4 : chunk_end], "big")
                actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
                if expected_crc != actual_crc:
                    break
                if first_chunk:
                    if chunk_type != b"IHDR" or chunk_length != 13:
                        break
                    width = int.from_bytes(chunk_data[0:4], "big")
                    height = int.from_bytes(chunk_data[4:8], "big")
                    first_chunk = False
                if chunk_type == b"acTL":
                    break
                if chunk_type == b"IEND":
                    valid_static_png = chunk_length == 0 and chunk_end == len(content)
                    break
                offset = chunk_end
            if (
                valid_static_png
                and 0 < width <= 4096
                and 0 < height <= 4096
                and IdentityService._image_decodes(content, "png")
            ):
                return "image/png"

        if content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
            index = 2
            sof_markers = {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }
            while index + 4 <= len(content):
                if content[index] != 0xFF:
                    index += 1
                    continue
                while index < len(content) and content[index] == 0xFF:
                    index += 1
                if index >= len(content):
                    break
                marker = content[index]
                index += 1
                if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    continue
                if index + 2 > len(content):
                    break
                segment_length = int.from_bytes(content[index : index + 2], "big")
                if segment_length < 2 or index + segment_length > len(content):
                    break
                if marker in sof_markers and segment_length >= 8:
                    height = int.from_bytes(content[index + 3 : index + 5], "big")
                    width = int.from_bytes(content[index + 5 : index + 7], "big")
                    if (
                        0 < width <= 4096
                        and 0 < height <= 4096
                        and IdentityService._image_decodes(content, "jpeg")
                    ):
                        return "image/jpeg"
                    return None
                index += segment_length
        return None

    @staticmethod
    def _image_decodes(content: bytes, file_type: str) -> bool:
        """Force the registered image decoder to parse pixel data."""
        try:
            import fitz  # type: ignore[import-untyped]  # PyMuPDF

            with fitz.open(stream=content, filetype=file_type) as document:
                if document.page_count != 1:
                    return False
                page = document.load_page(0)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(0.05, 0.05), alpha=False)
                return int(pixmap.width) > 0 and int(pixmap.height) > 0
        except Exception:
            # Decoder exceptions vary by malformed container; none are safe to
            # expose, and every one means the upload is not a valid static image.
            return False

    def reauthenticate(
        self,
        account_id: str,
        session_id: str,
        password: str,
    ) -> None:
        """Confirm the current account password for sensitive settings."""
        account = self._accounts.get(account_id)
        session = self._sessions.get(session_id)
        if account is None or session is None or session.session.account_id != account_id:
            raise IdentityError("会话已失效。", code="session")
        now = self._now()
        if session.reauth_locked_until is not None and session.reauth_locked_until > now:
            self._audit_security_event("reauthenticate", account_id, session_id, "locked")
            raise IdentityError("验证失败次数过多，请稍后再试。", code="credentials")
        try:
            _PASSWORD_HASHER.verify(account.password_hash, password)
        except VerifyMismatchError:
            session.reauth_failures += 1
            if session.reauth_failures >= _REAUTH_FAILURE_LIMIT:
                session.reauth_locked_until = now + _REAUTH_LOCKOUT
            self._audit_security_event("reauthenticate", account_id, session_id, "failure")
            self._persist()
            if session.reauth_locked_until is not None:
                raise IdentityError("验证失败次数过多，请稍后再试。", code="credentials") from None
            raise IdentityError("当前账户密码不正确。", code="credentials") from None
        if _PASSWORD_HASHER.check_needs_rehash(account.password_hash):
            account.password_hash = _PASSWORD_HASHER.hash(password)
        session.reauthenticated_at = now
        session.reauth_failures = 0
        session.reauth_locked_until = None
        self._audit_security_event("reauthenticate", account_id, session_id, "success")
        self._persist()

    def requires_recent_auth(self, session_id: str) -> bool:
        """Return whether the session must confirm its password again."""
        stored = self._sessions.get(session_id)
        if stored is None or stored.session.revoked_at is not None:
            raise IdentityError("会话已失效。", code="session")
        return self._now() - stored.reauthenticated_at > _RECENT_AUTH_TTL

    def _create_session(self, account: Account, method: AuthMethod) -> AuthResult:
        now = self._now()
        token = secrets.token_urlsafe(32)
        session = Session(
            id=secrets.token_urlsafe(16),
            account_id=account.id,
            created_at=now,
            expires_at=now + _SESSION_TTL,
            revoked_at=None,
        )
        token_hash = self._hash_token(token)
        self._sessions[session.id] = _StoredSession(
            session=session,
            token_hash=token_hash,
            auth_method=method,
            reauthenticated_at=now,
            token_hashes={token_hash},
        )
        return AuthResult(account=account, session=session, session_token=token)

    def resolve_session(self, token: str) -> SessionResponse:
        """Resolve an opaque session token to a subject context."""
        token_hash = self._hash_token(token)
        now = self._now()

        for stored in self._sessions.values():
            if token_hash not in (stored.token_hashes or {stored.token_hash}):
                continue
            if stored.session.revoked_at is not None:
                raise IdentityError("会话已失效。", code="session")
            if stored.session.expires_at <= now:
                raise IdentityError("会话已过期。", code="session")

            account_stored = self._accounts.get(stored.session.account_id)
            if account_stored is None:
                raise IdentityError("会话已失效。", code="session")

            subject = SubjectContext(
                account_id=account_stored.account.id,
                session_id=stored.session.id,
                auth_method=stored.auth_method,
            )
            return SessionResponse(
                account=account_stored.account,
                session=stored.session,
                subject=subject,
            )

        raise IdentityError("会话无效。", code="session")

    def ensure_device(self, device_token: str | None, session_id: str) -> tuple[str, str]:
        """Create or reuse a device registry and attach the current session."""
        if session_id not in self._sessions:
            raise IdentityError("会话已失效。", code="session")
        device: _StoredDevice | None = None
        device_id: str | None = None
        if device_token:
            token_hash = self._hash_token(device_token)
            for candidate_id, candidate in self._devices.items():
                if candidate.token_hash == token_hash:
                    device_id = candidate_id
                    device = candidate
                    break
        if device is not None and session_id not in device.session_ids:
            # A device cookie must not be enough to join another device's
            # registry. Start a fresh registry for this authenticated session.
            device = None
            device_id = None
        if device is None or device_id is None:
            device_token = secrets.token_urlsafe(32)
            device_id = secrets.token_urlsafe(16)
            device = _StoredDevice(token_hash=self._hash_token(device_token))
            self._devices[device_id] = device
        if session_id not in device.session_ids:
            device.session_ids.append(session_id)
        self._persist()
        assert device_token is not None
        return device_token, device_id

    def is_session_registered_on_device(self, device_token: str, session_id: str) -> bool:
        """Check the device/session binding without exposing registry contents."""
        token_hash = self._hash_token(device_token)
        return any(
            candidate.token_hash == token_hash and session_id in candidate.session_ids
            for candidate in self._devices.values()
        )

    def claim_device_operation(self, device_token: str, operation_id: int) -> bool:
        """Reject an older device operation before it can mint a cookie token."""
        token_hash = self._hash_token(device_token)
        device = next(
            (
                candidate
                for candidate in self._devices.values()
                if candidate.token_hash == token_hash
            ),
            None,
        )
        if device is None or operation_id <= device.last_device_operation:
            return False
        device.last_device_operation = operation_id
        self._persist()
        return True

    def attach_session_to_device(self, device_token: str, session_id: str) -> None:
        """Attach a newly authenticated session to an existing device."""
        token_hash = self._hash_token(device_token)
        device = next(
            (
                candidate
                for candidate in self._devices.values()
                if candidate.token_hash == token_hash
            ),
            None,
        )
        if device is None or session_id not in self._sessions:
            raise IdentityError("设备账户不可用。", code="device_account")
        if session_id not in device.session_ids:
            device.session_ids.append(session_id)
        self._persist()

    @staticmethod
    def _mask_qq_email(qq_email: str) -> str:
        local, _, domain = qq_email.partition("@")
        if len(local) <= 4:
            # 4 位及更短的 QQ 号只保留首位，避免"前 2 + 后 2"拼接还原完整号码。
            return f"{local[:1]}***@{domain or 'qq.com'}"
        return f"{local[:2]}***{local[-2:]}@{domain or 'qq.com'}"

    def list_device_accounts(
        self, device_token: str, current_session_id: str
    ) -> list[DeviceAccountProjection]:
        """Return one safe, status-aware projection per account on this device."""
        token_hash = self._hash_token(device_token)
        device = next(
            (
                candidate
                for candidate in self._devices.values()
                if candidate.token_hash == token_hash
            ),
            None,
        )
        if device is None:
            raise IdentityError("设备账户不可用。", code="device_account")

        candidates: dict[str, list[_StoredSession]] = {}
        for session_id in device.session_ids:
            stored = self._sessions.get(session_id)
            if stored is None:
                continue
            candidates.setdefault(stored.session.account_id, []).append(stored)

        projections: list[DeviceAccountProjection] = []
        now = self._now()
        for account_id, sessions in candidates.items():
            account_stored = self._accounts.get(account_id)
            if account_stored is None:
                continue
            selected = max(
                sessions,
                key=lambda item: (
                    item.session.id == current_session_id,
                    item.session.revoked_at is None and item.session.expires_at > now,
                    item.session.created_at,
                ),
            )
            active = selected.session.revoked_at is None and selected.session.expires_at > now
            projections.append(
                DeviceAccountProjection(
                    session_id=selected.session.id,
                    username=account_stored.account.username,
                    masked_qq_email=self._mask_qq_email(account_stored.account.qq_email),
                    avatar_choice=account_stored.account.avatar_choice,
                    has_uploaded_avatar=account_stored.account.has_uploaded_avatar,
                    status=(
                        DeviceAccountStatus.ACTIVE
                        if active
                        else DeviceAccountStatus.REAUTH_REQUIRED
                    ),
                    is_current=selected.session.id == current_session_id,
                )
            )
        return sorted(projections, key=lambda item: (not item.is_current, item.username.casefold()))

    def _get_device_session(self, device_token: str, session_id: str) -> _StoredSession:
        """Resolve a session only through a device registry membership."""
        token_hash = self._hash_token(device_token)
        device = next(
            (
                candidate
                for candidate in self._devices.values()
                if candidate.token_hash == token_hash
            ),
            None,
        )
        stored = (
            self._sessions.get(session_id)
            if device and session_id in device.session_ids
            else None
        )
        if stored is None:
            raise IdentityError("设备账户不可用。", code="device_account")
        return stored

    def issue_session_token(self, session_id: str) -> str:
        """Mint a fresh opaque cookie token for an active session."""
        stored = self._sessions.get(session_id)
        if (
            stored is None
            or stored.session.revoked_at is not None
            or stored.session.expires_at <= self._now()
        ):
            raise IdentityError("需要重新认证。", code="reauth_required")
        token = secrets.token_urlsafe(32)
        stored.token_hashes.add(self._hash_token(token))
        if len(stored.token_hashes) > _MAX_SESSION_TOKEN_ALIASES:
            preserved = sorted(stored.token_hashes - {stored.token_hash})[
                -(_MAX_SESSION_TOKEN_ALIASES - 1) :
            ]
            stored.token_hashes = {stored.token_hash, *preserved}
        self._persist()
        return token

    def activate_device_session(self, device_token: str, session_id: str) -> AuthResult:
        """Activate a currently valid account session registered on this device."""
        stored = self._get_device_session(device_token, session_id)
        if stored.session.revoked_at is not None or stored.session.expires_at <= self._now():
            raise IdentityError("请重新输入该账户密码。", code="reauth_required")
        account = self._accounts.get(stored.session.account_id)
        if account is None:
            raise IdentityError("设备账户不可用。", code="device_account")
        return AuthResult(
            account=account.account,
            session=stored.session,
            session_token=self.issue_session_token(session_id),
        )

    def reauthenticate_device_session(
        self, device_token: str, session_id: str, password: str
    ) -> AuthResult:
        """Recreate a device account session after verifying that account's password."""
        stored = self._get_device_session(device_token, session_id)
        account = self._accounts.get(stored.session.account_id)
        if account is None:
            raise IdentityError("账户信息或密码不正确。", code="credentials")
        now = self._now()
        if stored.reauth_locked_until is not None and stored.reauth_locked_until > now:
            self._audit_security_event(
                "device_reauthenticate", account.account.id, session_id, "locked"
            )
            raise IdentityError("验证失败次数过多，请稍后再试。", code="credentials")
        try:
            _PASSWORD_HASHER.verify(account.password_hash, password)
        except VerifyMismatchError:
            stored.reauth_failures += 1
            if stored.reauth_failures >= _REAUTH_FAILURE_LIMIT:
                stored.reauth_locked_until = now + _REAUTH_LOCKOUT
            self._audit_security_event(
                "device_reauthenticate", account.account.id, session_id, "failure"
            )
            self._persist()
            if stored.reauth_locked_until is not None:
                raise IdentityError("验证失败次数过多，请稍后再试。", code="credentials") from None
            raise IdentityError("账户信息或密码不正确。", code="credentials") from None
        stored.reauth_failures = 0
        stored.reauth_locked_until = None
        result = self._create_session(account.account, AuthMethod.PASSWORD)
        token_hash = self._hash_token(device_token)
        device = next(
            device for device in self._devices.values() if device.token_hash == token_hash
        )
        device.session_ids.append(result.session.id)
        self._audit_security_event(
            "device_reauthenticate", account.account.id, result.session.id, "success"
        )
        self._persist()
        return result

    def activate_next_device_session(
        self, device_token: str, excluded_session_id: str
    ) -> AuthResult | None:
        """Find the next active account after a current-session logout."""
        token_hash = self._hash_token(device_token)
        device = next(
            (
                candidate
                for candidate in self._devices.values()
                if candidate.token_hash == token_hash
            ),
            None,
        )
        if device is None:
            return None
        active_sessions = [
            stored
            for session_id in device.session_ids
            if session_id != excluded_session_id
            and (stored := self._sessions.get(session_id)) is not None
            and stored.session.revoked_at is None
            and stored.session.expires_at > self._now()
        ]
        if not active_sessions:
            return None
        selected = max(active_sessions, key=lambda item: item.session.created_at)
        account = self._accounts.get(selected.session.account_id)
        if account is None:
            return None
        return AuthResult(
            account=account.account,
            session=selected.session,
            session_token=self.issue_session_token(selected.session.id),
        )

    def revoke_device_sessions(self, device_token: str) -> list[Session]:
        """Revoke every session registered to one browser device."""
        token_hash = self._hash_token(device_token)
        device = next(
            (
                candidate
                for candidate in self._devices.values()
                if candidate.token_hash == token_hash
            ),
            None,
        )
        if device is None:
            return []
        revoked: list[Session] = []
        for session_id in device.session_ids:
            stored = self._sessions.get(session_id)
            if stored is None or stored.session.revoked_at is not None:
                continue
            stored.session.revoked_at = self._now()
            revoked.append(stored.session)
        self._persist()
        return revoked

    def revoke_session(self, session_id: str) -> Session:
        """Revoke a single session."""
        stored = self._sessions.get(session_id)
        if stored is None:
            raise IdentityError("会话不存在。", code="session")
        stored.session.revoked_at = self._now()
        self._persist()
        return stored.session

    def revoke_all_sessions(
        self, account_id: str, except_session_id: str | None = None
    ) -> list[Session]:
        """Revoke all sessions for an account, optionally preserving one."""
        revoked: list[Session] = []
        now = self._now()
        for stored in self._sessions.values():
            if stored.session.account_id != account_id:
                continue
            if except_session_id is not None and stored.session.id == except_session_id:
                continue
            if stored.session.revoked_at is not None:
                continue
            stored.session.revoked_at = now
            revoked.append(stored.session)
        self._persist()
        return revoked

    def request_recovery(self, request: RecoveryRequest) -> None:
        """Create a recovery token if the account exists.

        This method is intentionally silent when the QQ mailbox is not
        registered so that callers cannot enumerate accounts.
        """
        account_id = self._qq_email_to_account.get(normalize_qq_email(request.qq_email))
        if account_id is None:
            return

        token = secrets.token_urlsafe(32)
        state = _RecoveryState(
            account_id=account_id,
            token_hash=self._hash_token(token),
            expires_at=self._now() + _RECOVERY_TTL,
        )
        # Only one active recovery flow per account at a time.
        self._recovery_states[account_id] = state
        stored = self._accounts[account_id]
        stored.account.updated_at = self._now()
        self._persist()

    def reset_password_with_recovery(self, reset: RecoveryReset) -> AuthResult:
        """Reset password using a recovery token and revoke existing sessions."""
        token_hash = self._hash_token(reset.token)
        now = self._now()

        for account_id, state in list(self._recovery_states.items()):
            if state.token_hash != token_hash:
                continue
            if state.expires_at <= now:
                del self._recovery_states[account_id]
                raise IdentityError("恢复链接已过期或无效。", code="credentials")

            stored = self._accounts.get(account_id)
            if stored is None:
                del self._recovery_states[account_id]
                raise IdentityError("恢复链接已过期或无效。", code="credentials")

            password = reset.new_password.get_secret_value()
            self._validate_password(password)

            stored.password_hash = _PASSWORD_HASHER.hash(password)
            stored.account.updated_at = now
            del self._recovery_states[account_id]

            # Recovery invalidates all prior sessions.
            self.revoke_all_sessions(stored.account.id)

            result = self._create_session(stored.account, AuthMethod.RECOVERY)
            self._persist()
            return result

        raise IdentityError("恢复链接已过期或无效。", code="credentials")

    def get_account(self, account_id: str) -> Account | None:
        stored = self._accounts.get(account_id)
        return stored.account if stored else None

    def get_session(self, session_id: str) -> Session | None:
        stored = self._sessions.get(session_id)
        return stored.session if stored else None

    # ------------------------------------------------------------------
    # Test helpers (not part of the public API contract)
    # ------------------------------------------------------------------

    def test_create_recovery_token(self, qq_email: str) -> str:
        """Create and return a raw recovery token for testing.

        This bypasses the normal out-of-band delivery channel and is only exposed
        for automated tests that cannot receive email.
        """
        account_id = self._qq_email_to_account.get(normalize_qq_email(qq_email))
        if account_id is None:
            raise IdentityError("账户不存在。")

        token = secrets.token_urlsafe(32)
        self._recovery_states[account_id] = _RecoveryState(
            account_id=account_id,
            token_hash=self._hash_token(token),
            expires_at=self._now() + _RECOVERY_TTL,
        )
        return token
