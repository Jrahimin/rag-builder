import { operatorApiClient } from "./operatorApiClient";

test("converts a missing backend into an actionable typed error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connection refused")));
  await expect(operatorApiClient.getOverview()).rejects.toMatchObject({
    code: "backend_unavailable",
    status: 0,
  });
});

test("surfaces the backend error envelope without leaking response internals", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          success: false,
          error: {
            code: "operator_data_unavailable",
            message: "Operational data is temporarily unavailable.",
          },
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  await expect(operatorApiClient.getMetrics()).rejects.toMatchObject({
    code: "operator_data_unavailable",
    status: 503,
  });
});

test("classifies a development proxy failure as backend unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("proxy connection refused", { status: 500 })),
  );
  await expect(operatorApiClient.getOverview()).rejects.toMatchObject({
    code: "backend_unavailable",
    status: 500,
  });
});
