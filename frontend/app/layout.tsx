import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FD AI/BI Analytics Platform",
  description:
    "A multi-tenant, multi-business AI analytics platform for clear, trusted decisions.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>{children}</body>
    </html>
  );
}
