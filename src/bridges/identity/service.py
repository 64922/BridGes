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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from bridges.contracts.identity import (
    QQ_EMAIL_PATTERN,
    USERNAME_PATTERN,
    Account,
    AccountRegistration,
    AuthMethod,
    AuthResponse,
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

    def __init__(self, state_store: StateStore | None = None) -> None:
        self._accounts: dict[str, _StoredAccount] = {}
        self._sessions: dict[str, _StoredSession] = {}
        self._username_to_account: dict[str, str] = {}
        self._qq_email_to_account: dict[str, str] = {}
        self._recovery_states: dict[str, _RecoveryState] = {}
        self._state_store = state_store
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
        self._recovery_states = {
            account_id: _RecoveryState(
                account_id=str(value["account_id"]),
                token_hash=str(value["token_hash"]),
                expires_at=datetime.fromisoformat(value["expires_at"]),
            )
            for account_id, value in state.get("recovery_states", {}).items()
        }

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
                        "auth_method": stored.auth_method.value,
                    }
                    for session_id, stored in self._sessions.items()
                },
                "username_to_account": self._username_to_account,
                "qq_email_to_account": self._qq_email_to_account,
                "recovery_states": {
                    account_id: {
                        "account_id": state.account_id,
                        "token_hash": state.token_hash,
                        "expires_at": state.expires_at.isoformat(),
                    }
                    for account_id, state in self._recovery_states.items()
                },
            },
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _hash_token(self, token: str) -> str:
        # Tokens are opaque high-entropy secrets; a simple hash is sufficient to
        # prevent timing leaks and to allow revocation without storing plaintext.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

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

        username = self._validate_username(new_username)
        normalized = normalize_username(username)
        existing_owner = self._username_to_account.get(normalized)
        if existing_owner is not None and existing_owner != account_id:
            raise IdentityError("无法修改用户名。", code="conflict")

        old_normalized = normalize_username(stored.account.username)
        self._username_to_account.pop(old_normalized, None)
        self._username_to_account[normalized] = account_id
        stored.account.username = username
        stored.account.updated_at = self._now()
        self._persist()
        return stored.account

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
        self._sessions[session.id] = _StoredSession(
            session=session,
            token_hash=self._hash_token(token),
            auth_method=method,
        )
        return AuthResult(account=account, session=session, session_token=token)

    def resolve_session(self, token: str) -> SessionResponse:
        """Resolve an opaque session token to a subject context."""
        token_hash = self._hash_token(token)
        now = self._now()

        for stored in self._sessions.values():
            if stored.token_hash != token_hash:
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
