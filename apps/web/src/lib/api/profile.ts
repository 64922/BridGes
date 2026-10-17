import type { Account, AtomicProfileFeedbackProjection, AtomicProfileItemCreateRequest, AtomicProfileItemEvidenceProjection, AtomicProfileItemFeedbackRequest, AtomicProfileItemModifyRequest, AtomicProfileItemProjection, LearningReferencesProjection, OngoingItemStateUpdateRequest, OngoingItemsProjection, ProfileAccountControlsProjection, ProfileOverviewProjection } from "./types";
import { API_BASE, parseApiError } from "./core";

// ---------- 原子长期信息列表（V2 Issue 08） ----------

export async function listAtomicProfileItems(): Promise<
  AtomicProfileItemProjection[]
> {
  const res = await fetch(`${API_BASE}/profiles/items`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function createAtomicProfileItem(
  request: AtomicProfileItemCreateRequest
): Promise<AtomicProfileItemProjection> {
  const res = await fetch(`${API_BASE}/profiles/items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function modifyAtomicProfileItem(
  itemId: string,
  request: AtomicProfileItemModifyRequest
): Promise<AtomicProfileItemProjection> {
  const res = await fetch(
    `${API_BASE}/profiles/items/${encodeURIComponent(itemId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function deleteAtomicProfileItem(
  itemId: string,
  version: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/profiles/items/${encodeURIComponent(itemId)}`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ version }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
}

// 改进工单 20：展开一条信息的依据（只取本条必要数据）与四类反馈。

export async function fetchAtomicProfileItemEvidence(
  itemId: string
): Promise<AtomicProfileItemEvidenceProjection> {
  const res = await fetch(
    `${API_BASE}/profiles/items/${encodeURIComponent(itemId)}/evidence`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function submitAtomicProfileItemFeedback(
  itemId: string,
  request: AtomicProfileItemFeedbackRequest
): Promise<AtomicProfileFeedbackProjection> {
  const res = await fetch(
    `${API_BASE}/profiles/items/${encodeURIComponent(itemId)}/feedback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------- 画像记录/使用控制（改进工单 07） ----------

export async function fetchProfileControls(): Promise<ProfileAccountControlsProjection> {
  const res = await fetch(`${API_BASE}/profiles/controls`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function updateProfileControls(
  request: { recording_enabled?: boolean; usage_enabled?: boolean }
): Promise<ProfileAccountControlsProjection> {
  const res = await fetch(`${API_BASE}/profiles/controls`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------- 进行中事项（画像工单 13） ----------

export async function fetchOngoingItems(): Promise<OngoingItemsProjection> {
  const res = await fetch(`${API_BASE}/profiles/ongoing`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function updateOngoingItemState(
  itemId: string,
  request: OngoingItemStateUpdateRequest
): Promise<OngoingItemsProjection> {
  const res = await fetch(
    `${API_BASE}/profiles/ongoing/${encodeURIComponent(itemId)}/state`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------- 学习引用区（画像工单 14） ----------

export async function fetchLearningReferences(): Promise<LearningReferencesProjection> {
  const res = await fetch(`${API_BASE}/profiles/learning-references`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------- 整体概览（画像工单 15） ----------

export async function fetchProfileOverview(): Promise<ProfileOverviewProjection> {
  const res = await fetch(`${API_BASE}/profiles/overview`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function removeAvatar(): Promise<Account> {
  const res = await fetch(`${API_BASE}/auth/profile/avatar`, {
    method: "DELETE",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}
