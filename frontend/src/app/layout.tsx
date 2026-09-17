import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import "maplibre-gl/dist/maplibre-gl.css";
import { CustomCursor } from "@/components/ui/CustomCursor";
import { LoadingGate } from "@/components/ui/LoadingGate";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Contrail - Rewind the sky",
  description:
    "A real-time airspace digital twin. Rewind the sky, change one thing, watch what happens.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      {/* suppressHydrationWarning: browser extensions (Grammarly, Google
          Translate, etc.) inject attributes like data-gr-ext-installed
          straight onto <body> before React hydrates - a real, well-known
          false positive (https://react.dev/link/hydration-mismatch), not
          an app bug. This only suppresses the warning for attribute
          mismatches on this exact node; it does not hide real hydration
          errors elsewhere in the tree. */}
      <body className="min-h-full flex flex-col" suppressHydrationWarning>
        <CustomCursor />
        <Providers>
          <LoadingGate>{children}</LoadingGate>
        </Providers>
      </body>
    </html>
  );
}
