---
name: verdantflare-video
description: Generate SD2 videos through the VerdantFlare API from text prompts and local image, audio, or video references; use when the user asks for a VerdantFlare video, an SD2 product/ad video, a video task status, a previous task recovery, or a result download. The skill validates local media, uploads it to the configured temporary bucket, creates one asynchronous task, polls it, and downloads the result.
---

# VerdantFlare Video

Use the bundled Python client for every API, object-storage, task-state, and download operation. Read `references/api.md` when constructing or interpreting an API request or response.

## Commands

Run scripts with the Skill directory as the working directory:

```text
macOS:  python3 scripts/video_client.py generate --prompt "..." [options]
        python3 scripts/video_client.py list
        python3 scripts/video_client.py resume <task-id>
Windows: py -3 scripts/video_client.py generate --prompt "..." [options]
         py -3 scripts/video_client.py list
         py -3 scripts/video_client.py resume <task-id>
```

Use the platform wrapper in `scripts/` when configuration is missing or rejected: `install-config-macos.sh` on macOS or `install-config-windows.ps1` on Windows. Both wrappers invoke `install-config.py`, read the one-time configuration URL through a hidden TTY prompt, and download a pinned `mc` binary when needed. Never place a configuration URL, API key, or S3 secret in command arguments, environment overrides, prompts, logs, or task records.

## Request Mapping

- Keep the prompt non-empty and put it in the first text content item.
- Pass `--image`, `--audio`, and `--video` for local files; use the corresponding `--*-url` option for an existing HTTPS URL.
- Use `--duration` from 1 to 15 seconds; default to 10.
- Use one of `16:9`, `9:16`, `1:1`, `4:3`, or `3:4`; default to `16:9`.
- Keep `--generate-audio` enabled unless the user explicitly requests silence. Use `--no-generate-audio` for silence.
- Do not submit more than 9 images, 1 audio reference, or 1 video reference in v1.
- Do not add `metadata.content`; the client sends the structured `messages` form from `references/api.md`.

## Recovery Rules

- A successful POST must be persisted before polling. Never submit a second POST for a timeout, connection failure, 5xx, or malformed response.
- Use `list` to show local records. Use `resume <task-id>` to query and download an existing task; recovery never creates a task.
- Treat `queued` and `in_progress` as active, `completed` as downloadable only when `metadata.url` is present, and `failed`/`failure` as terminal.
- Treat an unknown status or completed response without a URL as a protocol error.
- A signed result URL that returns 403 or 410 may be refreshed once by querying the same Task ID. Never send the API Authorization header to the result host.

## Output

Return the public VerdantFlare Task ID, final public status, local absolute file path, task record path, and known result metadata. Explain a `PENDING`/`UNKNOWN` submission with its `client_request_id` and tell the user to verify it in the VerdantFlare API Center before creating anything again.

Do not expose JD upstream task IDs, S3 object names, credentials, raw response bodies, signed URL query strings in errors, or `mc` commands.
