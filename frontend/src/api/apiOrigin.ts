/** Build-time API origin for static local Compose; blank means same origin. */
export const apiOrigin = (import.meta.env.VITE_API_ORIGIN ?? "").replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${apiOrigin}${path}`;
}
