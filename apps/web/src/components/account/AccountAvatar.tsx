"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/design-system/Icon";
import type { User } from "@/context/AuthContext";
import type { AvatarChoice } from "@/lib/api";

interface AccountAvatarProps {
  account: User;
  size?: number;
  choice?: AvatarChoice;
  previewUrl?: string | null;
  ariaLabel?: string;
}

/** Owner-selected static avatar with a safe initials fallback. */
export function AccountAvatar({
  account,
  size = 36,
  choice = account.avatar_choice,
  previewUrl,
  ariaLabel,
}: AccountAvatarProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const initials = account.username.trim().slice(0, 1).toLocaleUpperCase() || "桥";
  const uploadedUrl = previewUrl || (
    account.has_uploaded_avatar
      ? `/api/auth/profile/avatar?v=${encodeURIComponent(account.avatar_updated_at || account.updated_at)}`
      : null
  );
  const showUploaded = choice === "uploaded" && uploadedUrl && !imageFailed;

  useEffect(() => {
    setImageFailed(false);
  }, [choice, uploadedUrl]);

  return (
    <span
      role={ariaLabel ? "img" : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      data-testid="account-avatar"
      data-avatar-choice={choice}
      style={{
        width: size,
        height: size,
        flex: `0 0 ${size}px`,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
        borderRadius: "var(--radius-full)",
        border: "1px solid var(--color-border-strong)",
        background:
          choice === "constellation"
            ? "linear-gradient(145deg, var(--color-status-info-bg), var(--color-accent-primary-soft))"
            : "var(--color-accent-primary-soft)",
        color: "var(--color-accent-primary)",
        fontWeight: 700,
        fontSize: Math.max(13, Math.round(size * 0.36)),
      }}
    >
      {showUploaded ? (
        // The authorized endpoint never exposes a host path. The surrounding
        // control/current-avatar group supplies the accessible name.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={uploadedUrl}
          alt=""
          onError={() => setImageFailed(true)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : choice === "bridge" ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src="/brand/bridges-logo-icon.svg"
          alt=""
          style={{ width: "72%", height: "72%" }}
        />
      ) : choice === "knowledge" ? (
        <Icon name="knowledgeBase" size={Math.round(size * 0.52)} aria-hidden />
      ) : choice === "constellation" ? (
        <Icon name="learningProject" size={Math.round(size * 0.52)} aria-hidden />
      ) : (
        initials
      )}
    </span>
  );
}
