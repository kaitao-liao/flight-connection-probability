export type ItineraryRequest = {
  carrier: string;
  origin: string;
  connection: string;
  destination: string;
  travel_date: string;
  first_departure_time: string;
  first_arrival_time: string;
  connecting_departure_time: string;
};

export type ConnectionRiskResponse = {
  connection_probability: number;
  scheduled_layover_minutes: number;
  overnight_connection: boolean;
  historical_sample_size: number;
  delay_statistics: {
    median_minutes: number;
    p75_minutes: number;
    p90_minutes: number;
  };
  scenarios: {
    on_time: number;
    delay_15: number;
    delay_30: number;
    delay_45: number;
  };
  model: {
    version: string;
    cohort_level: string;
    arrival_delay_evidence: string;
    deplaning_time: {
      fixed_minutes: number;
      evidence_type: string;
    };
    transfer_time: {
      distribution: string;
      minimum_minutes: number;
      mode_minutes: number;
      maximum_minutes: number;
      evidence_type: string;
    };
    boarding_cutoff_minutes: number;
    simulation_count: number;
    random_seed: number | null;
    exclusions: string[];
    historical_coverage: {
      lookback_months: 24;
      available_start_date: string;
      available_end_date: string;
      requested_prediction_date: string;
      effective_history_start_date: string;
      effective_history_end_date: string;
      strict_cutoff_exclusive: string;
      freshness_warning: string | null;
    };
  };
};

export type FlightNumberRequest = {
  first_flight_number: string;
  second_flight_number: string;
  travel_date: string;
  first_candidate_index?: number;
  second_candidate_index?: number;
};

export type ResolvedFlight = {
  marketing_carrier: string;
  marketing_flight_number: string;
  origin: string;
  destination: string;
  scheduled_departure: string;
  scheduled_arrival: string;
  departure_terminal: string | null;
  arrival_terminal: string | null;
  departure_gate: string | null;
  arrival_gate: string | null;
  aircraft_type: string | null;
  provider_quality: string[];
  codeshare_status: string | null;
  operating_carrier: string | null;
  operating_flight_number: string | null;
};

export type V2ConnectionResponse = {
  status:
    | "success"
    | "ambiguous"
    | "schedule_not_found"
    | "invalid_connection_airport"
    | "invalid_chronology"
    | "provider_data_quality_error"
    | "provider_temporarily_unavailable"
    | "provider_configuration_error"
    | "invalid_candidate_selection";
  itinerary: {
    first_flight: ResolvedFlight;
    second_flight: ResolvedFlight;
    connection_airport: string;
    scheduled_layover_minutes: number;
  } | null;
  probability_result: ConnectionRiskResponse | null;
  ambiguous_legs: Array<{ leg: "first" | "second"; candidates: ResolvedFlight[] }>;
  leg: "first" | "second" | null;
  message: string | null;
  warnings: string[];
};

export class ApiError extends Error {
  constructor(message: string, public readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
  if (configured) return configured;
  if (process.env.NODE_ENV === "production") {
    throw new ApiError(
      "The frontend is not configured with NEXT_PUBLIC_API_BASE_URL. Contact the site owner.",
    );
  }
  return "http://127.0.0.1:8000";
}

export async function estimateConnectionRisk(
  payload: ItineraryRequest,
  signal?: AbortSignal,
): Promise<ConnectionRiskResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v1/connection-risk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Could not reach the API. Confirm that the FastAPI server is running.");
  }

  if (!response.ok) {
    let message = "The estimate could not be generated.";
    try {
      const body = (await response.json()) as { detail?: string | Array<{ msg?: string }> };
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).filter(Boolean).join("; ") || message;
    } catch {
      // Keep the stable fallback message for non-JSON failures.
    }
    if (response.status === 503) {
      message = "Historical flight data is currently unavailable. Check the local database and try again.";
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as ConnectionRiskResponse;
}

export async function estimateConnectionRiskByFlightNumber(
  payload: FlightNumberRequest,
  signal?: AbortSignal,
): Promise<V2ConnectionResponse> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}/api/v2/connection-risk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Could not reach the API. Confirm that the FastAPI server is running.");
  }

  if (!response.ok) {
    let message = "The flight schedule could not be resolved.";
    try {
      const body = (await response.json()) as { detail?: string | Array<{ msg?: string }> };
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).filter(Boolean).join("; ") || message;
    } catch {
      // Keep the stable fallback message for non-JSON failures.
    }
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as V2ConnectionResponse;
}
