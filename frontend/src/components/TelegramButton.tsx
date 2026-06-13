import { Button } from "@mantine/core";

type TelegramButtonProps = {
  disabled?: boolean;
  size?: "sm" | "md" | "lg";
  label?: string;
};

export function TelegramButton({
  disabled = false,
  size = "md",
  label = "Sign in with Telegram",
}: TelegramButtonProps) {
  return (
    <Button
      component="a"
      href="/api/auth/telegram/start"
      className="telegram-button"
      size={size}
      disabled={disabled}
      leftSection={<TelegramIcon />}
    >
      {label}
    </Button>
  );
}

export function TelegramIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="telegram-icon">
      <path d="M21.94 4.68 18.9 19.01c-.23 1.02-.83 1.27-1.68.79l-4.64-3.42-2.24 2.16c-.25.25-.46.46-.94.46l.33-4.73 8.62-7.79c.38-.33-.08-.51-.58-.18L7.12 13.01 2.53 11.57c-1-.31-1.02-1 .21-1.48L20.7 3.17c.83-.31 1.56.19 1.24 1.51Z" />
    </svg>
  );
}
