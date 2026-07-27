"""Identity and session contracts shared by API and Web.

These models define the public surface of account registration, authentication,
session management, and credential recovery. They intentionally avoid exposing
internal hashes or storage details.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field, SecretStr


class AuthMethod(str, Enum):
    """How the current subject authenticated."""

    PASSWORD = "password"
    RECOVERY = "recovery"
    OIDC = "oidc"
    SERVICE = "service"


class Account(BaseModel):
    """Public account projection."""

    id: str = Field(description="Stable account identifier (UUIDv7/ULID).")
    email: EmailStr = Field(description="Verified email address.")
    created_at: datetime = Field(description="Account creation timestamp.")
    updated_at: datetime = Field(description="Last account update timestamp.")


class AccountRegistration(BaseModel):
    """Request to create a new account."""

    email: EmailStr = Field(description="Email address to register.")
    password: SecretStr = Field(
        description="Account password.",
        min_length=12,
    )
    agreed_to_terms: bool = Field(
        description="User has agreed to terms and privacy policy.",
    )


class LoginCredential(BaseModel):
    """Request to authenticate with email and password."""

    email: EmailStr = Field(description="Registered email address.")
    password: SecretStr = Field(description="Account password.")


class RecoveryRequest(BaseModel):
    """Request a credential recovery flow.

    The response is intentionally uniform whether the email is registered or not,
    to prevent account enumeration.
    """

    email: EmailStr = Field(description="Email address to recover.")


class RecoveryReset(BaseModel):
    """Reset password using a recovery token."""

    token: str = Field(description="Recovery token received through the recovery channel.")
    new_password: SecretStr = Field(
        description="New account password.",
        min_length=12,
    )


class Session(BaseModel):
    """Public session projection.

    The opaque session token is only exposed on creation (login/register/recovery).
    Subsequent requests use the HttpOnly cookie.
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
    context. It never includes the session secret.
    """

    account_id: str = Field(description="Authenticated account identifier.")
    session_id: str = Field(description="Current session identifier.")
    auth_method: AuthMethod = Field(description="Authentication method for this session.")
    device_id: str | None = Field(
        default=None,
        description="Device identifier when known.",
    )


class AuthResponse(BaseModel):
    """Response to a successful authentication operation.

    The session_token is delivered only once; callers must store it according to
    their client type (browser cookie managed by the API, native secure storage).
    """

    account: Account = Field(description="Authenticated account.")
    session: Session = Field(description="Newly created session.")
    session_token: str = Field(description="Opaque session token (one-time exposure).")


class SessionResponse(BaseModel):
    """Response for an existing session lookup."""

    account: Account = Field(description="Authenticated account.")
    session: Session = Field(description="Current session.")
    subject: SubjectContext = Field(description="Resolved subject context.")


class AuthError(BaseModel):
    """Uniform authentication error response.

    Errors intentionally share the same shape to avoid leaking whether an email
    is registered, whether a password is wrong, or whether a token exists.
    """

    error: str = Field(description="Stable error code.")
    message: str = Field(description="Human-readable, non-leaking message.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque detail safe for logging; must not expose internal state.",
    )
