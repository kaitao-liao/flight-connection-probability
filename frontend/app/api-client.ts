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
