"use client";

import { useState } from "react";
import { ConnectionRiskCalculator } from "./connection-risk-calculator";

type View = "estimator" | "how-it-works" | "about";

const navigation: Array<{ id: View; label: string }> = [
  { id: "estimator", label: "Estimator" },
  { id: "how-it-works", label: "How It Works" },
  { id: "about", label: "About" },
];

const methodSections = [
  {
    title: "Overview",
    content: <><p>The estimator compares the scheduled layover with a simulated combination of first-flight arrival delay and passenger time. It reports the share of simulations in which the traveler reaches the connecting flight before the boarding cutoff.</p><p>It is designed for U.S. domestic connections and uses historical performance rather than live operational conditions.</p></>,
  },
  {
    title: "Historical Flight Data",
    content: <><p>Arrival-delay samples come from completed, non-diverted flights in the U.S. Bureau of Transportation Statistics data stored by the project. Cancelled and diverted flights are excluded from the delay evidence.</p><p>The model uses records strictly before the requested travel date, normally from the preceding 24 months. It begins with a narrowly matched cohort based on route, reporting carrier, calendar and departure-time characteristics. If there are fewer than 30 observations, it progressively falls back through broader route, carrier, and global cohorts. Results identify the cohort and sample size actually used.</p></>,
  },
  {
    title: "Itinerary Resolution",
    content: <><p>Flight-number mode asks the configured schedule provider to resolve two marketing flight numbers and a date. The backend validates that the first arrival airport matches the second departure airport and that the schedule forms a valid connection. If a flight number maps to multiple schedules, the traveler selects the matching candidate before calculation.</p><p>The resolved schedule is then adapted to the same estimator used by manual entry. Provider-supplied terminal, gate, aircraft, or operating-carrier details may be displayed for context, but they do not change the probability calculation.</p></>,
  },
  {
    title: "Monte Carlo Simulation",
    content: <><p>For each of 20,000 trials in the current default configuration, the model samples one observed arrival delay from the selected historical cohort, with replacement, and one transfer time from the passenger-time assumption.</p><p>A trial succeeds when the sampled arrival delay plus transfer time fits within the scheduled layover after fixed deplaning time and the boarding cutoff are reserved. A deterministic itinerary-based seed makes repeated estimates for the same inputs reproducible under the same model version.</p></>,
  },
  {
    title: "Passenger-Time Model",
    content: <><p>The current model reserves 20 minutes for deplaning, samples a generic gate-to-gate transfer time from a triangular distribution of 15 / 25 / 40 minutes (minimum / most likely / maximum), and reserves a 15-minute boarding cutoff.</p><p>These are explicit V1 modeling assumptions, not observed passenger-movement measurements. The calculation does not vary them by airport, terminal, gate, walking distance, airport train, or security process.</p></>,
  },
  {
    title: "Sensitivity Analysis",
    content: <><p>Alongside the overall historical estimate, the result shows four controlled arrival scenarios: exactly on time, 15 minutes late, 30 minutes late, and 45 minutes late. Each scenario continues to sample the same generic transfer-time distribution.</p><p>These scenarios help show how the connection changes as arrival delay increases; they are not forecasts that those four delays are equally likely.</p></>,
  },
  {
    title: "Understanding the Results",
    content: <><p>The headline percentage is the simulated success rate, not a guarantee. The result also shows the scheduled layover, historical sample size, median arrival delay, upper delay percentiles, and the cohort used. A broader-cohort warning appears when the model has to rely on route-only, carrier-only, or global history.</p><p>Coverage dates and freshness warnings disclose when the available BTS history ends before the requested travel date.</p></>,
  },
  {
    title: "Limitations",
    content: <><p>The estimator does not use live flight status, weather, airport congestion, connection protection, rebooking policies, passenger mobility, checked-bag constraints, or security-queue information. Historical delay evidence excludes cancellations and diversions, so the result does not represent every way an itinerary can fail.</p><p>Schedule metadata may change. Terminal and gate fields supplied during flight-number resolution are display-only: the transfer model remains a generic assumption and does not calculate airport-specific paths, walking distances, train times, or security times.</p></>,
  },
];

function Header({ view, onNavigate }: { view: View; onNavigate: (view: View) => void }) {
  return <nav className="top-navigation" aria-label="Primary navigation">
    {navigation.map((item) => <button key={item.id} type="button" aria-current={view === item.id ? "page" : undefined} onClick={() => onNavigate(item.id)}>{item.label}</button>)}
  </nav>;
}

function InformationalHero({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <header className="hero informational-hero">
    <div className="brand"><span className="wing-mark" aria-hidden="true" /> Flight Connection Probability</div>
    <div className="hero-copy"><span className="badge">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
  </header>;
}

function HowItWorks() {
  return <main>
    <InformationalHero eyebrow="Methodology" title="How It Works" description="A transparent look at the historical evidence, schedule resolution, and explicit assumptions behind each estimate." />
    <section className="information-panel" aria-labelledby="methodology-title">
      <div className="information-intro"><p className="eyebrow">Model guide</p><h2 id="methodology-title">From itinerary to probability</h2><p>Open each section to see what the current implementation does—and what it deliberately does not model.</p></div>
      <div className="accordion-list">{methodSections.map((section, index) => <details key={section.title} open={index === 0}><summary>{section.title}</summary><div className="accordion-content">{section.content}</div></details>)}</div>
    </section>
    <Footer />
  </main>;
}

function About() {
  return <main>
    <InformationalHero eyebrow="About" title="Decision support, grounded in data" description="An experimental research tool for understanding connection risk—not a promise about a particular trip." />
    <section className="about-panel" aria-labelledby="about-title">
      <div><p className="eyebrow">Purpose</p><h2 id="about-title">A clearer way to reason about a layover</h2></div>
      <div className="about-copy"><p>Flight Connection Probability turns a scheduled itinerary, historical U.S. domestic arrival performance, and stated passenger-time assumptions into an interpretable estimate. Its purpose is to support comparison and planning when a simple layover duration does not tell the whole story.</p><p>The project emphasizes transparency: results expose the historical sample, model cohort, coverage window, delay percentiles, sensitivity scenarios, and passenger-time assumptions. It is not live flight tracking or operational advice, and its estimates should be considered alongside current airline and airport information.</p></div>
    </section>
    <Footer />
  </main>;
}

function Footer() {
  return <footer className="disclaimer"><strong>Experimental research tool.</strong> Results are estimates, not guarantees. Historical delay evidence excludes cancellations and diversions. Real-time conditions and airport-specific passenger movement are not modeled.</footer>;
}

export function SiteShell() {
  const [view, setView] = useState<View>("estimator");
  return <><Header view={view} onNavigate={setView} />{view === "estimator" ? <ConnectionRiskCalculator /> : view === "how-it-works" ? <HowItWorks /> : <About />}</>;
}
