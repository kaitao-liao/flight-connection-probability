import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionRiskCalculator, formatDelay } from "../app/connection-risk-calculator";

const probability = {
  connection_probability: 0.5909, scheduled_layover_minutes: 60, overnight_connection: false,
  historical_sample_size: 115, delay_statistics: { median_minutes: -2, p75_minutes: 18, p90_minutes: 38 },
  scenarios: { on_time: 0.96, delay_15: 0.87, delay_30: 0.62, delay_45: 0.29 },
  model: {
    version: "v1", cohort_level: "route_carrier_month_time_bucket", arrival_delay_evidence: "observed_completed_non_diverted_BTS_flights",
    deplaning_time: { fixed_minutes: 20, evidence_type: "modeling_assumption" },
    transfer_time: { distribution: "triangular", minimum_minutes: 15, mode_minutes: 25, maximum_minutes: 40, evidence_type: "modeling_assumption" },
    boarding_cutoff_minutes: 15, simulation_count: 10000, random_seed: 42, exclusions: ["cancelled flights", "diverted flights"],
    historical_coverage: { lookback_months: 24 as const, available_start_date: "2023-01-01", available_end_date: "2025-12-31", requested_prediction_date: "2026-08-20", effective_history_start_date: "2024-08-20", effective_history_end_date: "2025-12-31", strict_cutoff_exclusive: "2026-08-20", freshness_warning: "Historical BTS data ends before the prediction date." },
  },
};

const flight = (number: string, origin: string, destination: string, departure: string, arrival: string) => ({
  marketing_carrier: "DL", marketing_flight_number: number, origin, destination,
  scheduled_departure: departure, scheduled_arrival: arrival,
  departure_terminal: "S", arrival_terminal: "4", departure_gate: "A1", arrival_gate: "B2",
  aircraft_type: "Airbus A320", provider_quality: ["Basic"], codeshare_status: null,
  operating_carrier: null, operating_flight_number: null,
});

const v2Success = {
  status: "success", itinerary: {
    first_flight: flight("DL1575", "ATL", "JFK", "2026-08-20T15:00:00-04:00", "2026-08-20T17:00:00-04:00"),
    second_flight: flight("DL5798", "JFK", "BOS", "2026-08-20T18:00:00-04:00", "2026-08-20T19:20:00-04:00"),
    connection_airport: "JFK", scheduled_layover_minutes: 60,
  }, probability_result: probability, ambiguous_legs: [], leg: null, message: null,
  warnings: ["Schedule metadata can change."],
};

function mockJson(body: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: status >= 200 && status < 300, status, json: async () => body }));
}

async function submitFlightNumbers() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("First flight number"), "dl 1575");
  await user.type(screen.getByLabelText("Second flight number"), "DL5798");
  await user.type(screen.getByLabelText("Travel date"), "2026-08-20");
  await user.click(screen.getByRole("button", { name: /find flights and calculate probability/i }));
  return user;
}

async function switchToManual() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "Enter manually" }));
  return user;
}

afterEach(() => vi.unstubAllGlobals());

describe("ConnectionRiskCalculator", () => {
  it("defaults to flight-number mode and preserves the manual mode", async () => {
    render(<ConnectionRiskCalculator />);
    expect(screen.getByRole("tab", { name: "Search by flight number" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("First flight number")).toBeInTheDocument();
    expect(screen.queryByLabelText("Origin airport")).not.toBeInTheDocument();
    await switchToManual();
    expect(screen.getByLabelText("Origin airport")).toBeInTheDocument();
    expect(screen.getByLabelText("Carrier")).toBeInTheDocument();
    expect(screen.getByLabelText("First flight departure")).toBeInTheDocument();
  });

  it("validates flight-number fields without calling the API", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup(); render(<ConnectionRiskCalculator />);
    await user.type(screen.getByLabelText("First flight number"), "bad/flight");
    await user.click(screen.getByRole("button", { name: /find flights/i }));
    expect((await screen.findAllByText(/such as DL1575/))).toHaveLength(2);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("normalizes flight numbers, calls V2, and renders shared and schedule results", async () => {
    mockJson(v2Success); render(<ConnectionRiskCalculator />); await submitFlightNumbers();
    expect(await screen.findByText("59.1", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("DL1575: ATL → JFK")).toBeInTheDocument();
    expect(screen.getByText("DL5798: JFK → BOS")).toBeInTheDocument();
    expect(screen.getAllByText(/Aircraft Airbus A320/)).toHaveLength(2);
    expect(screen.getByText("115")).toBeInTheDocument();
    expect(screen.getByText("Historical data only — not live flight data.")).toBeInTheDocument();
    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toContain("/api/v2/connection-risk");
    expect(JSON.parse(String((options as RequestInit).body))).toEqual({ first_flight_number: "DL1575", second_flight_number: "DL5798", travel_date: "2026-08-20" });
  });

  it("resubmits an explicitly selected ambiguous candidate", async () => {
    const reverse = flight("DL1575", "JFK", "ATL", "2026-08-20T12:00:00-04:00", "2026-08-20T14:00:00-04:00");
    const ambiguous = { ...v2Success, status: "ambiguous", itinerary: null, probability_result: null, ambiguous_legs: [{ leg: "first", candidates: [reverse, v2Success.itinerary.first_flight] }] };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ambiguous })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => v2Success }));
    render(<ConnectionRiskCalculator />); const user = await submitFlightNumbers();
    expect(await screen.findByText("Choose the matching schedule")).toBeInTheDocument();
    const continueButton = screen.getByRole("button", { name: "Continue with selected flights" });
    expect(continueButton).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /DL1575: ATL → JFK/i }));
    expect(continueButton).toBeEnabled();
    await user.click(continueButton);
    expect(await screen.findByText("59.1", { exact: false })).toBeInTheDocument();
    const [, options] = vi.mocked(fetch).mock.calls[1];
    expect(JSON.parse(String((options as RequestInit).body)).first_candidate_index).toBe(1);
  });

  it("shows safe V2 domain errors", async () => {
    mockJson({ status: "schedule_not_found", itinerary: null, probability_result: null, ambiguous_legs: [], leg: "first", message: null, warnings: [] });
    render(<ConnectionRiskCalculator />); await submitFlightNumbers();
    expect(await screen.findByRole("alert")).toHaveTextContent("No matching schedule was found");
  });

  it("keeps the V1 request contract and shared result presentation", async () => {
    mockJson(probability); render(<ConnectionRiskCalculator />); const user = await switchToManual();
    await user.type(screen.getByLabelText("Travel date"), "2026-08-20");
    await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));
    expect(await screen.findByText("59.1", { exact: false })).toBeInTheDocument();
    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toContain("/api/v1/connection-risk");
    expect(JSON.parse(String((options as RequestInit).body))).toMatchObject({ carrier: "DL", origin: "ATL", connection: "JFK", destination: "BOS" });
  });

  it("retains manual airport and carrier autocomplete validation", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); render(<ConnectionRiskCalculator />); const user = await switchToManual();
    await user.clear(screen.getByLabelText("Origin airport")); await user.clear(screen.getByLabelText("Carrier"));
    await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));
    expect(await screen.findByText("Select a supported airport from the list.")).toBeInTheDocument();
    expect(screen.getByText("Select a supported carrier from the list.")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });

  it("clears a result when modes change", async () => {
    mockJson(v2Success); render(<ConnectionRiskCalculator />); const user = await submitFlightNumbers();
    expect(await screen.findByText("59.1", { exact: false })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Enter manually" }));
    expect(screen.queryByRole("heading", { name: "Probability of making the connection" })).not.toBeInTheDocument();
    expect(screen.getByText("Your estimate will appear here")).toBeInTheDocument();
  });
});

describe("formatDelay", () => {
  it("formats early, on-time, and late values", () => {
    expect(formatDelay(-4)).toBe("4 min early"); expect(formatDelay(0)).toBe("On time"); expect(formatDelay(12)).toBe("12 min late");
  });
});
