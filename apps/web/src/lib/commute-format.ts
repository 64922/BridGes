import type { CommutePlace, CommutePlaceCandidate } from "@/lib/api";

export function formatCommuteTime(value: string | null | undefined): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}

export function formatCommuteDistance(meters: number | null | undefined): string {
  if (meters === null || meters === undefined) return "未知";
  if (meters < 1000) return `${meters} 米`;
  return `${(meters / 1000).toFixed(1)} 公里`;
}

export function formatCommuteDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "未知";
  if (seconds < 60) return `${seconds} 秒`;
  const totalMinutes = Math.max(1, Math.round(seconds / 60));
  if (totalMinutes < 60) return `${totalMinutes} 分钟`;
  const hours = Math.floor(totalMinutes / 60);
  const rest = totalMinutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

export function commutePlaceScope(place: Pick<CommutePlace, "campus_verified">): string {
  return place.campus_verified ? "已核实校内或校门" : "未确认校内";
}

export function commuteCandidateScope(
  candidate: Pick<CommutePlaceCandidate, "campus">
): string {
  return candidate.campus ? "已确认校内" : "未确认校内";
}
