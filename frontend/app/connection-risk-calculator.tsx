"use client";

import { FormEvent, useState } from "react";
import {
  ApiError,
  ConnectionRiskResponse,
  estimateConnectionRisk,
  ItineraryRequest,
} from "./api-client";

type FormErrors = Partial<Record<keyof ItineraryRequest, string>>;

const initialForm: ItineraryRequest = {
  carrier: "DL",
  origin: "ATL",
  connection: "JFK",
  destination: "BOS",
  travel_date: "",
  first_departure_time: "15:30",
  first_arrival_time: "17:45",
  connecting_departure_time: "19:10",
};

const airportFields: Array<{ key: "origin" | "connection" | "destination"; label: string }> = [
  { key: "origin", label: "Origin airport" },
  { key: "connection", label: "Connection airport" },
  { key: "destination", label: "Final destination" },
];

const scenarioLabels: Record<keyof ConnectionRiskResponse["scenarios"], string> = {
  on_time: "On time",
  delay_15: "+15 min",
  delay_30: "+30 min",
  delay_45: "+45 min",
};

const broadFallbackCohorts = new Set(["route", "carrier", "global"]);

export function normalizeCode(value: string, maxLength: number): string {
  return value.replace(/[^a-z]/gi, "").toUpperCase().slice(0, maxLength);
}

export function formatDelay(value: number): string {
  if (value === 0) return "On time";
  const rounded = Math.round(Math.abs(value));
  return value < 0 ? `${rounded} min early` : `${rounded} min late`;
}

function validate(form: ItineraryRequest): FormErrors {
  const errors: FormErrors = {};
  if (!/^[A-Z0-9]{2,3}$/.test(form.carrier)) errors.carrier = "Use a 2–3 character carrier code.";
  for (const { key } of airportFields) {
    if (!/^[A-Z]{3}$/.test(form[key])) errors[key] = "Use a 3-letter U.S. airport code.";
  }
  if (form.origin && form.connection && form.origin === form.connection) errors.connection = "Connection must differ from origin.";
  if (form.connection && form.destination && form.connection === form.destination) errors.destination = "Destination must differ from connection.";
  if (!form.travel_date) errors.travel_date = "Choose a travel date.";
  for (const key of ["first_departure_time", "first_arrival_time", "connecting_departure_time"] as const) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(form[key])) errors[key] = "Enter a valid local time.";
  }
  return errors;
}

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? <span id={id} className="field-error">{message}</span> : null;
}

function ResultPanel({ result }: { result: ConnectionRiskResponse }) {
  const broadFallback = broadFallbackCohorts.has(result.model.cohort_level.toLowerCase());
  const probability = (result.connection_probability * 100).toFixed(1);

  return (
    <section className="results" aria-live="polite" aria-labelledby="result-title">
      <div className="probability-card">
        <div>
          <p className="eyebrow">Estimated result</p>
          <h2 id="result-title">Probability of making the connection</h2>
        </div>
        <div className="probability" aria-label={`${probability} percent`}>{probability}<span>%</span></div>
      </div>

      {broadFallback && (
        <div className="warning" role="status">
          <strong>Broader historical comparison used.</strong> The exact route cohort was too small, so this estimate relies on a fallback cohort.
        </div>
      )}

      {result.model.historical_coverage.freshness_warning && (
        <div className="warning" role="status">
          <strong>Historical data coverage notice.</strong>{" "}
          {result.model.historical_coverage.freshness_warning}
        </div>
      )}

      <div className="stat-grid">
        <article><span>Scheduled layover</span><strong>{result.scheduled_layover_minutes} min</strong><small>{result.overnight_connection ? "Overnight connection" : "Same-day connection"}</small></article>
        <article><span>Historical sample</span><strong>{result.historical_sample_size.toLocaleString()}</strong><small>Completed, non-diverted flights</small></article>
        <article><span>Median arrival</span><strong>{formatDelay(result.delay_statistics.median_minutes)}</strong><small>50th percentile</small></article>
        <article><span>75th percentile</span><strong>{formatDelay(result.delay_statistics.p75_minutes)}</strong><small>Observed arrival delay</small></article>
        <article><span>90th percentile</span><strong>{formatDelay(result.delay_statistics.p90_minutes)}</strong><small>Observed arrival delay</small></article>
      </div>

      <section className="scenario-card" aria-labelledby="scenario-title">
        <div className="section-heading">
          <div><p className="eyebrow">Sensitivity check</p><h3 id="scenario-title">First-flight arrival scenarios</h3></div>
          <p>How a fixed arrival delay changes the simulated probability.</p>
        </div>
        <div className="scenario-list">
          {(Object.keys(result.scenarios) as Array<keyof typeof result.scenarios>).map((key) => {
            const value = result.scenarios[key];
            return <div className="scenario" key={key}>
              <div><span>{scenarioLabels[key]}</span><strong>{(value * 100).toFixed(1)}%</strong></div>
              <div className="bar" aria-hidden="true"><span style={{ width: `${value * 100}%` }} /></div>
            </div>;
          })}
        </div>
      </section>

      <details className="method-card">
        <summary>How this estimate was generated</summary>
        <div className="method-content">
          <p>The arrival-delay distribution comes from observed BTS flights in the selected historical cohort. Transfer time and boarding cutoff are explicit simulation assumptions—not measured airport-specific data.</p>
          <dl>
            <div><dt>Model version</dt><dd>{result.model.version}</dd></div>
            <div><dt>Historical cohort</dt><dd>{result.model.cohort_level}</dd></div>
            <div><dt>Historical window</dt><dd>Previous {result.model.historical_coverage.lookback_months} months; records strictly before {result.model.historical_coverage.strict_cutoff_exclusive}</dd></div>
            <div><dt>Available BTS coverage</dt><dd>{result.model.historical_coverage.available_start_date} through {result.model.historical_coverage.available_end_date}</dd></div>
            <div><dt>Effective history</dt><dd>{result.model.historical_coverage.effective_history_start_date} through {result.model.historical_coverage.effective_history_end_date}</dd></div>
            <div><dt>Transfer-time assumption</dt><dd>Triangular: {result.model.transfer_time.minimum_minutes} / {result.model.transfer_time.mode_minutes} / {result.model.transfer_time.maximum_minutes} min (min / mode / max)</dd></div>
            <div><dt>Boarding cutoff</dt><dd>{result.model.boarding_cutoff_minutes} min before departure</dd></div>
            <div><dt>Simulations</dt><dd>{result.model.simulation_count.toLocaleString()}</dd></div>
            <div><dt>Excluded events</dt><dd>{result.model.exclusions.join(", ")}</dd></div>
          </dl>
        </div>
      </details>
    </section>
  );
}

export function ConnectionRiskCalculator() {
  const [form, setForm] = useState(initialForm);
  const [errors, setErrors] = useState<FormErrors>({});
  const [result, setResult] = useState<ConnectionRiskResponse | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function update(key: keyof ItineraryRequest, value: string) {
    const normalized = key === "carrier" ? normalizeCode(value, 3) :
      airportFields.some((field) => field.key === key) ? normalizeCode(value, 3) : value;
    setForm((current) => ({ ...current, [key]: normalized }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextErrors = validate(form);
    setErrors(nextErrors);
    setApiError(null);
    if (Object.keys(nextErrors).length) return;
    setLoading(true);
    try {
      setResult(await estimateConnectionRisk(form));
    } catch (error) {
      setResult(null);
      setApiError(error instanceof ApiError ? error.message : "An unexpected error occurred.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <header className="hero">
        <div className="brand"><span className="wing-mark" aria-hidden="true" /> Flight Connection Probability</div>
        <div className="hero-copy">
          <span className="badge">Experimental</span>
          <h1>Will I Make My Connection?</h1>
          <p>Estimate a U.S. domestic connection using historical arrival delays and a transparent transfer-time simulation.</p>
        </div>
      </header>

      <div className="page-grid">
        <section className="form-card" aria-labelledby="itinerary-title">
          <div className="section-heading"><div><p className="eyebrow">Your itinerary</p><h2 id="itinerary-title">Flight details</h2></div><p>All times are local airport clock times.</p></div>
          <form onSubmit={submit} noValidate>
            <div className="route-grid">
              {airportFields.map(({ key, label }) => <label key={key}>{label}<input aria-invalid={Boolean(errors[key])} aria-describedby={`${key}-error`} value={form[key]} onChange={(e) => update(key, e.target.value)} placeholder="JFK" autoComplete="off" /><FieldError id={`${key}-error`} message={errors[key]} /></label>)}
            </div>
            <div className="detail-grid">
              <label>Carrier<input aria-invalid={Boolean(errors.carrier)} aria-describedby="carrier-error" value={form.carrier} onChange={(e) => update("carrier", e.target.value)} placeholder="DL" autoComplete="off" /><FieldError id="carrier-error" message={errors.carrier} /></label>
              <label>Travel date<input type="date" aria-invalid={Boolean(errors.travel_date)} aria-describedby="date-error" value={form.travel_date} onChange={(e) => update("travel_date", e.target.value)} /><FieldError id="date-error" message={errors.travel_date} /></label>
            </div>
            <div className="time-grid">
              <label>First flight departure<input type="time" aria-invalid={Boolean(errors.first_departure_time)} aria-describedby="departure-error" value={form.first_departure_time} onChange={(e) => update("first_departure_time", e.target.value)} /><FieldError id="departure-error" message={errors.first_departure_time} /></label>
              <label>First flight arrival<input type="time" aria-invalid={Boolean(errors.first_arrival_time)} aria-describedby="arrival-error" value={form.first_arrival_time} onChange={(e) => update("first_arrival_time", e.target.value)} /><FieldError id="arrival-error" message={errors.first_arrival_time} /></label>
              <label>Connecting departure<input type="time" aria-invalid={Boolean(errors.connecting_departure_time)} aria-describedby="connection-time-error" value={form.connecting_departure_time} onChange={(e) => update("connecting_departure_time", e.target.value)} /><FieldError id="connection-time-error" message={errors.connecting_departure_time} /></label>
            </div>
            {apiError && <div className="error-banner" role="alert"><strong>Estimate unavailable.</strong> {apiError}</div>}
            <button type="submit" disabled={loading}>{loading ? <><span className="spinner" aria-hidden="true" />Calculating probability…</> : "Calculate connection probability"}</button>
          </form>
        </section>

        {result ? <ResultPanel result={result} /> : <aside className="empty-state"><div className="clock" aria-hidden="true"><span /></div><h2>Your estimate will appear here</h2><p>Enter the scheduled itinerary to compare the layover against historical delays and simulated transfer times.</p></aside>}
      </div>

      <footer className="disclaimer"><strong>Experimental research tool.</strong> Results are estimates, not guarantees. Cancellations, diversions, real-time conditions, gate assignments, and airport-specific walking times are not currently modeled.</footer>
    </main>
  );
}
