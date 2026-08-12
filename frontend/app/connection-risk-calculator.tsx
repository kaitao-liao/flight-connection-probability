"use client";

import { FormEvent, useRef, useState } from "react";
import {
  ApiError, ConnectionRiskResponse, estimateConnectionRisk,
  estimateConnectionRiskByFlightNumber, FlightNumberRequest, ItineraryRequest,
  ResolvedFlight, V2ConnectionResponse,
} from "./api-client";
import { AirportCombobox, supportedAirportCodes } from "./airport-combobox";
import { CarrierCombobox, supportedCarrierCodes } from "./carrier-combobox";

type Mode = "flight-number" | "manual";
type ManualErrors = Partial<Record<keyof ItineraryRequest, string>>;
type FlightErrors = Partial<Record<keyof FlightNumberRequest, string>>;

const initialManualForm: ItineraryRequest = {
  carrier: "DL", origin: "ATL", connection: "JFK", destination: "BOS", travel_date: "",
  first_departure_time: "15:30", first_arrival_time: "17:45", connecting_departure_time: "19:10",
};
const initialFlightForm: FlightNumberRequest = { first_flight_number: "", second_flight_number: "", travel_date: "" };
const airportFields: Array<{ key: "origin" | "connection" | "destination"; label: string }> = [
  { key: "origin", label: "Origin airport" }, { key: "connection", label: "Connection airport" },
  { key: "destination", label: "Final destination" },
];
const scenarioLabels: Record<keyof ConnectionRiskResponse["scenarios"], string> = {
  on_time: "On time", delay_15: "+15 min", delay_30: "+30 min", delay_45: "+45 min",
};
const broadFallbackCohorts = new Set(["route", "carrier", "global"]);

export function formatDelay(value: number): string {
  if (value === 0) return "On time";
  const rounded = Math.round(Math.abs(value));
  return value < 0 ? `${rounded} min early` : `${rounded} min late`;
}

function validateManual(form: ItineraryRequest): ManualErrors {
  const errors: ManualErrors = {};
  if (!supportedCarrierCodes.has(form.carrier)) errors.carrier = "Select a supported carrier from the list.";
  for (const { key } of airportFields) if (!supportedAirportCodes.has(form[key])) errors[key] = "Select a supported airport from the list.";
  if (form.origin && form.connection && form.origin === form.connection) errors.connection = "Connection must differ from origin.";
  if (form.connection && form.destination && form.connection === form.destination) errors.destination = "Destination must differ from connection.";
  if (!form.travel_date) errors.travel_date = "Choose a travel date.";
  for (const key of ["first_departure_time", "first_arrival_time", "connecting_departure_time"] as const) {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(form[key])) errors[key] = "Enter a valid local time.";
  }
  return errors;
}

function normalizeFlightNumber(value: string): string { return value.replace(/\s+/g, "").toUpperCase(); }
function validateFlight(form: FlightNumberRequest): FlightErrors {
  const errors: FlightErrors = {};
  for (const key of ["first_flight_number", "second_flight_number"] as const) {
    if (!/^[A-Z0-9]{3,8}$/.test(normalizeFlightNumber(form[key]))) errors[key] = "Enter a valid carrier and flight number, such as DL1575.";
  }
  if (!form.travel_date) errors.travel_date = "Choose a travel date.";
  return errors;
}

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? <span id={id} className="field-error">{message}</span> : null;
}

function formatScheduleTime(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?([+-]\d{2}:\d{2}|Z)$/.exec(value);
  if (!match) return value;
  const [, year, month, day, hour, minute, offset] = match;
  const localClock = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute)));
  const formatted = new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC",
  }).format(localClock);
  return `${formatted} (${offset === "Z" ? "UTC" : `UTC${offset}`})`;
}

function FlightSchedule({ label, flight }: { label: string; flight: ResolvedFlight }) {
  const metadata = [
    flight.departure_terminal && `Departure terminal ${flight.departure_terminal}`,
    flight.departure_gate && `gate ${flight.departure_gate}`,
    flight.arrival_terminal && `Arrival terminal ${flight.arrival_terminal}`,
    flight.arrival_gate && `gate ${flight.arrival_gate}`,
    flight.aircraft_type && `Aircraft ${flight.aircraft_type}`,
  ].filter(Boolean);
  return <article className="flight-schedule">
    <p className="eyebrow">{label}</p>
    <h3>{flight.marketing_flight_number}: {flight.origin} → {flight.destination}</h3>
    <dl>
      <div><dt>Departs</dt><dd>{formatScheduleTime(flight.scheduled_departure)}</dd></div>
      <div><dt>Arrives</dt><dd>{formatScheduleTime(flight.scheduled_arrival)}</dd></div>
    </dl>
    {metadata.length > 0 && <p className="flight-metadata">{metadata.join(" · ")}</p>}
    {flight.operating_carrier && <p className="flight-metadata">Operated by {flight.operating_carrier}{flight.operating_flight_number ? ` ${flight.operating_flight_number}` : ""}</p>}
  </article>;
}

function ResolvedSchedule({ response }: { response: V2ConnectionResponse }) {
  if (!response.itinerary) return null;
  return <section className="schedule-card" aria-labelledby="resolved-schedule-title">
    <div><p className="eyebrow">Resolved schedule</p><h2 id="resolved-schedule-title">Your flights</h2></div>
    <div className="flight-schedule-grid">
      <FlightSchedule label="First flight" flight={response.itinerary.first_flight} />
      <FlightSchedule label="Connecting flight" flight={response.itinerary.second_flight} />
    </div>
    <p className="schedule-note">Connection at <strong>{response.itinerary.connection_airport}</strong> · Times shown with the provider&apos;s airport time-zone offsets.</p>
  </section>;
}

function ResultPanel({ result, v2 }: { result: ConnectionRiskResponse; v2?: V2ConnectionResponse }) {
  const broadFallback = broadFallbackCohorts.has(result.model.cohort_level.toLowerCase());
  const probability = (result.connection_probability * 100).toFixed(1);
  return <section className="results" aria-live="polite" aria-labelledby="result-title">
    {v2 && <ResolvedSchedule response={v2} />}
    <div className="probability-card"><div><p className="eyebrow">Estimated result</p><h2 id="result-title">Probability of making the connection</h2><p className="result-context">Estimated from historical BTS arrival performance and explicit V1 passenger-time assumptions.</p></div><div className="probability" aria-label={`${probability} percent`}>{probability}<span>%</span></div></div>
    {v2?.warnings.map((warning) => <div className="warning" role="status" key={warning}>{warning}</div>)}
    {broadFallback && <div className="warning" role="status"><strong>Broader historical comparison used.</strong> The exact route cohort was too small, so this estimate relies on a fallback cohort.</div>}
    {result.model.historical_coverage.freshness_warning && <div className="warning" role="status"><strong>Historical data only — not live flight data.</strong>{" "}This {result.model.historical_coverage.requested_prediction_date} estimate uses BTS performance records only through {result.model.historical_coverage.available_end_date}.</div>}
    <div className="stat-grid">
      <article><span>Scheduled layover</span><strong>{result.scheduled_layover_minutes} min</strong><small>{result.overnight_connection ? "Overnight connection" : "Same-day connection"}</small></article>
      <article><span>Historical sample</span><strong>{result.historical_sample_size.toLocaleString()}</strong><small>Completed, non-diverted flights</small></article>
      <article><span>Median arrival</span><strong>{formatDelay(result.delay_statistics.median_minutes)}</strong><small>50th percentile</small></article>
      <article><span>75th percentile</span><strong>{formatDelay(result.delay_statistics.p75_minutes)}</strong><small>Observed arrival delay</small></article>
      <article><span>90th percentile</span><strong>{formatDelay(result.delay_statistics.p90_minutes)}</strong><small>Observed arrival delay</small></article>
    </div>
    <section className="scenario-card" aria-labelledby="scenario-title"><div className="section-heading"><div><p className="eyebrow">Sensitivity check</p><h3 id="scenario-title">First-flight arrival scenarios</h3></div><p>The overall estimate includes historically early arrivals. These scenarios instead assume the first flight arrives exactly on time or exactly 15, 30, or 45 minutes late; gate-to-gate transfer time is still simulated.</p></div><div className="scenario-list">{(Object.keys(result.scenarios) as Array<keyof typeof result.scenarios>).map((key) => { const value = result.scenarios[key]; return <div className="scenario" key={key}><div><span>{scenarioLabels[key]}</span><strong>{(value * 100).toFixed(1)}%</strong></div><div className="bar" aria-hidden="true"><span style={{ width: `${value * 100}%` }} /></div></div>; })}</div></section>
    <details className="method-card"><summary>How this estimate was generated</summary><div className="method-content"><p>The arrival-delay distribution comes from observed BTS flights in the selected historical cohort. Deplaning, gate transfer, and boarding cutoff are V1 assumptions—not measured passenger-movement data.</p><dl>
      <div><dt>Model version</dt><dd>{result.model.version}</dd></div><div><dt>Historical cohort</dt><dd>{result.model.cohort_level}</dd></div>
      <div><dt>Historical window</dt><dd>Previous {result.model.historical_coverage.lookback_months} months; records strictly before {result.model.historical_coverage.strict_cutoff_exclusive}</dd></div>
      <div><dt>Available BTS coverage</dt><dd>{result.model.historical_coverage.available_start_date} through {result.model.historical_coverage.available_end_date}</dd></div>
      <div><dt>Effective history</dt><dd>{result.model.historical_coverage.effective_history_start_date} through {result.model.historical_coverage.effective_history_end_date}</dd></div>
      <div><dt>Deplaning assumption</dt><dd>{result.model.deplaning_time.fixed_minutes} min fixed</dd></div><div><dt>Gate-transfer assumption</dt><dd>Triangular: {result.model.transfer_time.minimum_minutes} / {result.model.transfer_time.mode_minutes} / {result.model.transfer_time.maximum_minutes} min (min / mode / max)</dd></div>
      <div><dt>Boarding cutoff</dt><dd>{result.model.boarding_cutoff_minutes} min before departure</dd></div><div><dt>Simulations</dt><dd>{result.model.simulation_count.toLocaleString()}</dd></div><div><dt>Excluded events</dt><dd>{result.model.exclusions.join(", ")}</dd></div>
    </dl></div></details>
  </section>;
}

const statusMessages: Record<Exclude<V2ConnectionResponse["status"], "success" | "ambiguous">, string> = {
  schedule_not_found: "No matching schedule was found for the selected flight and date.",
  invalid_connection_airport: "The first flight does not arrive at the airport where the second flight departs.",
  invalid_chronology: "The resolved flight times do not form a valid connection.",
  provider_data_quality_error: "The schedule provider returned incomplete required flight times.",
  provider_temporarily_unavailable: "The flight schedule provider is temporarily unavailable. Try again later.",
  provider_configuration_error: "Flight-number search is not configured on the backend.",
};

export function ConnectionRiskCalculator() {
  const [mode, setMode] = useState<Mode>("flight-number");
  const [manualForm, setManualForm] = useState(initialManualForm);
  const [flightForm, setFlightForm] = useState(initialFlightForm);
  const [manualErrors, setManualErrors] = useState<ManualErrors>({});
  const [flightErrors, setFlightErrors] = useState<FlightErrors>({});
  const [result, setResult] = useState<ConnectionRiskResponse | null>(null);
  const [v2Response, setV2Response] = useState<V2ConnectionResponse | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);

  function clearOutput() { activeRequest.current?.abort(); activeRequest.current = null; setLoading(false); setResult(null); setV2Response(null); setApiError(null); }
  function changeMode(next: Mode) { if (next === mode) return; clearOutput(); setMode(next); }
  function updateManual(key: keyof ItineraryRequest, value: string) { clearOutput(); setManualForm((current) => ({ ...current, [key]: value })); setManualErrors((current) => ({ ...current, [key]: undefined })); }
  function updateFlight(key: keyof FlightNumberRequest, value: string) { clearOutput(); setFlightForm((current) => ({ ...current, [key]: value })); setFlightErrors((current) => ({ ...current, [key]: undefined })); }

  async function submitManual(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const errors = validateManual(manualForm); setManualErrors(errors); setApiError(null); setResult(null); setV2Response(null);
    if (Object.keys(errors).length) { requestAnimationFrame(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()); return; }
    await request(async (signal) => ({ result: await estimateConnectionRisk(manualForm, signal), v2: null }));
  }
  async function submitFlight(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const errors = validateFlight(flightForm); setFlightErrors(errors); setApiError(null); setResult(null); setV2Response(null);
    if (Object.keys(errors).length) { requestAnimationFrame(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()); return; }
    const payload = { ...flightForm, first_flight_number: normalizeFlightNumber(flightForm.first_flight_number), second_flight_number: normalizeFlightNumber(flightForm.second_flight_number) };
    await request(async (signal) => {
      const v2 = await estimateConnectionRiskByFlightNumber(payload, signal);
      if (v2.status !== "success" || !v2.probability_result || !v2.itinerary) {
        if (v2.status === "ambiguous") return { result: null, v2 };
        const status = v2.status as Exclude<V2ConnectionResponse["status"], "success" | "ambiguous">;
        throw new ApiError(v2.message || statusMessages[status]);
      }
      return { result: v2.probability_result, v2 };
    });
  }
  async function request(run: (signal: AbortSignal) => Promise<{ result: ConnectionRiskResponse | null; v2: V2ConnectionResponse | null }>) {
    if (activeRequest.current) return; const controller = new AbortController(); activeRequest.current = controller; setLoading(true);
    try { const next = await run(controller.signal); if (activeRequest.current === controller) { setResult(next.result); setV2Response(next.v2); } }
    catch (error) { if (!(error instanceof DOMException && error.name === "AbortError") && activeRequest.current === controller) setApiError(error instanceof ApiError ? error.message : "An unexpected error occurred."); }
    finally { if (activeRequest.current === controller) { activeRequest.current = null; setLoading(false); } }
  }

  return <main>
    <header className="hero"><div className="brand"><span className="wing-mark" aria-hidden="true" /> Flight Connection Probability</div><div className="hero-copy"><span className="badge">Experimental</span><h1>Will I Make My Connection?</h1><p>Estimate a U.S. domestic connection using resolved flight schedules, historical BTS arrival performance, and explicit passenger-time assumptions—not live flight status.</p></div></header>
    <div className="page-grid">
      <section className="form-card" aria-labelledby="itinerary-title">
        <div className="mode-tabs" role="tablist" aria-label="Itinerary entry method">
          <button type="button" role="tab" aria-selected={mode === "flight-number"} onClick={() => changeMode("flight-number")}>Search by flight number</button>
          <button type="button" role="tab" aria-selected={mode === "manual"} onClick={() => changeMode("manual")}>Enter manually</button>
        </div>
        {mode === "flight-number" ? <>
          <div className="section-heading"><div><p className="eyebrow">Your itinerary</p><h2 id="itinerary-title">Find your flights</h2></div><p>Enter the marketing carrier code and number shown on your booking.</p></div>
          <form onSubmit={submitFlight} noValidate aria-busy={loading}>
            <div className="flight-number-grid">
              <label>First flight number<input autoComplete="off" placeholder="e.g. DL1575" aria-invalid={Boolean(flightErrors.first_flight_number)} aria-describedby="first-flight-error" value={flightForm.first_flight_number} onChange={(e) => updateFlight("first_flight_number", e.target.value)} /><FieldError id="first-flight-error" message={flightErrors.first_flight_number} /></label>
              <label>Second flight number<input autoComplete="off" placeholder="e.g. DL5798" aria-invalid={Boolean(flightErrors.second_flight_number)} aria-describedby="second-flight-error" value={flightForm.second_flight_number} onChange={(e) => updateFlight("second_flight_number", e.target.value)} /><FieldError id="second-flight-error" message={flightErrors.second_flight_number} /></label>
              <label>Travel date<input type="date" aria-invalid={Boolean(flightErrors.travel_date)} aria-describedby="flight-date-error" value={flightForm.travel_date} onChange={(e) => updateFlight("travel_date", e.target.value)} /><FieldError id="flight-date-error" message={flightErrors.travel_date} /></label>
            </div>
            {apiError && <div className="error-banner" role="alert"><strong>Estimate unavailable.</strong> {apiError}</div>}
            <button type="submit" disabled={loading}>{loading ? <><span className="spinner" aria-hidden="true" />Finding flights…</> : "Find flights and calculate probability"}</button>
          </form>
        </> : <>
          <div className="section-heading"><div><p className="eyebrow">Your itinerary</p><h2 id="itinerary-title">Flight details</h2></div><p>Enter each time in the local time zone of that airport.</p></div>
          <form onSubmit={submitManual} noValidate aria-busy={loading}>
            <div className="route-grid">{airportFields.map(({ key, label }) => <AirportCombobox key={key} id={key} label={label} value={manualForm[key]} error={manualErrors[key]} onChange={(code) => updateManual(key, code)} />)}</div>
            <div className="detail-grid"><CarrierCombobox id="carrier" label="Carrier" value={manualForm.carrier} error={manualErrors.carrier} onChange={(code) => updateManual("carrier", code)} /><label>Travel date<input type="date" aria-invalid={Boolean(manualErrors.travel_date)} aria-describedby="date-error" value={manualForm.travel_date} onChange={(e) => updateManual("travel_date", e.target.value)} /><FieldError id="date-error" message={manualErrors.travel_date} /></label></div>
            <div className="time-grid"><label>First flight departure<input type="time" aria-invalid={Boolean(manualErrors.first_departure_time)} aria-describedby="departure-error" value={manualForm.first_departure_time} onChange={(e) => updateManual("first_departure_time", e.target.value)} /><FieldError id="departure-error" message={manualErrors.first_departure_time} /></label><label>First flight arrival<input type="time" aria-invalid={Boolean(manualErrors.first_arrival_time)} aria-describedby="arrival-error" value={manualForm.first_arrival_time} onChange={(e) => updateManual("first_arrival_time", e.target.value)} /><FieldError id="arrival-error" message={manualErrors.first_arrival_time} /></label><label>Connecting departure<input type="time" aria-invalid={Boolean(manualErrors.connecting_departure_time)} aria-describedby="connection-time-error" value={manualForm.connecting_departure_time} onChange={(e) => updateManual("connecting_departure_time", e.target.value)} /><FieldError id="connection-time-error" message={manualErrors.connecting_departure_time} /></label></div>
            {apiError && <div className="error-banner" role="alert"><strong>Estimate unavailable.</strong> {apiError}</div>}
            <button type="submit" disabled={loading}>{loading ? <><span className="spinner" aria-hidden="true" />Calculating probability…</> : "Calculate connection probability"}</button>
          </form>
        </>}
      </section>
      {result ? <ResultPanel result={result} v2={v2Response ?? undefined} /> : v2Response?.status === "ambiguous" ? <aside className="empty-state ambiguous-state"><h2>More than one schedule matched</h2><p>Selecting among multiple segments is not available yet. Check the flight numbers and date, then enter the itinerary manually if needed.</p>{v2Response.ambiguous_legs.map((item) => <div key={item.leg} className="candidate-list"><strong>{item.leg === "first" ? "First flight" : "Second flight"}</strong>{item.candidates.map((candidate) => <span key={`${candidate.marketing_flight_number}-${candidate.origin}-${candidate.destination}-${candidate.scheduled_departure}`}>{candidate.marketing_flight_number}: {candidate.origin} → {candidate.destination}</span>)}</div>)}</aside> : <aside className="empty-state"><div className="clock" aria-hidden="true"><span /></div><h2>Your estimate will appear here</h2><p>{mode === "flight-number" ? "Enter both flight numbers and a travel date to resolve the schedule and estimate the connection." : "Enter the scheduled itinerary to compare the layover against historical delays and simulated transfer times."}</p></aside>}
    </div>
    <footer className="disclaimer"><strong>Experimental research tool.</strong> Results are estimates, not guarantees. Historical delay evidence excludes cancellations and diversions. Real-time conditions and airport-specific walking times are not modeled; terminal, gate, and aircraft details appear only when the schedule provider supplies them.</footer>
  </main>;
}
