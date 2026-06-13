import type { AuthStatus, Notification, RemnawaveConfig, SessionResponse } from "./types";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });
  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || body?.error || `Request failed with ${response.status}`;
    throw new Error(message);
  }

  return body as T;
}

export const api = {
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  logout: () => request<AuthStatus>("/api/auth/logout", { method: "POST" }),
  activeSession: () => request<SessionResponse>("/api/session"),
  createSession: (payload: { version: "stable" | "dev"; remnawave: RemnawaveConfig }) =>
    request<SessionResponse>("/api/session", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  terminateSession: () => request<SessionResponse>("/api/session", { method: "DELETE" }),
  notifications: () => request<{ notifications: Notification[] }>("/api/notifications"),
};
