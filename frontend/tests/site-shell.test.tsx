import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SiteShell } from "../app/site-shell";

describe("SiteShell", () => {
  it("defaults to the estimator and navigates between informational views", async () => {
    const user = userEvent.setup();
    render(<SiteShell />);

    expect(screen.getByRole("button", { name: "Estimator" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: "Will I Make My Connection?" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "How It Works" }));
    expect(screen.getByRole("heading", { name: "How It Works" })).toBeInTheDocument();
    expect(screen.getByText("Passenger-Time Model")).toBeInTheDocument();
    expect(screen.getByText(/does not vary them by airport, terminal, gate/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "About" }));
    expect(screen.getByRole("heading", { name: "Decision support, grounded in data" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Estimator" }));
    expect(screen.getByRole("heading", { name: "Will I Make My Connection?" })).toBeInTheDocument();
  });
});
