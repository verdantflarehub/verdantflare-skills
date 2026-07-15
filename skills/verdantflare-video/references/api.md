# VerdantFlare Video API

## Endpoint

Use the configured API base URL, normally `https://api.verdantflarehub.com/v1`.

- `POST /videos`
- `GET /videos/{task_id}`

Send `Authorization: Bearer <API key>` only to the API host. Send `X-Request-ID` with a new UUID for every creation attempt.

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
        {"type": "video_url", "video_url": {"url": "https://assets.example/ref.mp4"}},
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

The first content item must be non-empty text. Keep media items separate and preserve their user-declared order. Do not mix `messages[].content[]` with `metadata.content`.

## Responses

Creation returns the public VerdantFlare ID in `id` (and may repeat it in `task_id`). The client persists this ID before the first query. The JD upstream ID is never a client field.

Query responses use these public statuses:

| Status | Meaning | Action |
| --- | --- | --- |
| `queued` | Waiting | Query again |
| `in_progress` | Generating | Save/report `progress`, then query again |
| `completed` | Ready | Require non-empty `metadata.url`, then download |
| `failed` or `failure` | Terminal failure | Return `error.code` and a redacted `error.message` |

`completed` without `metadata.url` is a protocol error. Unknown statuses are not guessed or converted.

Successful metadata may include `duration`, `ratio`, `resolution`, `framespersecond`, and `generate_audio`. Preserve only known non-sensitive fields in local state.

## Retry Contract

- Never retry `POST /videos` automatically. A timeout or 5xx is an `UNKNOWN` submission until the API Center verifies the `X-Request-ID`.
- GET may retry network errors, 408, 429, and transient 5xx with exponential backoff and a bounded retry limit.
- If a result download returns 403 or 410, query the same Task ID once and retry the new URL once.
- Never send API Authorization, S3 credentials, cookies, or creation headers to a media result URL.
