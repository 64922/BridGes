import type { Account, AtomicProfileFeedbackProjection, AtomicProfileItemCreateRequest, AtomicProfileItemEvidenceProjection, AtomicProfileItemFeedbackRequest, AtomicProfileItemModifyRequest, AtomicProfileItemProjection, FourDimensionProfileModifyRequest, FourDimensionProfileRecord, OngoingDomainReferenceUpdateRequest, OngoingItemStateUpdateRequest, OngoingItemsProjection, ProfileAccountControlsProjection, ProfileStatusProjection } from "./types";
import { API_BASE, parseApiError } from "./core";

// ---------- 画像中心（Issue 25） ----------

export async function listFourDimensionProfileRecords(): Promise<FourDimensionProfileRecord[]> {
  const res = await fetch(`${API_BASE}/profiles/four-dimensions`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getFourDimensionProfileStatus(): Promise<ProfileStatusProjection> {
  const res = await fetch(`${API_BASE}/profiles/status`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function modifyFourDimensionProfileRecord(
  recordId: string,
  request: FourDimensionProfileModifyRequest
): Promise<FourDimensionProfileRecord> {
  const res = await fetch(
    `${API_BASE}/profiles/four-dimensions/${encodeURIComponent(recordId)}`,
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

export async function withdrawFourDimensionProfileRecord(
  recordId: string,
  version: number
): Promise<FourDimensionProfileRecord> {
  const res = await fetch(
    `${API_BASE}/profiles/four-dimensions/${encodeURIComponent(recordId)}/withdraw`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ version }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function deleteFourDimensionProfileRecord(
  recordId: string,
  version: number
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/profiles/four-dimensions/${encodeURIComponent(recordId)}`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ version }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
}

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

export async function updateOngoingDomainReference(
  itemId: string,
  request: OngoingDomainReferenceUpdateRequest
): Promise<OngoingItemsProjection> {
  const res = await fetch(
    `${API_BASE}/profiles/ongoing/${encodeURIComponent(itemId)}/domain-reference`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(request),
    }
  );
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
