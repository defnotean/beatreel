import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "beatreel — auto highlight reels synced to a beat",
  description:
    "Drop a folder of gameplay clips, drop a song, get a highlight reel back. No editor required.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased noise">{children}</body>
    </html>
  );
}
