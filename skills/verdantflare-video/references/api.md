# VerdantFlare Video API

## Endpoint

Use the configured API base URL, normally `https://api.verdantflarehub.com/v1`.

- `POST /videos`
- `GET /videos/{task_id}`
- `GET /video-submissions/{client_request_id}`

Send `Authorization: Bearer <API key>` only to the API host. For one creation intent, generate one UUID and send the identical value in both `Idempotency-Key` and the compatible `X-Request-ID`. Serialize the payload once for the single POST and bind those exact bytes to that UUID; recovery uses GET and never reconstructs a POST. Signed media-query bytes participate in request identity, so changing or refreshing them under the same UUID causes `idempotency_conflict`.

## Create Request

```json
{
  "model": "verdantflare-sd2",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "A concise creative prompt"},
        {"type": "image_url", "image_url": {"url": "https://assets.example/image.jpg"}},
        {"type": "video_url", "video_url": {"url": "https://assets.example/ref-1.mp4"}},
        {"type": "video_url", "video_url": {"url": "https://assets.example/ref-2.mp4"}},
        {"type": "video_url", "video_url": {"url": "https://assets.example/ref-3.mp4"}},
        {"type": "audio_url", "audio_url": {"url": "https://assets.example/ref.mp3"}}
      ]
    }
  ],
  "duration": 10,
  "ratio": "16:9",
  "generate_audio": true,
  "watermark": false
}
```

The first content item must be non-empty text. Keep media items separate and preserve their collected order. Accept at most 9 image references, 1 audio reference, and 3 video references; local files and HTTPS URLs count toward the same per-type limit. Refer to ordered videos as `video 1`, `video 2`, and `video 3` in the prompt. `duration` defaults to 10 when omitted and otherwise accepts an integer from 1 through 15. Do not mix `messages[].content[]` with `metadata.content`; the API returns `conflicting_content` instead of choosing one.

Treat `invalid_model`, `invalid_messages`, `invalid_content`, `missing_prompt`, `invalid_duration`, `conflicting_duration`, `invalid_ratio`, `unsupported_resolution`, `media_limit_exceeded`, `invalid_media_url`, and `conflicting_content` as terminal local-validation errors. Correct the input only for a user-authorized new intent; do not retry the original POST.

## Responses

Creation returns the public VerdantFlare ID in `id` (and may repeat it in `task_id`). The client persists this ID before the first query. A provider upstream ID is never a client field.

Query responses use these public statuses:

| Status | Meaning | Action |
| --- | --- | --- |
| `queued` | Waiting | Query again |
| `in_progress` | Generating | Save/report `progress`, then query again |
| `completed` | Ready | Require non-empty `metadata.url`, then download |
| `failed` or `failure` | Terminal failure | Return `error.code` and a redacted `error.message` |

`completed` without `metadata.url` is a protocol error. Unknown statuses are not guessed or converted.

Successful metadata may include `duration`, `ratio`, `resolution`, `framespersecond`, and `generate_audio`. Preserve only known non-sensitive fields in local state.

## Submission Recovery

`GET /video-submissions/{client_request_id}` returns the owner-scoped `client_request_id`, `submission_state`, `billing_state`, safe error fields, and timestamps. Only `CONFIRMED` includes `public_task_id`.

| Submission state | Action |
| --- | --- |
| `PREPARED` or `SENDING` | Preserve the state and query again later |
| `CONFIRMED` | Persist `public_task_id`, then use the normal task GET |
| `REJECTED` | Stop; do not create automatically |
| `UNKNOWN` | Stop for reconciliation; do not label it queued or failed |

## Retry Contract

- Never retry `POST /videos` automatically. On a timeout, network failure, 408, 429, transient 5xx, an unparseable response, or `409 submission_in_progress`, query the Submission endpoint with the same client request ID. If recovery remains uncertain, preserve `UNKNOWN` for reconciliation.
- GET may retry network errors, 408, 429, and transient 5xx with exponential backoff and a bounded retry limit.
- A ledger-managed `metadata.url` is the stable, same-origin `/videos/{task_id}/content` archive proxy, not a refreshable provider-signed URL. It must not redirect. Treat 401/403 as authorization or policy failure, `409 result_not_ready` as an unsafe/unready archive, and 502 as archive unavailability or integrity failure; preserve the Task ID and stop rather than creating again.
- Send API Authorization to a result URL only when it exactly matches the configured VerdantFlare API origin and `/videos/{task_id}/content` path. Reject redirects and non-matching URLs. Never send the Bearer token, S3 credentials, cookies, idempotency headers, or other creation headers to an object/provider host.

Before any local-media upload, the client read-only verifies an existing enabled bucket lifecycle rule whose prefix exactly matches the installation-specific upload prefix and whose current-object expiration is exactly 7 days. Missing, unreadable, broader, tag/date-based, shorter/longer, or unsupported rules fail closed before upload or submission. The client never creates, edits, imports, or replaces bucket lifecycle configuration. This verification proves the configured expiration rule only; the operator remains responsible for private-bucket access, read-only HTTPS delivery, and a URL lifetime long enough for queueing, generation, and recovery.

For each upload, persist the deterministic object key and ownership proof as `PENDING` before `mc cp`, then mark it uploaded after success. The client attempts exact per-object deletion at task terminal state, while the verified storage lifecycle provides the 7-day hard deadline even for `UNKNOWN` or when the client never runs again. Cleanup validates installation ownership, never removes a prefix, and never logs object keys or credentials.
