import type { AttestationConclusion, ConflictDisclosure, ConflictDisclosureInput, ConflictOfInterestDeclaration, GrayReleaseCandidate, PackImpactCategory, PackImpactSet, PackInvalidationEvent, PackInvalidationStage, PackInvalidationTrigger, PackRelease, PackRollbackRecord, RevalidationReport, ReviewAttestation, RevocationEvent, SemanticDiff, WorkbenchPackRecord } from "./types";
import { API_BASE, parseApiError } from "./core";

export async function listWorkbenchPacks(): Promise<WorkbenchPackRecord[]> {
  const res = await fetch(`${API_BASE}/domain-packs/workbench`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function registerWorkbenchPack(
  packId: string,
  version: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(`${API_BASE}/domain-packs/workbench/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ pack_id: packId, version }),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getWorkbenchRecord(
  packId: string,
  version: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getSemanticDiff(
  packId: string,
  version: string
): Promise<SemanticDiff> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/semantic-diff`,
    { credentials: "same-origin", cache: "no-store" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function submitContentSignature(
  packId: string,
  version: string,
  opinion: string
): Promise<ReviewAttestation> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/content-signature`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ opinion, conclusion: "approve" }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function assignReviewer(
  packId: string,
  version: string,
  personId: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/reviewer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ person_id: personId }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function assignReleaser(
  packId: string,
  version: string,
  personId: string
): Promise<WorkbenchPackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/releaser`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ person_id: personId }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function submitIndependentSignature(
  packId: string,
  version: string,
  opinion: string,
  conclusion: AttestationConclusion = "approve"
): Promise<ReviewAttestation> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/independent-signature`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ opinion, conclusion }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function prepareGrayRelease(
  packId: string,
  version: string
): Promise<GrayReleaseCandidate> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/gray-release`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function releasePack(
  packId: string,
  version: string
): Promise<PackRelease> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/release`,
    { method: "POST", credentials: "same-origin" }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function declareConflictOfInterest(
  packId: string,
  version: string,
  disclosures: string[]
): Promise<ConflictOfInterestDeclaration> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/conflict-of-interest`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ disclosures }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function addConflictDisclosure(
  packId: string,
  version: string,
  input: ConflictDisclosureInput
): Promise<ConflictDisclosure> {
  const res = await fetch(
    `${API_BASE}/domain-packs/workbench/${encodeURIComponent(packId)}/${encodeURIComponent(version)}/conflict-disclosure`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(input),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// T047：失效、撤销、重验证与受信回滚
// ---------------------------------------------------------------------------

export async function registerSecurityAdmin(): Promise<void> {
  const res = await fetch(`${API_BASE}/domain-packs/security-admins`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) throw await parseApiError(res);
}

export async function fetchSecurityAdminStatus(): Promise<{ is_security_admin: boolean }> {
  const res = await fetch(`${API_BASE}/domain-packs/security-admins`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listInvalidations(packId?: string): Promise<PackInvalidationEvent[]> {
  const query = packId ? `?pack_id=${encodeURIComponent(packId)}` : "";
  const res = await fetch(`${API_BASE}/domain-packs/invalidations${query}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getInvalidation(eventId: string): Promise<PackInvalidationEvent> {
  const res = await fetch(`${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function recordInvalidation(input: {
  pack_id: string;
  version: string;
  trigger: PackInvalidationTrigger;
  reason: string;
  emergency?: boolean;
}): Promise<PackInvalidationEvent> {
  const res = await fetch(`${API_BASE}/domain-packs/invalidations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function advanceInvalidation(
  eventId: string,
  toStage: PackInvalidationStage,
  note = ""
): Promise<PackInvalidationEvent> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/advance`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ to_stage: toStage, note }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function resolveImpact(eventId: string): Promise<PackImpactSet> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/resolve-impact`,
    {
      method: "POST",
      credentials: "same-origin",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getImpactSet(eventId: string): Promise<PackImpactSet> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/impact`,
    {
      credentials: "same-origin",
      cache: "no-store",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function reportRevalidated(
  eventId: string,
  area: PackImpactCategory,
  refIds: string[],
  failed: string[] = []
): Promise<RevalidationReport> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/revalidate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ area, ref_ids: refIds, failed }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function getRevalidationReport(
  eventId: string
): Promise<RevalidationReport | null> {
  const res = await fetch(
    `${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}/revalidation`,
    {
      credentials: "same-origin",
      cache: "no-store",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function emergencyRevoke(input: {
  pack_id: string;
  version: string;
  trigger: PackInvalidationTrigger;
  reason: string;
  second_factor: string;
}): Promise<{ event: PackInvalidationEvent; revocation: RevocationEvent }> {
  const res = await fetch(`${API_BASE}/domain-packs/revocations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listRevocations(): Promise<RevocationEvent[]> {
  const res = await fetch(`${API_BASE}/domain-packs/revocations`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function proposeRollback(input: {
  pack_id: string;
  from_version: string;
  reason: string;
}): Promise<PackRollbackRecord> {
  const res = await fetch(`${API_BASE}/domain-packs/rollbacks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function listRollbacks(): Promise<PackRollbackRecord[]> {
  const res = await fetch(`${API_BASE}/domain-packs/rollbacks`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function confirmRollback(
  rollbackId: string,
  role: "independent" | "platform",
  conclusion: "approve" | "reject" | "needs_changes",
  opinion = ""
): Promise<PackRollbackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/rollbacks/${encodeURIComponent(rollbackId)}/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ role, conclusion, opinion }),
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}

export async function executeRollback(rollbackId: string): Promise<PackRollbackRecord> {
  const res = await fetch(
    `${API_BASE}/domain-packs/rollbacks/${encodeURIComponent(rollbackId)}/execute`,
    {
      method: "POST",
      credentials: "same-origin",
    }
  );
  if (!res.ok) throw await parseApiError(res);
  return res.json();
}
