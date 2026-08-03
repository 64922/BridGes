"use client";

import { useEffect, useRef, useState } from "react";

import { FormField } from "@/components/bridges/FormField";
import { AccountAvatar } from "@/components/account/AccountAvatar";
import { Button } from "@/components/design-system/Button";
import { ErrorSummary } from "@/components/design-system/ErrorSummary";
import { Icon } from "@/components/design-system/Icon";
import { LoadingStatus } from "@/components/design-system/LoadingStatus";
import { useAuth } from "@/context/AuthContext";
import {
  ApiError,
  classifyApiError,
  type AvatarChoice,
  updateProfile,
  uploadAvatar,
} from "@/lib/api";

import styles from "./AccountSettings.module.css";

const MAX_AVATAR_BYTES = 2 * 1024 * 1024;
const ALLOWED_AVATAR_TYPES = new Set(["image/png", "image/jpeg"]);
const USERNAME_PATTERN = /^[^@\s]{1,32}$/;

const avatarChoices: { value: AvatarChoice; label: string }[] = [
  { value: "initials", label: "首字头像" },
  { value: "bridge", label: "桥梁标志" },
  { value: "knowledge", label: "知识手册" },
  { value: "constellation", label: "探索星图" },
];

/** Real current-account profile editor for username and static avatar. */
export function PersonalProfileSettings() {
  const { user, refreshSession } = useAuth();
  const [username, setUsername] = useState(user?.username || "");
  const [avatarChoice, setAvatarChoice] = useState<AvatarChoice>(
    user?.avatar_choice || "initials"
  );
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [usernameError, setUsernameError] = useState<string | undefined>();
  const [fileError, setFileError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const previewUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!user) return;
    setUsername(user.username);
    setAvatarChoice(user.avatar_choice);
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

  const selectStaticAvatar = (choice: AvatarChoice) => {
    clearPreview();
    setAvatarFile(null);
    setSelectedFileName(null);
    setFileError(undefined);
    setAvatarChoice(choice);
    setSuccess(false);
  };

  const selectFile = (file: File | undefined) => {
    clearPreview();
    setAvatarFile(null);
    setSelectedFileName(file?.name ?? null);
    setFileError(undefined);
    setSuccess(false);
    if (!file) return;
    if (!ALLOWED_AVATAR_TYPES.has(file.type)) {
      setFileError("请选择静态 PNG 或 JPEG 图片；系统会在保存时再次校验真实类型。");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setFileError("头像不能超过 2 MiB，请压缩后重试。");
      return;
    }
    const nextPreviewUrl = URL.createObjectURL(file);
    previewUrlRef.current = nextPreviewUrl;
    setPreviewUrl(nextPreviewUrl);
    setAvatarFile(file);
    setAvatarChoice("uploaded");
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedUsername = username.trim();
    if (!USERNAME_PATTERN.test(trimmedUsername)) {
      setUsernameError("用户名需为 1-32 个字符，不能包含空格或 @ 符号。");
      document.getElementById("profile-username")?.focus();
      return;
    }
    if (fileError) {
      document.getElementById("profile-avatar-upload")?.focus();
      return;
    }

    setUsernameError(undefined);
    setFormError(null);
    setSuccess(false);
    setSubmitting(true);
    let profileSaved = false;
    try {
      await updateProfile({
        username: trimmedUsername,
        avatar_choice: avatarFile ? undefined : avatarChoice,
      });
      profileSaved = true;
      if (avatarFile) await uploadAvatar(avatarFile);
      await refreshSession();
      clearPreview();
      setAvatarFile(null);
      setSelectedFileName(null);
      setSuccess(true);
    } catch (cause) {
      if (profileSaved || classifyApiError(cause) === "session") {
        await refreshSession();
      }
      const message = cause instanceof Error ? cause.message : "保存失败，请稍后重试。";
      setFormError(
        profileSaved && avatarFile
          ? `用户名已保存，但头像上传失败：${message}`
          : message
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <header>
          <p className={styles.eyebrow}>账户设置 · 个人资料</p>
          <h1 className={styles.title}>让每次相遇都认得是你</h1>
          <p className={styles.lead}>
            头像和用户名用于界面展示；稳定账户 ID 与 QQ 邮箱归属不会因修改而变化。
            BridGes 不会根据头像推断你的身份、性格或情绪。
          </p>
        </header>

        <form className={styles.stack} onSubmit={submit} noValidate>
          {formError && <ErrorSummary title="个人资料未能完整保存" errors={[formError]} />}
          {success && (
            <div className={styles.successBanner} role="status" data-testid="profile-success">
              <Icon name="check" size={20} aria-hidden />
              <span>个人资料已保存，侧栏与当前会话已同步更新。</span>
            </div>
          )}

          <section className={styles.card} aria-labelledby="avatar-settings-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="avatar-settings-title" className={styles.cardTitle}>头像</h2>
                <p className={styles.cardDescription}>
                  选择 BridGes 原创静态头像，或上传你拥有使用权的图片。
                </p>
              </div>
              {!user.has_uploaded_avatar && avatarChoice === "initials" && (
                <span className={styles.statusBadge}>尚未上传图片</span>
              )}
            </div>

            <div className={styles.avatarLayout}>
              <div className={styles.avatarPreview}>
                <AccountAvatar
                  account={user}
                  size={88}
                  choice={avatarChoice}
                  previewUrl={previewUrl}
                  ariaLabel="当前头像预览"
                />
                <span>当前头像预览</span>
              </div>

              <div>
                <fieldset className={styles.avatarChoices}>
                  <legend className={styles.avatarLegend}>选择静态头像</legend>
                  {avatarChoices.map((choice) => (
                    <button
                      key={choice.value}
                      type="button"
                      role="radio"
                      aria-checked={avatarChoice === choice.value && !avatarFile}
                      className={styles.avatarOption}
                      onClick={() => selectStaticAvatar(choice.value)}
                    >
                      <AccountAvatar account={user} size={42} choice={choice.value} />
                      <span>{choice.label}</span>
                    </button>
                  ))}
                </fieldset>

                <div className={styles.fileField}>
                  <strong>上传图片</strong>
                  <div className={styles.filePicker}>
                    <input
                      className={styles.fileInput}
                      id="profile-avatar-upload"
                      name="avatar"
                      type="file"
                      accept="image/png,image/jpeg"
                      aria-label="上传图片"
                      aria-invalid={Boolean(fileError) || undefined}
                      aria-describedby={fileError ? "profile-avatar-error" : "profile-avatar-hint"}
                      onChange={(event) => selectFile(event.target.files?.[0])}
                    />
                    <label className={styles.fileButton} htmlFor="profile-avatar-upload">
                      选择图片
                    </label>
                    <span className={styles.fileName} aria-live="polite">
                      {selectedFileName || "尚未选择文件"}
                    </span>
                  </div>
                  <p id="profile-avatar-hint" className={styles.hint}>
                    仅支持静态 PNG/JPEG，最大 2 MiB；文件路径不会发送或显示。
                  </p>
                  {fileError && (
                    <p id="profile-avatar-error" role="alert" className={styles.errorText}>
                      {fileError}
                    </p>
                  )}
                </div>
              </div>
            </div>
          </section>

          <section className={styles.card} aria-labelledby="identity-settings-title">
            <div className={styles.cardHeader}>
              <div>
                <h2 id="identity-settings-title" className={styles.cardTitle}>账户显示</h2>
                <p className={styles.cardDescription}>
                  用户名可修改且大小写不敏感唯一；账户归属字段只读。
                </p>
              </div>
            </div>

            <div className={styles.formStack}>
              <FormField
                id="profile-username"
                label="用户名"
                value={username}
                onChange={(value) => {
                  setUsername(value);
                  setUsernameError(undefined);
                  setSuccess(false);
                }}
                error={usernameError}
                hint="1-32 个字符，不能包含空格或 @ 符号。保存后仍可用新用户名或 QQ 邮箱登录。"
                required
                autoComplete="username"
              />

              <dl className={styles.identityGrid} aria-label="不可变账户归属">
                <div className={styles.identityItem}>
                  <dt>稳定账户 ID</dt>
                  <dd><code>{user.id}</code></dd>
                </div>
                <div className={styles.identityItem}>
                  <dt>QQ 邮箱归属</dt>
                  <dd>{user.qq_email}</dd>
                </div>
              </dl>

              <div className={styles.infoBanner}>
                <Icon name="info" size={20} aria-hidden />
                <span>这两项不会随头像或用户名修改，也不会被展示字段替代为所有权键。</span>
              </div>
            </div>
          </section>

          <div className={styles.actions}>
            {submitting && <LoadingStatus message="正在安全保存个人资料…" />}
            <Button type="submit" variant="primary" size="lg" disabled={submitting}>
              {submitting ? "正在保存…" : "保存个人资料"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
