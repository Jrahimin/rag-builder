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

test("rejects a clean stream EOF without a terminal event", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response('data: {"event":"token","delta":"partial"}\n\n', {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    ),
  );
  let timing: { doneReceivedAt?: number; streamClosedAt?: number; firstAnswerTokenAt?: number } =
    {};
  await expect(
    operatorApiClient.streamMessage(
      "project-1",
      "conversation-1",
      "question",
      vi.fn(),
      undefined,
      undefined,
      undefined,
      (next) => {
        timing = next;
      },
    ),
  ).rejects.toMatchObject({ code: "stream_incomplete" });
  expect(timing.firstAnswerTokenAt).toBeDefined();
  expect(timing.doneReceivedAt).toBeUndefined();
  expect(timing.streamClosedAt).toBeDefined();
});

test("accepts a stream only after its terminal event", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          'data: {"event":"token","delta":"answer"}\n\n' +
            'data: {"event":"done","grounded":true}\n\n',
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        ),
      ),
  );
  const onDelta = vi.fn();
  const result = await operatorApiClient.streamMessage(
    "project-1",
    "conversation-1",
    "question",
    onDelta,
  );
  expect(result.content).toBe("answer");
  expect(onDelta).toHaveBeenCalledWith("answer");
  expect(result.timing.firstAnswerTokenAt).toBeGreaterThanOrEqual(result.timing.requestStartedAt);
  expect(result.timing.doneReceivedAt).toBeGreaterThanOrEqual(result.timing.firstAnswerTokenAt!);
  expect(result.timing.streamClosedAt).toBeGreaterThanOrEqual(result.timing.doneReceivedAt!);
  expect(result.timing.persistedMessageFetchedAt).toBeUndefined();
});

test("records token, done, and EOF marks without treating whitespace as an answer token", async () => {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode('data: {"event":"token","delta":"  "}\n\n'));
      setTimeout(() => {
        controller.enqueue(encoder.encode('data: {"event":"token","delta":"answer"}\n\n'));
      }, 20);
      setTimeout(() => {
        controller.enqueue(encoder.encode('data: {"event":"done","grounded":true}\n\n'));
        controller.close();
      }, 40);
    },
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
      ),
  );
  const result = await operatorApiClient.streamMessage(
    "project-1",
    "conversation-1",
    "question",
    vi.fn(),
  );
  expect(result.timing.firstAnswerTokenAt).toBeGreaterThan(result.timing.responseHeadersAt!);
  expect(result.timing.doneReceivedAt).toBeGreaterThan(result.timing.firstAnswerTokenAt!);
  expect(result.timing.streamClosedAt).toBeGreaterThanOrEqual(result.timing.doneReceivedAt!);
});

test("keeps cancellation and premature EOF distinct from a completed stream", async () => {
  const abort = new AbortController();
  let timing: { doneReceivedAt?: number; streamClosedAt?: number } = {};
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      abort.signal.addEventListener("abort", () => {
        controller.error(new Error("BodyStreamBuffer was aborted"));
      });
    },
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
      ),
  );

  const pending = operatorApiClient.streamMessage(
    "project-1",
    "conversation-1",
    "question",
    vi.fn(),
    undefined,
    undefined,
    abort.signal,
    (next) => {
      timing = next;
    },
  );
  abort.abort();

  await expect(pending).rejects.toMatchObject({ code: "stream_cancelled", status: 499 });
  expect(timing.doneReceivedAt).toBeUndefined();
  expect(timing.streamClosedAt).toBeDefined();
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

test("paginates operator Projects with the API's 100-row limit", async () => {
  const first = Array.from({ length: 100 }, (_, index) => ({ id: `project-${index}` }));
  const second = [{ id: "project-100" }];
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

  const page = await operatorApiClient.getAllOperatorProjects();

  expect(page.items).toHaveLength(101);
  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "/api/v1/operator/projects?limit=100&offset=0&include_deleted=true",
    expect.any(Object),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "/api/v1/operator/projects?limit=100&offset=100&include_deleted=true",
    expect.any(Object),
  );
});

test("paginates Organization Projects with the API's 100-row limit", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        success: true,
        data: { items: [{ id: "project-1" }], total: 1, limit: 100, offset: 0 },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await operatorApiClient.getOrganizationProjects("org-1");

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/organizations/org-1/projects?limit=100&offset=0&include_deleted=true",
    expect.any(Object),
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
