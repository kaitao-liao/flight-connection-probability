import type { Metadata } from "next";
import { SiteShell } from "./site-shell";

export const metadata: Metadata = {
  title: "Will I Make My Connection?",
  description: "An experimental, transparent flight connection probability estimator.",
};

export default function Home() {
  return <SiteShell />;
}
