import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectionRiskCalculator, formatDelay } from "../app/connection-risk-calculator";

const response = {
  connection_probability: 0.782,
  scheduled_layover_minutes: 85,
  overnight_connection: false,
  historical_sample_size: 1800,
  delay_statistics: { median_minutes: -2, p75_minutes: 18, p90_minutes: 38 },
  scenarios: { on_time: 0.96, delay_15: 0.87, delay_30: 0.62, delay_45: 0.29 },
  model: {
    version: "v1",
    cohort_level: "route_carrier_month_time_bucket",
    arrival_delay_evidence: "observed_completed_non_diverted_BTS_flights",
    deplaning_time: { fixed_minutes: 20, evidence_type: "modeling_assumption" },
    transfer_time: { distribution: "triangular", minimum_minutes: 15, mode_minutes: 25, maximum_minutes: 40, evidence_type: "modeling_assumption" },
    boarding_cutoff_minutes: 15,
    simulation_count: 10000,
    random_seed: 42,
    exclusions: ["cancelled flights", "diverted flights"],
    historical_coverage: {
      lookback_months: 24 as const,
      available_start_date: "2023-01-01",
      available_end_date: "2025-12-31",
      requested_prediction_date: "2026-08-20",
      effective_history_start_date: "2024-08-20",
      effective_history_end_date: "2025-12-31",
      strict_cutoff_exclusive: "2026-08-20",
      freshness_warning: "Historical BTS data ends on 2025-12-31, 232 days before the requested prediction date.",
    },
  },
};

function mockJson(body: unknown, status = 200) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: status >= 200 && status < 300, status, json: async () => body }));
}

async function validSubmission() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Travel date"), "2026-08-20");
  await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));
  return user;
}

async function chooseAirport(
  user: ReturnType<typeof userEvent.setup>, label: string, search: string, option: RegExp,
) {
  const input = screen.getByLabelText(label);
  await user.clear(input);
  await user.type(input, search);
  await user.click(await screen.findByRole("option", { name: option }));
  return input;
}

async function chooseCarrier(
  user: ReturnType<typeof userEvent.setup>, search: string, option: RegExp,
) {
  const input = screen.getByLabelText("Carrier");
  await user.clear(input);
  await user.type(input, search);
  await user.click(await screen.findByRole("option", { name: option }));
  return input;
}

afterEach(() => vi.unstubAllGlobals());

describe("ConnectionRiskCalculator", () => {
  it("renders the form, empty state, and experimental disclaimer without fake results", () => {
    render(<ConnectionRiskCalculator />);
    expect(screen.getByRole("heading", { name: "Will I Make My Connection?" })).toBeInTheDocument();
    expect(screen.getByLabelText("Origin airport")).toBeInTheDocument();
    expect(screen.getByText("Your estimate will appear here")).toBeInTheDocument();
    expect(screen.getByText(/Experimental research tool/)).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("searches airports by IATA code and retains the human-readable selection", async () => {
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const origin = await chooseAirport(user, "Origin airport", "LAX", /Los Angeles International Airport.*LAX/i);
    expect(origin).toHaveValue("Los Angeles — Los Angeles International Airport (LAX)");
  });

  it("searches airports by city and airport name", async () => {
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const connection = await chooseAirport(user, "Connection airport", "Atlanta", /Hartsfield.*ATL/i);
    expect((connection as HTMLInputElement).value).toContain("Atlanta");
    const destination = await chooseAirport(user, "Final destination", "Logan", /Boston.*Logan.*BOS/i);
    expect((destination as HTMLInputElement).value).toContain("(BOS)");
  });

  it("supports keyboard navigation and selection", async () => {
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const origin = screen.getByLabelText("Origin airport");
    await user.clear(origin);
    await user.type(origin, "Los Angeles");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(origin).toHaveValue("Los Angeles — Los Angeles International Airport (LAX)");
    expect(origin).toHaveAttribute("aria-expanded", "false");
  });

  it("searches carriers by code and retains the selected airline", async () => {
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const carrier = await chooseCarrier(user, "UA", /United Airlines.*UA/i);
    expect(carrier).toHaveValue("United Airlines (UA)");
  });

  it("searches carriers by airline name and supports changing the selection", async () => {
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const carrier = await chooseCarrier(user, "American", /American Airlines.*AA/i);
    expect(carrier).toHaveValue("American Airlines (AA)");
    await user.click(screen.getByRole("button", { name: "Clear Carrier" }));
    expect(carrier).toHaveValue("");
    await user.type(carrier, "Delta");
    await user.click(await screen.findByRole("option", { name: /Delta Air Lines.*DL/i }));
    expect(carrier).toHaveValue("Delta Air Lines (DL)");
  });

  it("supports carrier keyboard navigation, selection, and Escape", async () => {
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const carrier = screen.getByLabelText("Carrier");
    await user.clear(carrier);
    await user.type(carrier, "United");
    await user.keyboard("{ArrowDown}{ArrowUp}{ArrowDown}{Enter}");
    expect(carrier).toHaveValue("United Airlines (UA)");
    await user.tab();
    await user.click(carrier);
    expect(carrier).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(carrier).toHaveAttribute("aria-expanded", "false");
  });

  it("shows client-side validation and does not submit invalid input", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    await user.clear(screen.getByLabelText("Origin airport"));
    await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));
    expect(await screen.findByText("Select a supported airport from the list.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not submit unsupported arbitrary airport text", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const origin = screen.getByLabelText("Origin airport");
    await user.clear(origin);
    await user.type(origin, "ZZZ Unknown Airport");
    expect(screen.getByText("No supported airports found")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));
    expect(await screen.findByText("Select a supported airport from the list.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not submit unsupported arbitrary carrier text", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    const carrier = screen.getByLabelText("Carrier");
    await user.clear(carrier);
    await user.type(carrier, "ZZ Unsupported Air");
    expect(screen.getByText("No supported carriers found")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));
    expect(await screen.findByText("Select a supported carrier from the list.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends only the selected carrier code to the backend", async () => {
    mockJson(response);
    const user = userEvent.setup();
    render(<ConnectionRiskCalculator />);
    await chooseCarrier(user, "American", /American Airlines.*AA/i);
    await user.type(screen.getByLabelText("Travel date"), "2026-08-20");
    await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));
    await screen.findByText("78.2", { exact: false });
    const request = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String((request[1] as RequestInit).body)).carrier).toBe("AA");
  });

  it("submits normalized JSON, shows loading, and renders quantitative results", async () => {
    let resolve!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise((done) => { resolve = done; })));
    render(<ConnectionRiskCalculator />);
    const submission = validSubmission();
    expect(await screen.findByRole("button", { name: /calculating probability/i })).toBeDisabled();
    resolve({ ok: true, status: 200, json: async () => response });
    await submission;
    expect(await screen.findByText("78.2", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("85 min")).toBeInTheDocument();
    expect(screen.getByText("1,800")).toBeInTheDocument();
    expect(screen.getByText("2 min early")).toBeInTheDocument();
    expect(screen.getByText("96.0%")).toBeInTheDocument();
    expect(screen.getByText("29.0%")).toBeInTheDocument();
    expect(screen.getByText("Historical data coverage notice.")).toBeInTheDocument();
    expect(screen.queryByText(/low risk|moderate risk|high risk/i)).not.toBeInTheDocument();
    const request = vi.mocked(fetch).mock.calls[0];
    expect(request[0]).toContain("/api/v1/connection-risk");
    expect(JSON.parse(String((request[1] as RequestInit).body)).origin).toBe("ATL");
    expect(JSON.parse(String((request[1] as RequestInit).body)).carrier).toBe("DL");
  });

  it("shows a warning when the backend uses a broad fallback cohort", async () => {
    mockJson({ ...response, model: { ...response.model, cohort_level: "route" } });
    render(<ConnectionRiskCalculator />);
    await validSubmission();
    expect(await screen.findByText("Broader historical comparison used.")).toBeInTheDocument();
  });

  it("shows backend and availability errors without fabricated fallback results", async () => {
    mockJson({ detail: "historical flight data is unavailable" }, 503);
    render(<ConnectionRiskCalculator />);
    await validSubmission();
    expect(await screen.findByRole("alert")).toHaveTextContent("Historical flight data is currently unavailable");
    expect(screen.getByText("Your estimate will appear here")).toBeInTheDocument();
  });

  it("shows timezone validation errors and removes stale probability results", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => response })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          detail: "These scheduled times are not valid after accounting for the airports' local time zones. Please check the first-flight departure and arrival times.",
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<ConnectionRiskCalculator />);
    const user = await validSubmission();
    expect(await screen.findByText("78.2", { exact: false })).toBeInTheDocument();

    await chooseAirport(user, "Origin airport", "LAX", /Los Angeles International Airport.*LAX/i);
    await chooseAirport(user, "Connection airport", "ATL", /Hartsfield.*ATL/i);
    await user.click(screen.getByRole("button", { name: /calculate connection probability/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("not valid after accounting for the airports' local time zones");
    expect(screen.queryByRole("heading", { name: "Probability of making the connection" })).not.toBeInTheDocument();
    expect(screen.getByText("Your estimate will appear here")).toBeInTheDocument();
  });
});

describe("formatDelay", () => {
  it("formats early, on-time, and late values", () => {
    expect(formatDelay(-4)).toBe("4 min early");
    expect(formatDelay(0)).toBe("On time");
    expect(formatDelay(12)).toBe("12 min late");
  });
});
