---
name: agnes-multimodal
description: Generate images or videos, edit a source image, or turn images into text through the Agnes AI multimodal API. Use when the user wants a picture, illustration, poster, cover, logo, sticker, sprite, or product mockup made; when a task, document, deck, web page, or game needs new visual assets; when they want a clip, animation, or image-to-video; or when images must be described, OCR'd, or converted to text by Agnes in bulk.
metadata:
  short-description: Agnes AI images, video and vision
---

# Agnes Multimodal

Agnes AI (Sapiens AI) exposes one OpenAI-compatible API for text, image generation,
video generation and image understanding. This skill wraps all of it in a single
zero-dependency CLI so you can call it mid-conversation or mid-task.

**Compatibility:** the CLI is plain HTTPS plus the standard library, so it is
independent of which model backend Codex is running on. That includes **Codex
connected to DeepSeek** (`model_provider = "deepseek"` in `config.toml`) — the skill
calls the Agnes endpoint directly and never touches the chat model.

Entry point:

```bash
python <skill>/scripts/agnes.py <command> [args]
```

Everything below assumes `PY` is a Python 3.9+ interpreter; on Windows use the
Codex runtime Python if `python` is not on PATH.

## Use this skill when

- The user asks for an image, video, poster, cover, logo, icon, sticker, sprite sheet,
  product mockup, or any other visual asset to be created.
- A deliverable you are producing (document, deck, web page, game, README) needs
  new artwork rather than a stock or placeholder asset.
- The user wants an existing image restyled, edited, upscaled into a different
  aspect ratio, or turned into a short clip.
- A batch of images must be described, transcribed, or reduced to structured text.

## Do not use this skill when

- The user only wants to look at an image. Codex already reads images natively;
  reach for `vision` only when you need Agnes-specific output (bulk OCR, a text
  pipeline, or a written description to feed another tool).
- A vector/CSS/SVG asset would serve better than a raster image.
- The request is about editing local code or documents with no new visual asset.

## One-time setup

The CLI reads the key from the environment, then from a config file:

```powershell
$env:AGNES_API_KEY = "sk-..."          # current session
[Environment]::SetEnvironmentVariable("AGNES_API_KEY", "sk-...", "User")   # persistent
```

Accepted variables: `AGNES_API_KEY` (preferred), `AGNES_API_TOKEN`,
`APIHUB_AGNES_API_KEY`. Config file alternative: `~/.codex/agnes.json` or
`~/.agnes/api.json` containing `{"api_key": "sk-..."}`.

Verify before the first real request:

```bash
python scripts/agnes.py check
```

## Hard rules

1. **Never read image or video bytes into context.** No base64 in your reply, no
   opening generated media with a viewer tool. A single 1024x768 PNG is roughly
   1,000,000 base64 characters - enough to destroy the context window. Confirm a
   file exists with its path and size only (`Get-Item <path> | Select-Object Name, Length`).
2. **Always save to disk.** Pass `--output <absolute-path>` (or `--outdir`) so the
   result is a file you can hand back. Then show the user the saved path.
3. **Prefer the free tier.** `agnes-image-*-flash`, `agnes-video-*-flash`,
   `agnes-3.0-flash` and `agnes-2.5-flash` are free. Only `agnes-video-2.5`
   (no `-flash`) costs money (~$0.025/s at 720P). Do not submit a paid video task
   unless the user explicitly asks for it and accepts the cost.
4. **Respect rate limits.** Text is ~10 RPM; video is ~1 request/minute. Space out
   video calls; a 5-second clip takes roughly 2-6 minutes to render.
5. **Pass through what the user asked for.** Keep their subject, style, framing and
   constraints. Do not invent brand names, real people, or text that must be exact
   unless the user supplied it.

## Commands

| Goal | Command |
|---|---|
| Verify key | `agnes.py check` |
| List models | `agnes.py models --kind image` |
| Text / reasoning | `agnes.py text "..." [--thinking]` |
| Describe / OCR an image | `agnes.py vision <path\|url> [--prompt "..."]` |
| Text-to-image | `agnes.py image "prompt" --size 2K --ratio 16:9 --output cover.png` |
| Image-to-image edit | `agnes.py edit <source> "instruction" --output out.png` |
| Text/image-to-video | `agnes.py video "prompt" [--image ref.png] --seconds 5 --output clip.mp4` |
| Poll a video task | `agnes.py video-status <video_id> --model agnes-video-2.5-flash` |
| zh -> en prompt | `agnes.py translate "一只猫"` |

Add `--json` for a single parseable line, `--raw` to inspect the API response while
debugging. Dimensions accept exact `WxH` or a tier (`1K`/`2K`/`3K`/`4K`) combined
with `--ratio`.

## Prompting

- Chinese (or any non-ASCII) prompts are translated to English automatically before
  generation, because Agnes renders English prompts more reliably. The translated
  prompt is printed back; use `--no-translate` to send the original as-is.
- For images, state subject, scene, style, lighting, composition and intended
  aspect ratio. For video, state camera motion and what changes over time.
- Match the ratio to the destination: `16:9` covers/video, `3:4` note covers,
  `9:16` short-form video, `1:1` avatars.
- Never ask Agnes to render exact text if the wording matters - it mangles
  lettering, and it cannot render Chinese characters inside video at all. Compose
  text afterwards in HTML, an editor, or with ffmpeg.

## Troubleshooting

Run `check` first - it distinguishes a bad key (401) from a wrong host (404).
Endpoints move between regions: if the global host fails, retry with `--region cn`
(`https://api.agnes-ai.cn/v1`). A key issued on one region may not work on the other.
Video results are sometimes exposed only through `remixed_from_video_id`; the CLI
already checks that field first, and `--raw` shows the full response when a task
finishes without a URL.

For endpoint details, model list, parameter constraints and known pitfalls, read
[references/api.md](references/api.md).
