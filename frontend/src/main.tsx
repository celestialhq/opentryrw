import "@mantine/core/styles.css";
import "./styles.css";

import { MantineProvider, createTheme } from "@mantine/core";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "./shell/AppShell";
import { ConsolePage } from "./views/ConsolePage";
import { LandingPage } from "./views/LandingPage";

const theme = createTheme({
  primaryColor: "cyan",
  fontFamily:
    "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
  defaultRadius: 8,
  headings: {
    fontFamily:
      "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    fontWeight: "820",
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <LandingPage /> },
      { path: "console", element: <ConsolePage /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider defaultColorScheme="dark" theme={theme}>
      <RouterProvider router={router} />
    </MantineProvider>
  </StrictMode>,
);
