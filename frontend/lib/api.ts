export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8013";

export function token(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("ryze_token");
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  const t = token();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  const res = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  if (res.status === 401) {
    localStorage.removeItem("ryze_token");
    localStorage.removeItem("ryze_user");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // The active domain was revoked by an administrator: the session can no longer be used.
    if (res.status === 403 && typeof body.detail === "string" && body.detail.includes("no longer has access")) {
      localStorage.removeItem("ryze_token");
      localStorage.removeItem("ryze_user");
      localStorage.removeItem("ryze_domain");
      localStorage.removeItem("ryze_role");
      window.location.href = "/login";
    }
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

/** Re-issues the session for another domain; the API refuses domains the user is not authorized for. */
export async function switchDomain(domain: string): Promise<void> {
  const result = await api<{ domain: string; role: string; access_token?: string }>("/auth/switch-domain", {
    method: "POST",
    body: JSON.stringify({ domain }),
  });
  if (result.access_token) localStorage.setItem("ryze_token", result.access_token);
  localStorage.setItem("ryze_domain", result.domain);
  localStorage.setItem("ryze_role", result.role);
}
