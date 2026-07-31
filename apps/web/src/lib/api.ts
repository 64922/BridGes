"use client";

import type { components } from "@science-companion/contracts";

export type HealthProjection = components["schemas"]["HealthProjection"];
export type HealthStatus = components["schemas"]["HealthStatus"];
export type Account = components["schemas"]["Account"];
export type AuthResponse = components["schemas"]["AuthResponse"];
export type SessionResponse = components["schemas"]["SessionResponse"];
export type AuthError = components["schemas"]["AuthError"];
export type Project = components["schemas"]["Project"];
export type ProjectCreateRequest = components["schemas"]["ProjectCreateRequest"];
export type ProjectListProjection = components["schemas"]["ProjectListProjection"];
export type ProjectSummary = components["schemas"]["ProjectSummary"];
export type ProjectError = components["schemas"]["ProjectError"];
export type WorkbenchPackRecord = components["schemas"]["WorkbenchPackRecord"];
export type ReviewAttestation = components["schemas"]["ReviewAttestation"];
export type SemanticDiff = components["schemas"]["SemanticDiff"];
export type SemanticDiffEntry = components["schemas"]["SemanticDiffEntry"];
export type GrayReleaseCandidate = components["schemas"]["GrayReleaseCandidate"];
export type PackRelease = components["schemas"]["PackRelease"];
export type QualificationRecord = components["schemas"]["QualificationRecord"];
export type ConflictOfInterestDeclaration = components["schemas"]["ConflictOfInterestDeclaration"];
export type PackInvalidationEvent = components["schemas"]["PackInvalidationEvent"];
export type PackInvalidationStage = components["schemas"]["PackInvalidationStage"];
export type PackInvalidationTrigger = components["schemas"]["PackInvalidationTrigger"];
export type PackImpactSet = components["schemas"]["PackImpactSet"];
export type PackImpactItem = components["schemas"]["PackImpactItem"];
export type PackImpactCategory = components["schemas"]["PackImpactCategory"];
export type RevocationEvent = components["schemas"]["RevocationEvent"];
export type RevalidationReport = components["schemas"]["RevalidationReport"];
export type PackRollbackRecord = components["schemas"]["PackRollbackRecord"];
export type PackRollbackStatus = components["schemas"]["PackRollbackStatus"];
export type DomainPackError = { error?: string; message?: string };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";

async function parseAuthError(res: Response): Promise<string> {
  try {
    const body: { detail?: AuthError } = await res.json();
    return body.detail?.message || `请求失败（${res.status}）`;
  } catch {
    return `请求失败（${res.status}）`;
  }
}

export async function fetchHealthSummary(options?: { signal?: AbortSignal }): Promise<HealthProjection> {
  const res = await fetch(`${API_BASE}/health`, {
    cache: "no-store",
    signal: options?.signal,
  });
  if (!res.ok) {
    throw new Error(`Health fetch failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchSession(): Promise<SessionResponse> {
  const res = await fetch(`${API_BASE}/auth/session`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function register(
  email: string,
  password: string,
  agreedToTerms: boolean
): Promise<AuthResponse> {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ email, password, agreed_to_terms: agreedToTerms }),
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
}

export async function createProject(request: ProjectCreateRequest): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function listProjects(): Promise<ProjectListProjection> {
  const res = await fetch(`${API_BASE}/projects`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

export async function getProject(projectId: string): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(await parseAuthError(res));
  }
  return res.json();
}

async function parseDomainPackError(res: Response): Promise<DomainPackError> {
  try {
    const body: { detail?: unknown } = await res.json();
    // 领域包路由返回 {error, message} 对象；认证中间件（401）返回字符串、
    // 校验错误（422）返回数组，统一折叠为可展示的消息。
    if (typeof body.detail === "string" || Array.isArray(body.detail)) {
      return { message: body.detail as string };
    }
    if (body.detail && typeof body.detail === "object") {
      const detail = body.detail as { message?: unknown };
      if (typeof detail.message === "string" && detail.message) {
        return { message: detail.message };
      }
    }
    return { message: `请求失败（${res.status}）` };
  } catch {
    return { message: `请求失败（${res.status}）` };
  }
}

export async function listWorkbenchPacks(): Promise<WorkbenchPackRecord[]> {
  const res = await fetch(`${API_BASE}/domain-packs/workbench`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export type AttestationConclusion = "approve" | "reject" | "needs_changes";

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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export type ConflictDisclosureInput = {
  item_ref: string;
  minority_opinion: string;
  basis?: string[];
  missing_evidence?: string[];
  decision?: string;
};

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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export type ConflictDisclosure = {
  disclosure_id: string;
  pack_id: string;
  pack_version: string;
  item_ref: string;
  minority_opinion: string;
  basis?: string[];
  missing_evidence?: string[];
  decision?: string;
  created_at: string;
};

// ---------------------------------------------------------------------------
// T047：失效、撤销、重验证与受信回滚
// ---------------------------------------------------------------------------

export async function registerSecurityAdmin(): Promise<void> {
  const res = await fetch(`${API_BASE}/domain-packs/security-admins`, {
    method: "POST",
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
}

export async function fetchSecurityAdminStatus(): Promise<{ is_security_admin: boolean }> {
  const res = await fetch(`${API_BASE}/domain-packs/security-admins`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export async function listInvalidations(packId?: string): Promise<PackInvalidationEvent[]> {
  const query = packId ? `?pack_id=${encodeURIComponent(packId)}` : "";
  const res = await fetch(`${API_BASE}/domain-packs/invalidations${query}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export async function getInvalidation(eventId: string): Promise<PackInvalidationEvent> {
  const res = await fetch(`${API_BASE}/domain-packs/invalidations/${encodeURIComponent(eventId)}`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export async function listRevocations(): Promise<RevocationEvent[]> {
  const res = await fetch(`${API_BASE}/domain-packs/revocations`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export async function listRollbacks(): Promise<PackRollbackRecord[]> {
  const res = await fetch(`${API_BASE}/domain-packs/rollbacks`, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
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
  if (!res.ok) throw new Error((await parseDomainPackError(res)).message);
  return res.json();
}

export function statusText(status: HealthStatus): string {
  switch (status) {
    case "pass":
      return "正常";
    case "fail":
      return "异常";
    case "unknown":
      return "未知";
    default:
      return String(status);
  }
}
