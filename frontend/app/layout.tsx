import type { Metadata } from "next";
import "@fontsource-variable/instrument-sans";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mira | Perfect Saturday",
  description: "Plan a Saturday in India around your mood, time, and budget. Chat by voice or text.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en-IN"><body>{children}</body></html>;
}
