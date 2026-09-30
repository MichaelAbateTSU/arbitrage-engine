import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T extends z.ZodType>(
  path: string,
  schema: T,
  options: RequestInit = {},
): Promise<z.infer<T>> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const payload: unknown = await response.json();
  if (!response.ok) {
    const body = z.object({ detail: z.unknown() }).safeParse(payload);
    throw new ApiError(
      response.status,
      body.success ? JSON.stringify(body.data.detail) : "Request failed",
    );
  }
  const result = schema.safeParse(payload);
  if (!result.success)
    throw new Error("API schema mismatch; data was not displayed as valid");
  return result.data;
}

export function useData<T extends z.ZodType>(
  path: string,
  schema: T,
  enabled = true,
) {
  return useQuery({
    queryKey: [path],
    queryFn: ({ signal }) => api(path, schema, { signal }),
    enabled,
    refetchInterval: 5000,
    retry: 1,
  });
}

export function money(value: string | number | null | undefined) {
  if (value == null) return "\u2014";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(Number(value));
}
export function percent(value: string) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}
export function time(value: string | null | undefined) {
  return value
    ? new Date(value).toLocaleTimeString("en-GB", {
        timeZone: "UTC",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "\u2014";
}
export function reason(value: string) {
  return value
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase());
}
