import { ActionIcon, Anchor, Badge, Group, Tooltip } from "@mantine/core";
import { Github, Home, LogOut, PanelsTopLeft } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { AuthStatus } from "../types";

export type AppOutletContext = {
  auth: AuthStatus | null;
  setAuth: Dispatch<SetStateAction<AuthStatus | null>>;
};

export function AppShell() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.authStatus().then(setAuth).catch(() => setAuth(null));
  }, []);

  async function handleLogout() {
    await api.logout();
    setAuth({ authenticated: false, telegram_auth_enabled: auth?.telegram_auth_enabled ?? false, user: null });
    navigate("/");
  }

  return (
    <div className="app-frame">
      <header className="topbar">
        <Anchor component={Link} to="/" className="brand" underline="never">
          OpenTry<span>RW</span>
        </Anchor>

        <nav className="nav-tabs" aria-label="Primary navigation">
          <NavLink to="/" end>
            <Home size={16} />
            <span>Home</span>
          </NavLink>
          {auth?.authenticated && (
            <NavLink to="/console">
              <PanelsTopLeft size={16} />
              <span>Console</span>
            </NavLink>
          )}
        </nav>

        <Group gap={8} className="top-actions">
          {auth?.authenticated && auth.user ? (
            <>
              <Badge variant="outline" color="gray">
                {auth.user.name}
              </Badge>
              <Tooltip label="Log out">
                <ActionIcon variant="subtle" color="gray" onClick={handleLogout} aria-label="Log out">
                  <LogOut size={17} />
                </ActionIcon>
              </Tooltip>
            </>
          ) : null}
          <Tooltip label="GitHub">
            <ActionIcon
              component="a"
              href="https://github.com/remnawave"
              target="_blank"
              rel="noreferrer"
              variant="subtle"
              color="gray"
              aria-label="GitHub"
            >
              <Github size={18} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </header>

      <main className="main-surface">
        <Outlet context={{ auth, setAuth } satisfies AppOutletContext} />
      </main>
    </div>
  );
}
