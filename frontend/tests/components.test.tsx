import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Badge, DataTable, Empty, ErrorBox, Reasons } from "../src/components";
import { decimal, opportunitySchema, riskSchema } from "../src/schemas";
import { money, percent, time } from "../src/api";

describe("fail-closed client schemas", () => {
  it("rejects floating point financial responses", () => {
    expect(decimal.safeParse(0.5).success).toBe(false);
    expect(decimal.safeParse("0.50").success).toBe(true);
    expect(decimal.safeParse("1E-7").success).toBe(true);
    expect(
      opportunitySchema.safeParse({ calculation: { net_profit: "10" } })
        .success,
    ).toBe(false);
  });
  it("rejects an empty supported universe", () => {
    expect(riskSchema.safeParse({ supported_leagues: [] }).success).toBe(false);
  });
});
describe("dashboard evidence and states", () => {
  it("shows zero without presenting null as a profit", () => {
    expect(money("0")).toBe("$0.00");
    expect(money(null)).toBe("\u2014");
    expect(percent("0.005")).toBe("0.50%");
    expect(time("2026-09-30T18:30:00Z")).toBe("18:30:00");
  });
  it("renders reason codes in understandable language", () => {
    render(<Reasons reasons={["BOOK_STALE", "UNKNOWN_FEE"]} />);
    expect(screen.getByText("Book stale")).toBeVisible();
    expect(screen.getByText("Unknown fee")).toBeVisible();
  });
  it("does not hide errors as empty success", () => {
    render(<ErrorBox error={new Error("API unavailable")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("API unavailable");
  });
  it("shows an honest empty table", () => {
    render(<DataTable rows={[]} columns={[]} />);
    expect(screen.getByText(/never creates a trade/)).toBeVisible();
  });
  it("encodes user text instead of injecting markup", () => {
    render(<Badge>{"<script>alert(1)</script>"}</Badge>);
    expect(screen.getByText("<script>alert(1)</script>")).toBeVisible();
    render(<Empty title="No signals" />);
    expect(screen.getByText("No signals")).toBeVisible();
  });
});
