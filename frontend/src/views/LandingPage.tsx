import {
  Badge,
  Button,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from "@mantine/core";
import { motion } from "framer-motion";
import { Bell, Clock3, Rocket, ShieldCheck } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";
import { TelegramButton } from "../components/TelegramButton";
import type { AppOutletContext } from "../shell/AppShell";

const steps = [
  { label: "Telegram auth", icon: ShieldCheck, color: "cyan" },
  { label: "Worker queue", icon: Rocket, color: "violet" },
  { label: "One-hour lab", icon: Clock3, color: "teal" },
];

export function LandingPage() {
  const { auth } = useOutletContext<AppOutletContext>();

  return (
    <section className="landing-screen">
      <div className="hero-copy">
        <Badge variant="light" color="cyan" size="lg">
          Remnawave demo launcher
        </Badge>
        <Title order={1}>OpenTryRW</Title>
        <Text size="xl" c="dimmed" maw={680}>
          Temporary Remnawave instances with Telegram auth, queue-backed provisioning,
          operator notifications, automatic cleanup, and abuse-aware cooldowns.
        </Text>
        <Group gap="sm" mt="xl">
          {auth?.authenticated ? (
            <Button component={Link} to="/console" size="lg">
              Open console
            </Button>
          ) : (
            <TelegramButton size="lg" />
          )}
          <Button component="a" href="/docs" size="lg" variant="light">
            API docs
          </Button>
        </Group>
      </div>

      <Paper className="launch-panel" withBorder>
        <Group justify="space-between" align="flex-start">
          <Stack gap={4}>
            <Text fw={800}>Access policy</Text>
            <Text size="sm" c="dimmed">
              Telegram identity is required before opening the deployment console.
            </Text>
          </Stack>
          <Badge variant="outline" color="gray">
            gated
          </Badge>
        </Group>

        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm" mt="xl">
          {steps.map((step, index) => (
            <motion.div
              key={step.label}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: index * 0.08 }}
            >
              <Paper className="flow-tile" withBorder>
                <ThemeIcon variant="light" color={step.color} size={38}>
                  <step.icon size={19} />
                </ThemeIcon>
                <Text fw={760} size="sm">
                  {step.label}
                </Text>
              </Paper>
            </motion.div>
          ))}
        </SimpleGrid>

        <Paper className="signal-strip" withBorder>
          <Bell size={18} />
          <Text size="sm">
            Operators and users receive delivery updates while the worker owns lifecycle cleanup.
          </Text>
        </Paper>
      </Paper>
    </section>
  );
}
