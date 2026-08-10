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
          error: {
            code: "operator_data_unavailable",
            message: "Operational data is temporarily unavailable.",
            request_id: "req-test",
          },
        }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  await expect(operatorApiClient.getMetrics()).rejects.toMatchObject({
    code: "operator_data_unavailable",
    status: 503,
    requestId: "req-test",
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

test("forwards OCR language on document reprocess", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ success: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await operatorApiClient.reprocessDocument("project-1", "document-1", "bn");

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/projects/project-1/documents/document-1/reprocess?ocr_lang=bn",
    expect.objectContaining({ method: "POST" }),
  );
});
