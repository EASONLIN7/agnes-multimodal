# Agnes Multimodal — a Codex skill

![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-0078D6)
![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)
![Dependencies](https://img.shields.io/badge/dependencies-none-3DD68C)
![Codex skill](https://img.shields.io/badge/Codex-skill-7C3AED)

One OpenAI-compatible API for **text, image generation, video generation and image
understanding**, wrapped in a single zero-dependency CLI and packaged as a
[Codex skill](https://github.com/openai/skills).

Point it at an Agnes AI (Sapiens AI) key and you can generate a cover image mid-task,
turn a still into a short clip, or OCR a folder of images — without leaving the
conversation and without installing anything.

**Compatibility — works with any Codex backend, including DeepSeek.** The CLI talks
straight to the Agnes HTTPS endpoint through the standard library, so it does not
care which model Codex itself is running. If your Codex is pointed at DeepSeek
(`model_provider = "deepseek"` / `deepseek-codex`), this skill works unchanged:
DeepSeek drives the conversation, Agnes produces the images and video.

```
SKILL.md                 skill definition — when to use it, hard rules, command map
agents/openai.yaml       Codex UI metadata (display name, brand color, default prompt)
references/api.md        endpoint details, model list, parameter limits, pitfalls
scripts/agnes.py         the CLI — Python 3.9+, standard library only
```

## Install

### As a Codex skill

Copy (or symlink) this folder so that `SKILL.md` sits at the skill root:

```powershell
# Windows
git clone https://github.com/<you>/agnes-multimodal "$env:USERPROFILE\.codex\skills\agnes-multimodal"
```

```bash
# macOS / Linux
git clone https://github.com/<you>/agnes-multimodal "${CODEX_HOME:-$HOME/.codex}/skills/agnes-multimodal"
```

Codex picks the skill up automatically; `$agnes-multimodal` invokes it explicitly,
and it can also be triggered implicitly when a task needs a visual asset.

### Standalone

The CLI is self-contained — clone it anywhere and call `scripts/agnes.py` directly:

```bash
python scripts/agnes.py check
```

No `pip install`, no virtualenv. Only Python 3.9+ and network access.

## Configure the key

The CLI reads the key from the environment first, then from a config file:

```powershell
$env:AGNES_API_KEY = "sk-..."                                        # current session
[Environment]::SetEnvironmentVariable("AGNES_API_KEY", "sk-...", "User")   # persistent
```

```bash
export AGNES_API_KEY="sk-..."          # macOS / Linux
```

Accepted variables: `AGNES_API_KEY` (preferred), `AGNES_API_TOKEN`,
`APIHUB_AGNES_API_KEY`. Config-file alternative: `~/.codex/agnes.json` or
`~/.agnes/api.json` containing `{"api_key": "sk-..."}`.

Verify before the first real request — `check` tells a bad key (401) apart from a
wrong host (404):

```bash
python scripts/agnes.py check
```

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
| zh → en prompt | `agnes.py translate "一只猫"` |
| End-to-end connectivity | `agnes.py smoke-test` |

Add `--json` for a single parseable line, `--raw` to inspect the API response while
debugging. Dimensions accept an exact `WxH` or a tier (`1K`/`2K`/`3K`/`4K`) combined
with `--ratio`. If the global host fails, retry with `--region cn`.

## Models and cost

These are **free**:

- `agnes-image-*-flash`
- `agnes-video-*-flash`
- `agnes-3.0-flash`, `agnes-2.5-flash`

`agnes-video-2.5` (no `-flash` suffix) is **paid** — roughly `$0.025/s` at 720P.
The skill's hard rules tell the agent not to submit a paid video task unless you
explicitly ask for it and accept the cost.

Rate limits: text ≈ 10 RPM, video ≈ 1 request/minute. A 5-second clip takes roughly
2–6 minutes to render, so the CLI polls and reports progress.

## Practical notes

- **Non-ASCII prompts are auto-translated to English** before generation and the
  translated prompt is printed back. Use `--no-translate` to send the original.
- **Never ask Agnes to render exact text.** It mangles lettering and cannot render
  Chinese characters inside video at all. Compose text afterwards in HTML, an
  editor, or with ffmpeg.
- **Match the ratio to the destination:** `16:9` covers and video, `3:4` note
  covers, `9:16` short-form video, `1:1` avatars.
- **Always write to disk.** Pass `--output <absolute path>`; the CLI hands back a
  path rather than dumping bytes into the conversation.

Full endpoint and parameter details live in [`references/api.md`](references/api.md).

## Requirements

- Python 3.9 or newer
- An Agnes AI API key
- Network access to `api.agnes-ai.com` (or `api.agnes-ai.cn` with `--region cn`)

## License

No license has been chosen yet — add a `LICENSE` file before publishing if you want
others to reuse this.
