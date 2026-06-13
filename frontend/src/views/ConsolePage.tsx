import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Divider,
  Group,
  Paper,
  PasswordInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  ThemeIcon,
  Title,
  Tooltip,
} from "@mantine/core";
import { useInterval, useLocalStorage } from "@mantine/hooks";
import {
  BellRing,
  BookOpen,
  CircleAlert,
  Clock3,
  Copy,
  ExternalLink,
  KeyRound,
  Link2,
  Rocket,
  Server,
  Settings2,
  ShieldCheck,
  Trash2,
  Webhook,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Navigate, useOutletContext } from "react-router-dom";
import { api } from "../api";
import type { AppOutletContext } from "../shell/AppShell";
import type { DeploymentVersion, Notification, RemnawaveConfig, SessionResponse } from "../types";

const defaultConfig: RemnawaveConfig = {
  documentation: { enabled: false },
  telegram_notifications: {
    enabled: false,
    bot_token: "",
    notify_users: "",
    notify_nodes: "",
    notify_crm: "",
    notify_service: "",
    notify_tblocker: "",
  },
  webhook: {
    enabled: false,
    url: "",
    secret_header: "",
  },
};

const statusLabel = {
  queued: "Queued",
  initializing: "Initializing",
  installing: "Installing",
  deploying: "Deploying",
  ready: "Ready",
  terminating: "Terminating",
  terminated: "Terminated",
  expired: "Expired",
  failed: "Failed",
};

export function ConsolePage() {
  const { auth, setAuth } = useOutletContext<AppOutletContext>();
  const [sessionState, setSessionState] = useState<SessionResponse | null>(null);
  const [version, setVersion] = useLocalStorage<DeploymentVersion>({
    key: "opentryrw-version",
    defaultValue: "stable",
  });
  const [config, setConfig] = useLocalStorage<RemnawaveConfig>({
    key: "opentryrw-config",
    defaultValue: defaultConfig,
  });
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const poller = useInterval(() => {
    if (auth?.authenticated) refreshSession({ quiet: true });
  }, sessionState?.session && sessionState.session.status !== "ready" ? 3500 : 9000);

  useEffect(() => {
    api.authStatus().then(setAuth).catch(() => setAuth(null));
  }, [setAuth]);

  useEffect(() => {
    if (auth?.authenticated) {
      refreshSession({ quiet: true });
    }
  }, [auth?.authenticated]);

  useEffect(() => {
    if (auth?.authenticated) {
      refreshNotifications();
    }
  }, [auth?.authenticated]);

  useEffect(() => {
    if (auth?.authenticated) poller.start();
    return poller.stop;
  }, [auth?.authenticated, poller]);

  const activeSession = sessionState?.session ?? null;
  const cooldownActive =
    !activeSession && sessionState?.can_create_session === false && Boolean(sessionState.cooldown_until);
  const readyActive = activeSession?.status === "ready";
  const progress = activeSession?.progress_percent ?? 0;
  const cooldownText = useMemo(
    () => formatRemaining(sessionState?.cooldown_until ?? null, now),
    [now, sessionState?.cooldown_until],
  );
  const cooldownClock = useMemo(
    () => formatCountdownClock(sessionState?.cooldown_until ?? null, now),
    [now, sessionState?.cooldown_until],
  );

  useEffect(() => {
    if (!cooldownActive && !readyActive) return undefined;
    const handle = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(handle);
  }, [cooldownActive, readyActive]);

  async function refreshSession(options: { quiet?: boolean } = {}) {
    if (!options.quiet) setBusy(true);
    try {
      const response = await api.activeSession();
      setSessionState(response);
      setError(null);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "Unable to load session";
      if (message !== "Unauthorized") setError(message);
    } finally {
      if (!options.quiet) setBusy(false);
    }
  }

  async function refreshNotifications() {
    try {
      const response = await api.notifications();
      setNotifications(response.notifications.slice(0, 4));
    } catch {
      setNotifications([]);
    }
  }

  async function createSession() {
    setBusy(true);
    setError(null);
    try {
      const response = await api.createSession({ version, remnawave: config });
      setSessionState(response);
      await refreshNotifications();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to create session");
    } finally {
      setBusy(false);
    }
  }

  async function terminateSession() {
    setBusy(true);
    setError(null);
    try {
      const response = await api.terminateSession();
      setSessionState(response);
      await refreshNotifications();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to terminate session");
    } finally {
      setBusy(false);
    }
  }

  function updateConfig(patch: Partial<RemnawaveConfig>) {
    setConfig({ ...config, ...patch });
  }

  function updateTelegram(key: keyof RemnawaveConfig["telegram_notifications"], value: string | boolean) {
    updateConfig({
      telegram_notifications: {
        ...config.telegram_notifications,
        [key]: value,
      },
    });
  }

  function updateWebhook(key: keyof RemnawaveConfig["webhook"], value: string | boolean) {
    updateConfig({
      webhook: {
        ...config.webhook,
        [key]: value,
      },
    });
  }

  if (auth && !auth.authenticated) {
    return <Navigate to="/" replace />;
  }

  if (!auth) {
    return (
      <section className="console-page console-grid-loading">
        <Paper className="status-panel" withBorder>
          <Badge variant="outline" color="gray">
            Checking session
          </Badge>
          <Title order={1}>Deployment console</Title>
          <Text c="dimmed">Authorization state is being verified.</Text>
        </Paper>
      </section>
    );
  }

  if (activeSession?.status === "ready") {
    return (
      <ReadyScreen
        busy={busy}
        remaining={formatCountdownClock(activeSession.expires_at, now)}
        session={activeSession}
        terminateSession={terminateSession}
      />
    );
  }

  if (activeSession) {
    return <ProvisioningScreen progress={progress} session={activeSession} />;
  }

  if (cooldownActive) {
    return <CooldownScreen cooldownClock={cooldownClock} />;
  }

  return (
    <section className="console-page">
      <div className="console-heading">
        <div>
          <Badge variant="light" color="gray">
            No active session
          </Badge>
          <Title order={1}>Deployment console</Title>
          <Text c="dimmed">
            Configure the Remnawave environment, then launch a temporary instance.
          </Text>
        </div>
      </div>

      <div className="console-layout">
        <Stack gap="xl" className="console-main">
          <IdleLaunchPanel
            busy={busy}
            canCreateSession={sessionState?.can_create_session !== false}
            cooldownText={cooldownText}
            createSession={createSession}
            error={error}
            version={version}
          />

          <SettingsPanel
            config={config}
            updateConfig={updateConfig}
            updateTelegram={updateTelegram}
            updateWebhook={updateWebhook}
            version={version}
            setVersion={setVersion}
          />
        </Stack>

        <aside className="console-aside">
          <NotificationsPanel notifications={notifications} />
        </aside>
      </div>
    </section>
  );
}

function IdleLaunchPanel({
  busy,
  canCreateSession,
  cooldownText,
  createSession,
  error,
  version,
}: {
  busy: boolean;
  canCreateSession: boolean;
  cooldownText: string | null;
  createSession: () => Promise<void>;
  error: string | null;
  version: DeploymentVersion;
}) {
  return (
    <Paper className="status-panel launch-ready-panel" withBorder>
      <div className="launch-ready-layout">
        <div>
          <div className="panel-kicker">
            <Server size={16} />
            Ready to provision
          </div>
          <Title order={2}>Prepare a Remnawave demo instance</Title>
          <Text c="dimmed" maw={620}>
            Choose the build and environment options below. The provisioning progress appears here
            only after the launch request is accepted.
          </Text>
        </div>
        <ThemeIcon size={56} radius={8} variant="light" color="cyan">
          <Rocket size={28} />
        </ThemeIcon>
      </div>

      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md" mt="xl">
        <Metric label="Version" value={version === "stable" ? "Stable" : "Dev"} icon={<Settings2 size={18} />} />
        <Metric label="TTL" value="1 hour" icon={<Clock3 size={18} />} />
        <Metric label="Cooldown" value={cooldownText ?? "Open"} icon={<KeyRound size={18} />} />
      </SimpleGrid>

      {error && (
        <Alert color="red" variant="light" icon={<CircleAlert size={18} />} mt="lg">
          {error}
        </Alert>
      )}

      <Group mt="xl">
        <Button
          size="md"
          leftSection={<Rocket size={18} />}
          onClick={createSession}
          loading={busy}
          disabled={!canCreateSession}
        >
          Launch instance
        </Button>
      </Group>
    </Paper>
  );
}

function ProvisioningScreen({
  session,
  progress,
}: {
  session: NonNullable<SessionResponse["session"]>;
  progress: number;
}) {
  const stage = activeStage(session.status);
  return (
    <section className="provisioning-screen" aria-live="polite">
      <div className="provisioning-pulse" aria-hidden="true">
        <span />
      </div>
      <Title order={1}>Setting Up Your Instance</Title>
      <Text className="provisioning-copy">
        We're provisioning your VPS and configuring <span>Remnawave</span>.
        <br />
        This usually takes 3-5 minutes.
      </Text>
      <StageWordProgress progress={progress} stage={stage} />
      <Text className="provisioning-note">Please wait, this page will update automatically...</Text>
    </section>
  );
}

function ReadyScreen({
  busy,
  remaining,
  session,
  terminateSession,
}: {
  busy: boolean;
  remaining: string;
  session: NonNullable<SessionResponse["session"]>;
  terminateSession: () => Promise<void>;
}) {
  return (
    <section className="ready-screen" aria-live="polite">
      <div className="ready-icon" aria-hidden="true">
        <Rocket size={42} />
      </div>
      <Title order={1}>Your Instance is Ready!</Title>
      <Text className="ready-copy">
        Your Remnawave instance is fully deployed and ready
        <br />
        to use.
        <br />
        Click the button below to access it.
      </Text>
      {session.url && (
        <Button
          component="a"
          href={session.url}
          target="_blank"
          rel="noreferrer"
          className="open-instance-button"
          leftSection={<ExternalLink size={18} />}
        >
          Open Remnawave
        </Button>
      )}
      <div className="ready-timer-card">
        <Text className="ready-timer-label">
          <Clock3 size={16} />
          Time remaining
        </Text>
        <div className="ready-time">{remaining}</div>
        <Text className="ready-timer-note">
          Your instance will be automatically terminated when the timer reaches zero
        </Text>
      </div>
      <Button
        className="terminate-instance-button"
        color="red"
        variant="filled"
        leftSection={<Trash2 size={18} />}
        onClick={terminateSession}
        loading={busy}
        disabled={!session.can_terminate}
      >
        Terminate instance
      </Button>
    </section>
  );
}

function CooldownScreen({ cooldownClock }: { cooldownClock: string }) {
  return (
    <section className="cooldown-screen" aria-live="polite">
      <div className="cooldown-icon" aria-hidden="true">
        <Clock3 size={42} />
      </div>
      <Title order={1}>Come Back Later</Title>
      <Text className="cooldown-copy">
        You've already used your trial session in the last <span>24 hours</span>.
        <br />
        You can request a new session once the cooldown expires.
      </Text>
      <div className="cooldown-card">
        <Text className="cooldown-card-label">Next available slot in</Text>
        <div className="cooldown-time">{cooldownClock}</div>
      </div>
    </section>
  );
}

function SettingsPanel({
  config,
  setVersion,
  updateConfig,
  updateTelegram,
  updateWebhook,
  version,
}: {
  config: RemnawaveConfig;
  setVersion: (value: DeploymentVersion) => void;
  updateConfig: (patch: Partial<RemnawaveConfig>) => void;
  updateTelegram: (key: keyof RemnawaveConfig["telegram_notifications"], value: string | boolean) => void;
  updateWebhook: (key: keyof RemnawaveConfig["webhook"], value: string | boolean) => void;
  version: DeploymentVersion;
}) {
  return (
    <Paper className="config-panel" withBorder>
      <Group justify="space-between" align="flex-start" className="settings-header">
        <div>
          <Text fw={820} size="lg">Remnawave settings</Text>
          <Text size="sm" c="dimmed" mt={4}>
            These values are rendered into the temporary instance before deployment.
          </Text>
        </div>
        <SegmentedControl
          value={version}
          onChange={(value) => setVersion(value as DeploymentVersion)}
          data={[
            { label: "Stable", value: "stable" },
            { label: "Dev", value: "dev" },
          ]}
        />
      </Group>

      <Divider />

      <Stack gap="xl">
        <section className="settings-section">
          <div className="settings-section-head">
            <div className="settings-title">
              <BookOpen size={18} />
              <div>
                <Text fw={780}>Documentation</Text>
                <Text size="sm" c="dimmed">Expose Remnawave API reference pages in the demo.</Text>
              </div>
            </div>
            <Switch
              checked={config.documentation.enabled}
              onChange={(event) =>
                updateConfig({ documentation: { enabled: event.currentTarget.checked } })
              }
            />
          </div>
          <Checkbox
            checked={config.documentation.enabled}
            label="Expose /docs and /scalar in the demo instance"
            onChange={(event) =>
              updateConfig({ documentation: { enabled: event.currentTarget.checked } })
            }
          />
        </section>

        <section className="settings-section">
          <div className="settings-section-head">
            <div className="settings-title">
              <BellRing size={18} />
              <div>
                <Text fw={780}>Telegram notifications</Text>
                <Text size="sm" c="dimmed">Instance-side notification targets for Remnawave.</Text>
              </div>
            </div>
            <Switch
              checked={config.telegram_notifications.enabled}
              onChange={(event) => updateTelegram("enabled", event.currentTarget.checked)}
            />
          </div>
          <PasswordInput
            label="Bot token"
            placeholder="123456:ABC..."
            value={config.telegram_notifications.bot_token}
            onChange={(event) => updateTelegram("bot_token", event.currentTarget.value)}
            disabled={!config.telegram_notifications.enabled}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput label="Notify users" value={config.telegram_notifications.notify_users} onChange={(event) => updateTelegram("notify_users", event.currentTarget.value)} disabled={!config.telegram_notifications.enabled} />
            <TextInput label="Notify nodes" value={config.telegram_notifications.notify_nodes} onChange={(event) => updateTelegram("notify_nodes", event.currentTarget.value)} disabled={!config.telegram_notifications.enabled} />
            <TextInput label="Notify CRM" value={config.telegram_notifications.notify_crm} onChange={(event) => updateTelegram("notify_crm", event.currentTarget.value)} disabled={!config.telegram_notifications.enabled} />
            <TextInput label="Notify service" value={config.telegram_notifications.notify_service} onChange={(event) => updateTelegram("notify_service", event.currentTarget.value)} disabled={!config.telegram_notifications.enabled} />
          </SimpleGrid>
          <TextInput
            label="Notify TBlocker"
            value={config.telegram_notifications.notify_tblocker}
            onChange={(event) => updateTelegram("notify_tblocker", event.currentTarget.value)}
            disabled={!config.telegram_notifications.enabled}
          />
        </section>

        <section className="settings-section">
          <div className="settings-section-head">
            <div className="settings-title">
              <Webhook size={18} />
              <div>
                <Text fw={780}>Webhook</Text>
                <Text size="sm" c="dimmed">Optional outbound webhook for Remnawave events.</Text>
              </div>
            </div>
            <Switch
              checked={config.webhook.enabled}
              onChange={(event) => updateWebhook("enabled", event.currentTarget.checked)}
            />
          </div>
          <TextInput
            label="Webhook URL"
            placeholder="https://example.com/remnawave"
            leftSection={<Link2 size={16} />}
            value={config.webhook.url}
            onChange={(event) => updateWebhook("url", event.currentTarget.value)}
            disabled={!config.webhook.enabled}
          />
          <PasswordInput
            label="Secret header"
            placeholder="At least 32 alphanumeric characters"
            value={config.webhook.secret_header}
            onChange={(event) => updateWebhook("secret_header", event.currentTarget.value)}
            disabled={!config.webhook.enabled}
          />
        </section>
      </Stack>
    </Paper>
  );
}

function ReadyCallout({ url }: { url: string }) {
  return (
    <Paper className="ready-callout" withBorder>
      <Stack gap={4}>
        <Text size="sm" c="dimmed">
          Access URL
        </Text>
        <Group gap="xs">
          <Text fw={780}>{url}</Text>
          <Tooltip label="Copy URL">
            <ActionIcon
              variant="light"
              color="gray"
              aria-label="Copy URL"
              onClick={() => navigator.clipboard.writeText(url)}
            >
              <Copy size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Open URL">
            <ActionIcon
              component="a"
              href={url}
              target="_blank"
              rel="noreferrer"
              variant="light"
              aria-label="Open URL"
            >
              <ExternalLink size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Stack>
    </Paper>
  );
}

function NotificationsPanel({ notifications }: { notifications: Notification[] }) {
  return (
    <Paper className="config-panel" withBorder>
      <Group justify="space-between">
        <Text fw={820}>Recent delivery events</Text>
        <Badge variant="light">{notifications.length}</Badge>
      </Group>
      <Stack gap="xs" mt="md">
        {notifications.length ? (
          notifications.map((item) => (
            <Paper key={item.id} className="event-row" withBorder>
              <Badge size="sm" color={item.delivery_status === "failed" ? "red" : "teal"}>
                {item.delivery_status}
              </Badge>
              <Text size="sm">{item.lines.join(" ")}</Text>
            </Paper>
          ))
        ) : (
          <Text size="sm" c="dimmed">
            No delivery events yet.
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

function StageWordProgress({
  progress,
  stage,
}: {
  progress: number;
  stage: "initializing" | "installing" | "deploying" | "ready";
}) {
  const word = stage === "ready" ? "READY" : stage.toUpperCase();
  const fill = stage === "ready" ? 100 : Math.max(0, Math.min(100, progress));

  return (
    <div
      className="stage-word-progress"
      data-text={word}
      style={{ "--stage-fill": `${fill}%` } as React.CSSProperties}
    >
      {word}
    </div>
  );
}

function activeStage(status: string): "initializing" | "installing" | "deploying" | "ready" {
  if (status === "ready") return "ready";
  if (status === "installing" || status === "deploying") return status;
  return "initializing";
}

function Metric({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <Paper className="metric" withBorder>
      <ThemeIcon variant="light" color="cyan">
        {icon}
      </ThemeIcon>
      <Stack gap={0}>
        <Text size="xs" c="dimmed">
          {label}
        </Text>
        <Text fw={820}>{value}</Text>
      </Stack>
    </Paper>
  );
}

function formatRemaining(timestamp: number | null, nowMs = Date.now()) {
  if (!timestamp) return null;
  const seconds = remainingSeconds(timestamp, nowMs);
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes <= 0) return `${rest}s`;
  return `${minutes}m ${rest.toString().padStart(2, "0")}s`;
}

function formatCountdownClock(timestamp: number | null, nowMs = Date.now()) {
  const seconds = timestamp ? remainingSeconds(timestamp, nowMs) : 0;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return [hours, minutes, rest].map((value) => value.toString().padStart(2, "0")).join(":");
}

function remainingSeconds(timestamp: number, nowMs: number) {
  const timestampMs = timestamp > 10_000_000_000 ? timestamp : timestamp * 1000;
  return Math.max(0, Math.floor((timestampMs - nowMs) / 1000));
}
