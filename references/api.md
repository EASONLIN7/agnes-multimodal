# Agnes AI API reference (as used by `scripts/agnes.py`)

Read this when a call fails, when you need a parameter the CLI does not expose, or
when you are adapting the script.

## Endpoints and regions

| Region | Base URL | Notes |
|---|---|---|
| Global | `https://apihub.agnes-ai.com/v1` | default |
| China | `https://api.agnes-ai.cn/v1` | `--region cn` |

Hosts have moved repeatedly. `api.agnes-ai.cn` may answer while
`apihub.agnes-ai.cn` returns 401, and keys are not always portable between the
`.com` and `.cn` platforms. When a call fails with 401 or 404, try the other host
before assuming the key is bad.

Override with `--base <url>` or `AGNES_BASE_URL`. Trailing `/v1` is optional.

## Models

| Kind | Model | Cost |
|---|---|---|
| Text / vision | `agnes-3.0-flash` | free (512K context, thinking mode) |
| Text / vision | `agnes-2.5-flash` | free, faster |
| Image | `agnes-image-2.5-flash` | free |
| Image | `agnes-image-2.1-flash`, `agnes-image-2.0-flash` | free, older |
| Video | `agnes-video-2.5-flash` | free, 720P only, ~1 req/min |
| Video | `agnes-video-2.5` | paid: $0.025/s 720P, $0.040/s 1080P/1K, $0.055/s 2K |
| Text | `agnes-2.5-pro` / `-pro-beta` / `-pro-alpha` | paid reasoning |

`agnes-video-v2.0` was retired around 2026-09-25 - do not target it.
`resolve_model()` in the script queries `GET /v1/models`, prefers free `flash`
builds, and caches the result for 7 days in `~/.codex/.agnes-models-cache.json`.
Delete that file to force a refresh.

## Text and vision

`POST /v1/chat/completions` - OpenAI chat format. Responses, Anthropic-compatible
`/v1/messages` also exist but the chat-completions path is the one that has proven
stable for agent tool loops; multi-turn function calling on the Responses API is
documented as unreliable, so do not build an agent loop on it.

Thinking mode: add `{"thinking": {"type": "enabled"}}`. The final answer may arrive
in `message.content` while reasoning sits in `message.reasoning_content`.

Vision uses the ordinary chat endpoint with OpenAI-style content parts:

```json
{"role": "user", "content": [
  {"type": "text", "text": "..."},
  {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
]}
```

Both public URLs and `data:` URIs are accepted.

## Image generation

`POST /v1/images/generations`

```json
{
  "model": "agnes-image-2.5-flash",
  "prompt": "...",
  "size": "2K",
  "aspect_ratio": "16:9",
  "extra_body": {
    "response_format": "url",
    "image": ["data:image/png;base64,..."]
  }
}
```

- `size` accepts exact `WxH` (`1024x768`, `768x1024`, `1024x1024`) or a tier
  (`1K`/`2K`/`3K`/`4K`) combined with `aspect_ratio`.
- `extra_body.image` drives image-to-image and multi-image composition. Local
  files must be converted to a `data:` URI first (the CLI does this).
- `response_format` belongs inside `extra_body`. Some deployments accept it at the
  top level; the CLI retries that way on a 400.
- `negative_prompt` and `seed` are **not** supported by the image API - only
  `prompt` plus source images participate. Do not send them.
- Response: `{"created": ..., "data": [{"url": "...", "b64_json": null}]}`.

## Video generation

Create: `POST /v1/videos`

```json
{
  "model": "agnes-video-2.5-flash",
  "prompt": "...",
  "mode": "text",
  "seconds": "5",
  "size": "720P",
  "aspect_ratio": "16:9",
  "num_frames": 121,
  "frame_rate": 24
}
```

- `mode`: `text`, `reference` (image/audio reference), `keyframes` (first/last frame).
- `seconds`: string `"4"` to `"12"`.
- `num_frames` must satisfy `8n+1` and be `<= 441` (81/121/161/241/321/361/401/441).
  Mismatched frame counts are a common cause of server errors.
- Flash constraints: 720P only, at most 5 reference images, at most 3 audio clips,
  no video input.
- `first_frame` / `last_frame` are keyframe-mode inputs. Official docs only promise
  public URLs for those fields; base64 may be undocumented behaviour - fall back to
  a public URL if a keyframe task fails.

Retrieve: `GET {root}/agnesapi?video_id=<id>&model_name=<model>` where `root` is the
base URL without `/v1`. The CLI also tries `GET /v1/videos/<id>` as a fallback.
Newer responses return `video_id`; older ones only return `task_id`.

Download URL extraction order (the CLI follows this):

1. `remixed_from_video_id` - despite the name this is where several deployments put
   the final mp4 URL
2. `video_url` -> `url` -> `video` -> `output_url` -> `result_url` -> `download_url`
3. `data[0].url`

Some accounts hit `404` on `/v1/videos/{id}/content`; that endpoint is not the one
used here. If a task completes but no URL surfaces, open the task in the Agnes web
console.

## Aspect ratios

| Ratio | Output |
|---|---|
| `21:9` | 1680x720 |
| `16:9` | 1280x704 - 1280x720 |
| `4:3` | 960x720 |
| `1:1` | 720x720 |
| `3:4` | 720x960 |
| `9:16` | 720x1280 |

## Error codes

| Status | Meaning | Action |
|---|---|---|
| 401 | Key rejected on this host | Retry `--region cn`, then re-check the key |
| 403 | Host or plan blocks the call | Try the other region host |
| 404 | Wrong host, model, or retired model | `models` to see what exists |
| 429 | Rate limit (text ~10 RPM, video stricter) | Wait; back off. Video is far stricter than the documented "1/min": rejected attempts also appear to count, so the window can stay shut for several minutes. Do not retry in a loop - wait minutes, not seconds. |
| 5xx | Provider-side error | Retry; `division by zero` on video is a known server bug |

## Operational notes

- Generation requests can take 30-180s. Use a timeout of at least 120s for POSTs
  and 360s if you call the API directly.
- Never log or print the API key, and never pass it on a command line where the OS
  process list can read it. Environment variables and config files are supported
  for exactly this reason.
- Chinese text cannot be rendered inside generated video. Generate text-free scenes
  and add subtitles afterwards (for example with ffmpeg `ass=` filters).
