export type DeploymentVersion = "stable" | "dev";
export type DeploymentStatus =
  | "queued"
  | "initializing"
  | "installing"
  | "deploying"
  | "ready"
  | "terminating"
  | "terminated"
  | "expired"
  | "failed";

export type User = {
  id: string;
  name: string;
  username: string | null;
  avatar_initials: string;
};

export type AuthStatus = {
  authenticated: boolean;
  user: User | null;
  telegram_auth_enabled: boolean;
};

export type RemnawaveConfig = {
  telegram_notifications: {
    enabled: boolean;
    bot_token: string;
    notify_users: string;
    notify_nodes: string;
    notify_crm: string;
    notify_service: string;
    notify_tblocker: string;
  };
  documentation: {
    enabled: boolean;
  };
  webhook: {
    enabled: boolean;
    url: string;
    secret_header: string;
  };
};

export type Session = {
  id: string;
  version: DeploymentVersion;
  provider: "mock" | "digitalocean";
  provider_instance_id: string | null;
  status: DeploymentStatus;
  url: string | null;
  started_at: number;
  ready_at: number;
  expires_at: number;
  progress_percent: number;
  can_terminate: boolean;
};

export type SessionResponse = {
  session: Session | null;
  cooldown_until: number | null;
  can_create_session: boolean;
};

export type Notification = {
  id: string;
  target: "operator" | "user";
  lines: string[];
  created_at: number;
  delivery_status: "stored" | "sent" | "failed";
  error: string | null;
};
