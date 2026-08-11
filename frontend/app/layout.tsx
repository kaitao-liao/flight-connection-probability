import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flight Connection Probability",
  description: "A transparent historical flight connection probability estimator.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
