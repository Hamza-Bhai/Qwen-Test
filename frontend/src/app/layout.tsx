import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { SiteHeader } from "@/components/site-header";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "YT Growth Intelligence",
  description:
    "Phase 1 — Look up any public YouTube channel and see real subscriber, view and video statistics.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      {/* Browser extensions (e.g. Grammarly) add attributes to <body> before React
          loads; without this React logs a harmless hydration-mismatch error. */}
      <body className={`${inter.className} min-h-screen bg-background`} suppressHydrationWarning>
        <Providers>
          <SiteHeader />
          <main className="mx-auto w-full max-w-6xl px-4 pb-16 sm:px-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
