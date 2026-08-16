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

test("serializes every usage aggregation filter", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ success: true, data: { items: [] } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await operatorApiClient.getUsage({
    startAt: "2026-08-01T00:00:00Z",
    endAt: "2026-08-02T00:00:00Z",
    bucket: "hour",
    organizationId: "organization-1",
    projectId: "project-1",
    provider: "gemini",
    model: "gemini-test",
    workload: "evaluation",
  });

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/operator/usage?start_at=2026-08-01T00%3A00%3A00Z&end_at=2026-08-02T00%3A00%3A00Z&bucket=hour&organization_id=organization-1&project_id=project-1&provider=gemini&model=gemini-test&workload=evaluation",
    expect.objectContaining({ credentials: "include" }),
  );
});

test("requests capabilities for the selected provider and model", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ success: true, data: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await operatorApiClient.getProviderCapabilities("openai", "o1-test");

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/operator/provider-capabilities?provider=openai&model=o1-test",
    expect.objectContaining({ credentials: "include" }),
  );
});

test("paginates Organizations with the API's 100-row limit", async () => {
  const first = Array.from({ length: 100 }, (_, index) => ({ id: `org-${index}` }));
  const second = [{ id: "org-100" }];
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          success: true,
          data: { items: first, total: 101, limit: 100, offset: 0 },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          success: true,
          data: { items: second, total: 101, limit: 100, offset: 100 },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  vi.stubGlobal("fetch", fetchMock);

  const page = await operatorApiClient.getOrganizations();

  expect(page.items).toHaveLength(101);
  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "/api/v1/organizations?limit=100&offset=0&include_deleted=true",
    expect.any(Object),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "/api/v1/organizations?limit=100&offset=100&include_deleted=true",
    expect.any(Object),
  );
});
