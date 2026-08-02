"""Identity and session contracts shared by API and Web.

These models define the public surface of account registration, authentication,
session management, and credential recovery. They intentionally avoid exposing
internal hashes or storage details.

BridGes accounts are keyed by a stable internal ID (ADR-0003). The username and
the QQ mailbox are login identifiers only; they must never be used as data
ownership keys. The QQ mailbox is restricted to a digits-only QQ number plus
the literal ``@qq.com`` domain (ADR-0017).
"""

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, SecretStr

from bridges.contracts.institution import MembershipContext

#: Strict QQ mailbox shape: digits-only local part, literal ``qq.com`` domain.
QQ_EMAIL_PATTERN = re.compile(r"^\d+@qq\.com$")

#: Usernames must not contain ``@`` so the login identifier field can
#: unambiguously route between username and QQ mailbox lookup.
USERNAME_PATTERN = re.compile(r"^[^@\s]{1,32}$")


def normalize_username(username: str) -> str:
    """Return the canonical, case-insensitive comparison form of a username."""
    return username.strip().casefold()


def normalize_qq_email(qq_email: str) -> str:
    """Return the canonical comparison form of a QQ mailbox address."""
    return qq_email.strip().lower()


class AuthMethod(str, Enum):
    """How the current subject authenticated."""

    PASSWORD = "password"
    RECOVERY = "recovery"
    OIDC = "oidc"
    SERVICE = "service"


class Account(BaseModel):
    """Public account projection."""

    id: str = Field(description="Stable opaque account identifier.")
    username: str = Field(
        description="Public, mutable username. Unique after case-insensitive normalization."
    )
    qq_email: str = Field(
        description="Unique QQ mailbox (digits-only QQ number plus @qq.com)."
    )
    created_at: datetime = Field(description="Account creation timestamp.")
    updated_at: datetime = Field(description="Last account update timestamp.")


class AccountRegistration(BaseModel):
    """Request to create a new account.

    Format validation beyond shape (QQ mailbox pattern, username rules,
    password length) is enforced by the identity service so that error
    messages stay uniform and non-leaking.
    """

    username: str = Field(description="Desired public username.", min_length=1, max_length=32)
    qq_email: str = Field(description="QQ mailbox in the form <digits>@qq.com.", min_length=1)
    password: SecretStr = Field(
        description="Account password.",
        min_length=12,
    )


class LoginCredential(BaseModel):
    """Request to authenticate with the current username or QQ mailbox."""

    identifier: str = Field(
        description="Current username or QQ mailbox of the account.",
        min_length=1,
    )
    password: SecretStr = Field(description="Account password.")


class RecoveryRequest(BaseModel):
    """Request a credential recovery flow.

    The response is intentionally uniform whether the QQ mailbox is registered
    or not, to prevent account enumeration.
    """

    qq_email: str = Field(description="QQ mailbox to recover.", min_length=1)


class RecoveryReset(BaseModel):
    """Reset password using a recovery token."""

    token: str = Field(description="Recovery token received through the recovery channel.")
    new_password: SecretStr = Field(
        description="New account password.",
        min_length=12,
    )


class Session(BaseModel):
    """Public session projection.

    The opaque session token is never part of any JSON contract; it is only
    transported through the secure HttpOnly session cookie.
    """

    id: str = Field(description="Stable session identifier.")
    account_id: str = Field(description="Owning account identifier.")
    created_at: datetime = Field(description="Session creation timestamp.")
    expires_at: datetime = Field(description="Session expiration timestamp.")
    revoked_at: datetime | None = Field(
        default=None,
        description="If set, the session has been revoked and must not be honored.",
    )


class SubjectContext(BaseModel):
    """Resolved subject for an authenticated request.

    This is the canonical object carried by request state, audit logs, and RLS
    context. It never includes the session secret. Memberships are populated by
    the API layer from the institution service so downstream services can
    evaluate institution-scoped access without re-querying identity.
    """

    account_id: str = Field(description="Authenticated account identifier.")
    session_id: str = Field(description="Current session identifier.")
    auth_method: AuthMethod = Field(description="Authentication method for this session.")
    device_id: str | None = Field(
        default=None,
        description="Device identifier when known.",
    )
    memberships: list[MembershipContext] = Field(
        default_factory=list,
        description="Institution memberships for the current account.",
    )


class AuthResponse(BaseModel):
    """Response to a successful authentication operation.

    The session token is deliberately absent: browsers receive it exclusively
    through the secure HttpOnly session cookie, never through JSON, URLs, or
    client-readable storage.
    """

    account: Account = Field(description="Authenticated account.")
    session: Session = Field(description="Newly created session.")


class SessionResponse(BaseModel):
    """Response for an existing session lookup."""

    account: Account = Field(description="Authenticated account.")
    session: Session = Field(description="Current session.")
    subject: SubjectContext = Field(description="Resolved subject context.")


class AuthError(BaseModel):
    """Uniform authentication error response.

    Errors intentionally share the same shape to avoid leaking whether an
    identifier is registered, whether a password is wrong, or whether a token
    exists.
    """

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
