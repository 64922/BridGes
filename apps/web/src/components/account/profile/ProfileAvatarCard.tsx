"use client";

import { useEffect, useRef, useState } from "react";

import { AccountAvatar } from "@/components/account/AccountAvatar";
import { Button } from "@/components/design-system/Button";
import { useAuth } from "@/context/AuthContext";
import {
  removeAvatar,
  updateProfile,
  uploadAvatar,
  type AvatarChoice,
} from "@/lib/api";

import styles from "./ProfileCenter.module.css";

const avatarChoices: { value: AvatarChoice; label: string }[] = [
  { value: "initials", label: "姓名首字" },
  { value: "bridge", label: "桥牌" },
  { value: "knowledge", label: "知识手册" },
  { value: "constellation", label: "探索星图" },
];

const ALLOWED_AVATAR_TYPES = new Set(["image/png", "image/jpeg"]);
const MAX_AVATAR_BYTES = 2 * 1024 * 1024;

/**
 * 画像中心头像卡：选择静态头像、上传或移除图片。
 *
 * 头像只承担视觉标识（ADR-0021），不用于推断身份、性格或情绪；卡片说明
 * 与移除按钮共同保证这一点。所有操作写入失败时展示错误，不显示假成功。
 */
export function ProfileAvatarCard() {
  const { user, refreshSession } = useAuth();
  const [choice, setChoice] = useState<AvatarChoice>(user?.avatar_choice || "initials");
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "save" | "remove">(null);
  const previewUrlRef = useRef<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!user) return;
    setChoice(user.avatar_choice);
  }, [user]);

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    []
  );

  if (!user) return null;

  const clearPreview = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    setPreviewUrl(null);
  };

  const selectStaticAvatar = (next: AvatarChoice) => {
    clearPreview();
    setAvatarFile(null);
    setSelectedFileName(null);
    setFileError(null);
    setError(null);
    setChoice(next);
  };

  const selectFile = (file: File | null) => {
    clearPreview();
    setAvatarFile(null);
    setSelectedFileName(null);
    setFileError(null);
    setError(null);
    if (!file) return;
    if (!ALLOWED_AVATAR_TYPES.has(file.type)) {
      setFileError("请选择静态 PNG 或 JPEG 图片；系统会在保存时再次校验真实类型。");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setFileError("头像不能超过 2 MiB，请压缩后重试。");
      return;
    }
    previewUrlRef.current = URL.createObjectURL(file);
    setPreviewUrl(previewUrlRef.current);
    setAvatarFile(file);
    setSelectedFileName(file.name);
    setChoice("uploaded");
  };

  const save = async () => {
    setBusy("save");
    setError(null);
    try {
      await updateProfile({
        username: user.username,
        avatar_choice: avatarFile ? undefined : choice,
      });
      if (avatarFile) await uploadAvatar(avatarFile);
      await refreshSession();
      clearPreview();
      setAvatarFile(null);
      setSelectedFileName(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "头像保存失败，请稍后重试。");
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy("remove");
    setError(null);
    try {
      await removeAvatar();
      await refreshSession();
      clearPreview();
      setAvatarFile(null);
      setSelectedFileName(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "头像移除失败，请稍后重试。");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className={styles.card} aria-labelledby="profile-avatar-title">
      <div className={styles.cardHeader}>
        <div>
          <h2 id="profile-avatar-title" className={styles.cardTitle}>
            静态头像
          </h2>
          <p className={styles.cardDescription}>
            头像仅作视觉标识，不用于推断身份、性格或情绪；可随时更换或移除。
          </p>
        </div>
      </div>

      <div className={styles.avatarLayout}>
        <div className={styles.avatarPreview}>
          <AccountAvatar
            account={user}
            size={96}
            choice={choice}
            previewUrl={previewUrl}
            ariaLabel="当前头像预览"
          />
          {!user.has_uploaded_avatar && choice === "initials" && (
            <p className={styles.avatarHint}>尚未上传图片</p>
          )}
        </div>

        <div>
          <fieldset className={styles.avatarChoices}>
            <legend className="sc-visually-hidden">选择静态头像</legend>
            {avatarChoices.map((option) => (
              <label
                key={option.value}
                className={`${styles.avatarOption} ${
                  choice === option.value ? styles.avatarOptionActive : ""
                }`}
              >
                <input
                  type="radio"
                  name="avatar-choice"
                  value={option.value}
                  checked={choice === option.value}
                  onChange={() => selectStaticAvatar(option.value)}
                  className="sc-visually-hidden"
                />
                {option.label}
              </label>
            ))}
          </fieldset>

          <div className={styles.fileField}>
            <div className={styles.fileRow}>
              <input
                ref={fileInputRef}
                id="profile-avatar-upload"
                type="file"
                accept="image/png,image/jpeg"
                className="sc-visually-hidden"
                onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                选择图片文件
              </Button>
              {selectedFileName && (
                <span className={styles.fileName} aria-live="polite">
                  {selectedFileName}
                </span>
              )}
              <Button variant="primary" size="sm" onClick={save} isLoading={busy === "save"}>
                保存头像
              </Button>
              {user.has_uploaded_avatar && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={remove}
                  isLoading={busy === "remove"}
                >
                  移除头像
                </Button>
              )}
            </div>
            <p id="profile-avatar-hint" className={styles.hint}>
              支持静态 PNG 或 JPEG，最大 2 MiB；系统会校验文件真实类型。
            </p>
            {fileError && (
              <p id="profile-avatar-error" role="alert" className={styles.errorText}>
                {fileError}
              </p>
            )}
            {error && (
              <p role="alert" className={styles.errorText}>
                {error}
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
