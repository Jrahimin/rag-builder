# Webhooks API

Prefix: `/api/v1/projects/{project_id}/webhooks`. Organization authentication and
Project ownership checks apply to every route.

## POST `/endpoints`

Create an endpoint. Production accepts HTTPS only. The response includes the
derived signing secret; store it in the receiver's secret manager.

```json
{
  "url": "https://customer.example.com/webhooks/ape",
  "description": "DMS document status",
  "event_types": [
    "document.processing.succeeded.v1",
    "document.processing.failed.v1",
    "document.indexing.succeeded.v1",
    "document.indexing.failed.v1"
  ]
}
```

Returns `201` with `WebhookEndpointCreatedResponse`.

## GET `/endpoints`

Paginated endpoint inspection. Query: `limit` (1–100), `offset`.

## PATCH `/endpoints/{endpoint_id}/status`

Enable or disable delivery without deleting history.

```json
{ "enabled": false, "reason": "receiver maintenance" }
```

## GET `/deliveries`

Paginated delivery history. Optional filters: `endpoint_id`, `state`. States are
`pending`, `delivering`, `retry_scheduled`, `succeeded`, and `failed`.

## GET `/deliveries/{delivery_id}`

Returns the immutable event plus every completed HTTP attempt, including bounded
response excerpts and safe error text.

## POST `/deliveries/{delivery_id}/replay`

Returns `202` and queues a new delivery record. Replay retains the original event
ID and creates a new delivery ID/replay number. Receivers must deduplicate by event ID.

## Receiver contract

APE sends canonical UTF-8 JSON and these headers:

```http
Content-Type: application/json
X-APE-Event-ID: <uuid>
X-APE-Event-Type: document.indexing.succeeded.v1
X-APE-Timestamp: <unix-seconds>
X-APE-Signature: v1=<hex-hmac-sha256>
Idempotency-Key: <same-event-uuid>
```

Verify `HMAC-SHA256(secret, timestamp + "." + event_id + "." + raw_body)` before
parsing. Reject stale timestamps according to the host application's approved replay
window, compare signatures in constant time, and persist processed event IDs.

