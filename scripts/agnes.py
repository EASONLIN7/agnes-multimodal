#!/usr/bin/env python3
"""Agnes AI multimodal CLI for the `agnes-multimodal` Codex skill.

Subcommands:
  check          Verify the API key and report which models are reachable.
  models         List models (optionally filtered by kind).
  text           Text / reasoning generation.
  vision         Image -> text (describe, OCR, extract structured info).
  image          Text-to-image (optionally with reference images).
  edit           Image-to-image edit / restyle.
  video          Text-to-video, image-to-video, keyframes, reference.
  video-status   Poll an existing video task.
  translate      Prompt zh->en helper (image/video commands do this automatically).
  smoke-test     End-to-end connectivity check.

Design rules -- do not relax these:
  * Standard library only (urllib). No pip install required.
  * NEVER print image/video bytes or base64 to stdout. Save to disk, print paths.
  * Emit one compact JSON object with --json, else a short human summary.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

REGIONS = {
    "global": "https://apihub.agnes-ai.com/v1",
    "cn": "https://api.agnes-ai.cn/v1",
}
DEFAULT_BASE = REGIONS["global"]

# Fallbacks used only when /v1/models cannot be reached.
FALLBACK_MODELS = {
    "text": "agnes-3.0-flash",
    "image": "agnes-image-2.5-flash",
    "video": "agnes-video-2.5-flash",
}

KEY_ENV_VARS = ("AGNES_API_KEY", "AGNES_API_TOKEN", "APIHUB_AGNES_API_KEY")
MODEL_CACHE_TTL = 7 * 24 * 3600


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def config_paths() -> list[Path]:
    return [
        Path.home() / ".agnes" / "api.json",
        codex_home() / "agnes.json",
    ]


def cache_path() -> Path:
    return codex_home() / ".agnes-models-cache.json"


def load_config() -> dict:
    cfg: dict = {}
    for path in config_paths():
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        cfg.setdefault(k, v)
        except Exception:
            continue
    return cfg


def resolve_credentials(args) -> tuple[str, str]:
    cfg = load_config()

    api_key = ""
    for var in KEY_ENV_VARS:
        if os.environ.get(var):
            api_key = os.environ[var].strip()
            break
    if not api_key:
        for field in ("api_key", "apiKey", "key", "token"):
            if cfg.get(field):
                api_key = str(cfg[field]).strip()
                break

    base = (
        getattr(args, "base", None)
        or os.environ.get("AGNES_BASE_URL")
        or cfg.get("base_url")
        or REGIONS.get((getattr(args, "region", None) or "").lower())
        or DEFAULT_BASE
    )
    base = base.rstrip("/")
    if not base.endswith("/v1") and "/v1" not in base:
        base = base + "/v1"
    return api_key, base


def require_key(api_key: str) -> None:
    if api_key:
        return
    print(
        "Agnes API key not found.\n"
        "\n"
        "Set one of these environment variables:\n"
        "  AGNES_API_KEY        (preferred)\n"
        "  AGNES_API_TOKEN\n"
        "  APIHUB_AGNES_API_KEY\n"
        "\n"
        "PowerShell (current session):  $env:AGNES_API_KEY=\"sk-...\"\n"
        "PowerShell (persistent):       [Environment]::SetEnvironmentVariable('AGNES_API_KEY','sk-...','User')\n"
        "\n"
        "Or write {\"api_key\":\"sk-...\"} to ~/.codex/agnes.json or ~/.agnes/api.json.",
        file=sys.stderr,
    )
    raise SystemExit(2)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:600]}")
        self.status = status
        self.body = body


def http_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict | None = None,
    timeout: int = 180,
    retries: int = 3,
) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "codex-agnes-multimodal/1.0",
    }
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(60, 5 * (2**attempt)))
                last = ApiError(exc.code, body)
                continue
            raise ApiError(exc.code, body) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(min(30, 3 * (2**attempt)))
                last = exc
                continue
            raise ApiError(0, f"network error: {exc}") from None
    raise ApiError(0, f"request failed: {last}")


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
    return dest


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


def list_models(api_key: str, base: str) -> list[str]:
    data = http_json("GET", f"{base}/models", api_key, timeout=60, retries=1)
    items = data.get("data") or data.get("models") or []
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name")
        else:
            name = str(item)
        if name:
            names.append(str(name))
    return names


_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


def _version_key(name: str) -> tuple:
    match = _VERSION_RE.search(name)
    major, minor = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    # Prefer flash (cheap/free) over pro/alpha/beta when auto-selecting.
    penalty = 0 if "flash" in name else 1
    return (penalty, -major, -minor)


def resolve_model(api_key: str, base: str, kind: str, override: str | None) -> str:
    if override:
        return override

    cache_file = cache_path()
    try:
        if cache_file.is_file():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - cached.get("ts", 0) < MODEL_CACHE_TTL:
                hit = cached.get("models", {}).get(kind)
                if hit:
                    return hit
    except Exception:
        pass

    resolved: dict[str, str] = {}
    try:
        names = list_models(api_key, base)
        prefixes = {
            "image": "agnes-image-",
            "video": "agnes-video-",
            "text": "agnes-",
        }
        for k, prefix in prefixes.items():
            if k == "text":
                pool = [
                    n
                    for n in names
                    if prefix in n and "image" not in n and "video" not in n
                ]
            else:
                pool = [n for n in names if n.startswith(prefix)]
            if pool:
                resolved[k] = sorted(pool, key=_version_key)[0]
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"ts": time.time(), "models": resolved}),
                encoding="utf-8",
            )
        except Exception:
            pass
    except Exception:
        pass

    return resolved.get(kind) or FALLBACK_MODELS[kind]


# --------------------------------------------------------------------------
# Input helpers
# --------------------------------------------------------------------------


def as_data_uri(ref: str) -> str:
    """URL / data URI pass through; local path -> base64 data URI."""
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    path = Path(ref).expanduser()
    if not path.is_file():
        raise SystemExit(f"Reference image not found: {ref}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def is_non_ascii(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text)


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^0-9A-Za-z\-]+", "-", text.strip())[:limit].strip("-")
    return slug or "output"


def output_dir(args) -> Path:
    raw = args.outdir or os.environ.get("AGNES_OUTPUT_DIR") or "./agnes_output"
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def emit(payload: dict, lines: list[str], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


# --------------------------------------------------------------------------
# Prompt translation (image/video stability)
# --------------------------------------------------------------------------

TRANSLATE_SYSTEM = (
    "You translate prompts for an image/video generation model into English. "
    "Preserve subject, scene, style, lighting, composition, camera motion and every "
    "explicit constraint. Do not add new ideas, commentary or quotes. "
    "Output only the translated English prompt."
)


def maybe_translate(api_key: str, base: str, prompt: str, enabled: bool) -> tuple[str, bool]:
    if not enabled or not is_non_ascii(prompt):
        return prompt, False
    try:
        chat_model = resolve_model(api_key, base, "text", None)
        data = http_json(
            "POST",
            f"{base}/chat/completions",
            api_key,
            {
                "model": chat_model,
                "messages": [
                    {"role": "system", "content": TRANSLATE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
            },
            timeout=90,
            retries=1,
        )
        text = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
        text = (text or "").strip().strip('"')
        if text:
            return text, True
    except Exception:
        pass
    return prompt, False


def chat_text(api_key: str, base: str, model: str, messages: list, thinking: bool) -> dict:
    payload: dict = {"model": model, "messages": messages}
    if thinking:
        payload["thinking"] = {"type": "enabled"}
    return http_json("POST", f"{base}/chat/completions", api_key, payload)


# --------------------------------------------------------------------------
# Media result helpers
# --------------------------------------------------------------------------

_VIDEO_URL_FIELDS = (
    "video_url",
    "url",
    "video",
    "output_url",
    "result_url",
    "download_url",
    "remixed_from_video_id",
)


def extract_media_url(data: dict, prefer: tuple[str, ...]) -> str | None:
    for key in prefer:
        value = data.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    for container in (data.get("data"), data.get("output"), data.get("result")):
        entries = container if isinstance(container, list) else [container]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in prefer:
                value = entry.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value
        if isinstance(container, dict):
            for key in prefer:
                value = container.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value
    return None


def extract_status(data: dict) -> str:
    for key in ("status", "state", "task_status"):
        value = data.get(key)
        if isinstance(value, str):
            return value.lower()
    return ""


def save_image_result(data: dict, args, prompt: str, model: str) -> dict:
    url = extract_media_url(data, ("url", "image_url"))
    b64 = None
    for entry in data.get("data") or []:
        if isinstance(entry, dict):
            b64 = b64 or entry.get("b64_json")
            url = url or entry.get("url")
    b64 = b64 or data.get("b64_json")

    saved: list[str] = []
    if b64:
        dest = Path(args.output).expanduser() if args.output else (
            output_dir(args) / f"{time.strftime('%Y%m%d-%H%M%S')}-{slugify(prompt)}.png"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(b64))
        saved.append(str(dest.resolve()))
    elif url:
        dest = Path(args.output).expanduser() if args.output else (
            output_dir(args) / f"{time.strftime('%Y%m%d-%H%M%S')}-{slugify(prompt)}.png"
        )
        try:
            download(url, dest)
            saved.append(str(dest.resolve()))
        except Exception:
            pass

    return {"model": model, "prompt": prompt, "url": url, "files": saved}


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_check(args, api_key: str, base: str) -> int:
    info = {"base_url": base, "key_present": bool(api_key), "ok": False}
    if not api_key:
        emit(info, ["No API key configured. See `--help` / SKILL.md for setup."], args.json)
        return 2
    try:
        names = list_models(api_key, base)
    except ApiError as exc:
        hint = {
            401: "Key rejected. Check the key, or try the other region (--region cn).",
            403: "Key lacks access, or the endpoint host is wrong.",
            404: "Wrong host/model. Try --region cn or --base https://apihub.agnes-ai.com/v1.",
        }.get(exc.status, "Unexpected response.")
        info["error"] = f"HTTP {exc.status}"
        emit(info, [f"FAILED ({exc.status}). {hint}\n{exc.body[:300]}"], args.json)
        return 1

    kinds = {
        "text": [n for n in names if "image" not in n and "video" not in n and n.startswith("agnes-")],
        "image": [n for n in names if n.startswith("agnes-image-")],
        "video": [n for n in names if n.startswith("agnes-video-")],
    }
    info.update({"ok": True, "model_count": len(names), "models": kinds})
    lines = [
        f"OK - key valid. {len(names)} models reachable at {base}",
        f"  text : {', '.join(kinds['text'][:4]) or '-'}",
        f"  image: {', '.join(kinds['image'][:4]) or '-'}",
        f"  video: {', '.join(kinds['video'][:4]) or '-'}",
    ]
    emit(info, lines, args.json)
    return 0


def cmd_models(args, api_key: str, base: str) -> int:
    require_key(api_key)
    names = list_models(api_key, base)
    if args.kind:
        prefix = "agnes-image-" if args.kind == "image" else "agnes-video-" if args.kind == "video" else ""
        if args.kind == "text":
            names = [n for n in names if "image" not in n and "video" not in n]
        else:
            names = [n for n in names if n.startswith(prefix)]
    emit({"base_url": base, "models": names}, names, args.json)
    return 0


def cmd_text(args, api_key: str, base: str) -> int:
    require_key(api_key)
    model = resolve_model(api_key, base, "text", args.model)
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})
    data = chat_text(api_key, base, model, messages, args.thinking)
    message = (data.get("choices", [{}])[0].get("message", {}) or {})
    content = (message.get("content") or "").strip()
    reasoning = message.get("reasoning_content")
    payload = {"model": model, "content": content}
    if reasoning:
        payload["reasoning"] = reasoning
    if args.raw:
        payload["raw"] = data
    emit(payload, [content], args.json)
    return 0


def cmd_vision(args, api_key: str, base: str) -> int:
    require_key(api_key)
    model = resolve_model(api_key, base, "text", args.model)
    prompt = args.prompt or "Describe this image in detail. Transcribe any visible text."
    content: list[dict] = [{"type": "text", "text": prompt}]
    for ref in args.image:
        content.append({"type": "image_url", "image_url": {"url": as_data_uri(ref)}})
    data = chat_text(api_key, base, model, [{"role": "user", "content": content}], False)
    text = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
    payload = {"model": model, "images": args.image, "content": (text or "").strip()}
    if args.raw:
        payload["raw"] = data
    emit(payload, [(text or "").strip()], args.json)
    return 0


def _image_call(api_key: str, base: str, payload: dict) -> dict:
    try:
        return http_json("POST", f"{base}/images/generations", api_key, payload)
    except ApiError as exc:
        # Some deployments take response_format at the top level instead of extra_body.
        if exc.status == 400 and "extra_body" in payload:
            retry = dict(payload)
            extra = retry.pop("extra_body")
            retry.update(extra)
            return http_json("POST", f"{base}/images/generations", api_key, retry)
        raise


def cmd_image(args, api_key: str, base: str) -> int:
    require_key(api_key)
    model = resolve_model(api_key, base, "image", args.model)
    prompt, translated = maybe_translate(api_key, base, args.prompt, not args.no_translate)

    payload: dict = {"model": model, "prompt": prompt}
    if args.size:
        payload["size"] = args.size
    if args.ratio:
        payload["aspect_ratio"] = args.ratio
    extra: dict = {"response_format": "url"}
    if args.transparent:
        extra["transparent"] = True
    if args.image:
        extra["image"] = [as_data_uri(ref) for ref in args.image]
    payload["extra_body"] = extra

    data = _image_call(api_key, base, payload)
    result = save_image_result(data, args, prompt, model)
    result["translated_prompt"] = translated
    if args.raw:
        result["raw"] = data

    lines = [f"image: {model}"]
    if translated:
        lines.append(f"prompt (en): {prompt}")
    if result["files"]:
        lines.extend(f"file: {p}" for p in result["files"])
    if result["url"]:
        lines.append(f"url: {result['url']}")
    if not result["files"] and not result["url"]:
        lines.append("no image returned - rerun with --raw to inspect the response")
    emit(result, lines, args.json)
    return 0


def cmd_edit(args, api_key: str, base: str) -> int:
    args.image = list(args.image) + [args.source]
    return cmd_image(args, api_key, base)


def _video_retrieve_urls(base: str, video_id: str, model: str) -> list[str]:
    root = base[:-3] if base.endswith("/v1") else base
    query = urllib.parse.urlencode({"video_id": video_id, "model_name": model})
    return [
        f"{root}/agnesapi?{query}",
        f"{base}/videos/{video_id}",
    ]


def poll_video(api_key: str, base: str, video_id: str, model: str, interval: int, timeout: int) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        for url in _video_retrieve_urls(base, video_id, model):
            try:
                last = http_json("GET", url, api_key, timeout=60, retries=1)
            except ApiError:
                continue
            status = extract_status(last)
            if status in ("completed", "succeeded", "success", "failed", "error"):
                return last
            if extract_media_url(last, _VIDEO_URL_FIELDS):
                return last
            break
        time.sleep(interval)
    return last


def cmd_video(args, api_key: str, base: str) -> int:
    require_key(api_key)
    model = resolve_model(api_key, base, "video", args.model)
    prompt, translated = maybe_translate(api_key, base, args.prompt, not args.no_translate)

    mode = args.mode
    if not mode:
        mode = "keyframes" if (args.first_frame or args.last_frame) else (
            "reference" if args.image else "text"
        )

    payload: dict = {"model": model, "prompt": prompt, "mode": mode}
    if args.seconds:
        payload["seconds"] = str(args.seconds)
    if args.ratio:
        payload["aspect_ratio"] = args.ratio
    payload["size"] = args.size or "720P"
    if args.frames:
        payload["num_frames"] = args.frames
    if args.fps:
        payload["frame_rate"] = args.fps
    if args.seed is not None:
        payload["seed"] = args.seed
    if args.first_frame:
        payload["first_frame"] = as_data_uri(args.first_frame)
    if args.last_frame:
        payload["last_frame"] = as_data_uri(args.last_frame)
    if args.image:
        payload["images"] = [as_data_uri(ref) for ref in args.image]

    created = http_json("POST", f"{base}/videos", api_key, payload, timeout=180)
    video_id = (
        created.get("video_id")
        or created.get("id")
        or created.get("task_id")
        or created.get("data", {}).get("video_id")
    )
    result: dict = {
        "model": model,
        "prompt": prompt,
        "translated_prompt": translated,
        "mode": mode,
        "video_id": video_id,
    }

    if not video_id:
        result["raw_create"] = created
        emit(result, ["Video task created but no video_id returned - rerun with --raw."], args.json)
        return 1

    if args.no_poll:
        emit(result, [f"submitted: video_id={video_id} (not waiting)"], args.json)
        return 0

    final = poll_video(api_key, base, video_id, model, args.interval, args.timeout)
    result["status"] = extract_status(final) or "unknown"
    url = extract_media_url(final, _VIDEO_URL_FIELDS)
    result["url"] = url
    if url and args.output:
        try:
            result["files"] = [str(download(url, Path(args.output).expanduser()).resolve())]
        except Exception:
            pass
    if args.raw:
        result["raw"] = final

    if not url and result["status"] not in ("completed", "succeeded", "success"):
        emit(result, [f"video task {video_id}: status={result['status']} (not finished or failed)"], args.json)
        return 1

    lines = [f"video: {model} ({result['status']})"]
    if translated:
        lines.append(f"prompt (en): {prompt}")
    if url:
        lines.append(f"url: {url}")
    for path in result.get("files", []):
        lines.append(f"file: {path}")
    emit(result, lines, args.json)
    return 0


def cmd_video_status(args, api_key: str, base: str) -> int:
    require_key(api_key)
    model = args.model or FALLBACK_MODELS["video"]
    final = poll_video(api_key, base, args.video_id, model, args.interval, args.timeout)
    url = extract_media_url(final, _VIDEO_URL_FIELDS)
    payload = {
        "video_id": args.video_id,
        "model": model,
        "status": extract_status(final) or "unknown",
        "url": url,
    }
    if args.raw:
        payload["raw"] = final
    emit(payload, [f"{payload['status']}: {url or args.video_id}"], args.json)
    return 0


def cmd_translate(args, api_key: str, base: str) -> int:
    require_key(api_key)
    model = resolve_model(api_key, base, "text", args.model)
    text, _ = maybe_translate(api_key, base, args.text, True)
    emit({"model": model, "text": text}, [text], args.json)
    return 0


def cmd_smoke_test(args, api_key: str, base: str) -> int:
    require_key(api_key)
    report: dict = {"base_url": base, "steps": {}}
    ok = True

    try:
        models = list_models(api_key, base)
        report["steps"]["models"] = f"ok ({len(models)})"
    except ApiError as exc:
        report["steps"]["models"] = f"failed ({exc.status})"
        emit(report, [f"models: FAILED {exc.status} - {exc.body[:200]}"], args.json)
        return 1

    try:
        text_model = resolve_model(api_key, base, "text", None)
        data = chat_text(api_key, base, text_model, [{"role": "user", "content": "Reply with the single word: pong"}], False)
        reply = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
        report["steps"]["text"] = f"ok ({reply[:30]})"
    except ApiError as exc:
        ok = False
        report["steps"]["text"] = f"failed ({exc.status})"

    if args.full:
        try:
            image_model = resolve_model(api_key, base, "image", None)
            data = _image_call(
                api_key,
                base,
                {
                    "model": image_model,
                    "prompt": "a single small blue ceramic cup on a plain white background",
                    "size": "1024x1024",
                    "extra_body": {"response_format": "url"},
                },
            )
            report["steps"]["image"] = "ok" if extract_media_url(data, ("url",)) else "no url returned"
        except ApiError as exc:
            ok = False
            report["steps"]["image"] = f"failed ({exc.status})"

    report["ok"] = ok
    emit(report, [f"{k}: {v}" for k, v in report["steps"].items()], args.json)
    return 0 if ok else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="machine-readable single-line JSON output")
    p.add_argument("--raw", action="store_true", help="include the raw API response (debugging)")
    p.add_argument("--region", choices=sorted(REGIONS), help="endpoint region shortcut")
    p.add_argument("--base", help="override API base URL")
    p.add_argument("--model", help="force a specific model")


def add_media(p: argparse.ArgumentParser) -> None:
    p.add_argument("--outdir", help="directory for generated files (default ./agnes_output)")
    p.add_argument("--output", help="explicit output file path")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agnes",
        description="Agnes AI multimodal CLI (text / vision / image / video).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="verify key and reachable models")
    add_common(p)
    p.set_defaults(func=cmd_check, needs_key=False)

    p = sub.add_parser("models", help="list models")
    add_common(p)
    p.add_argument("--kind", choices=["text", "image", "video"])
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("text", help="text generation")
    add_common(p)
    p.add_argument("prompt")
    p.add_argument("--system")
    p.add_argument("--thinking", action="store_true")
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("vision", help="image -> text")
    add_common(p)
    p.add_argument("image", nargs="+", help="local path, URL or data URI")
    p.add_argument("--prompt")
    p.set_defaults(func=cmd_vision)

    p = sub.add_parser("image", help="text-to-image")
    add_common(p)
    add_media(p)
    p.add_argument("prompt")
    p.add_argument("--size", help="WxH or tier: 1K / 2K / 3K / 4K")
    p.add_argument("--ratio", help="aspect ratio, e.g. 16:9, 3:4, 1:1")
    p.add_argument("--image", action="append", default=[], help="reference image (repeatable)")
    p.add_argument("--transparent", action="store_true")
    p.add_argument("--no-translate", action="store_true", help="skip automatic zh->en translation")
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("edit", help="image-to-image edit")
    add_common(p)
    add_media(p)
    p.add_argument("source", help="source image: path, URL or data URI")
    p.add_argument("prompt")
    p.add_argument("--size")
    p.add_argument("--ratio")
    p.add_argument("--image", action="append", default=[], help="extra reference image (repeatable)")
    p.add_argument("--transparent", action="store_true")
    p.add_argument("--no-translate", action="store_true")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("video", help="text/image -> video")
    add_common(p)
    add_media(p)
    p.add_argument("prompt")
    p.add_argument("--mode", choices=["text", "reference", "keyframes"])
    p.add_argument("--image", action="append", default=[], help="reference image (repeatable, max 5)")
    p.add_argument("--first-frame")
    p.add_argument("--last-frame")
    p.add_argument("--seconds", default="5", help="4-12 (default 5)")
    p.add_argument("--ratio", default="16:9")
    p.add_argument("--size", default="720P", help="flash supports 720P only")
    p.add_argument("--frames", type=int, help="8n+1, max 441")
    p.add_argument("--fps", type=int)
    p.add_argument("--seed", type=int)
    p.add_argument("--interval", type=int, default=10, help="poll interval seconds")
    p.add_argument("--timeout", type=int, default=600, help="max wait seconds")
    p.add_argument("--no-poll", action="store_true", help="submit only, do not wait")
    p.add_argument("--no-translate", action="store_true")
    p.set_defaults(func=cmd_video)

    p = sub.add_parser("video-status", help="poll an existing video task")
    add_common(p)
    p.add_argument("video_id")
    p.add_argument("--interval", type=int, default=10)
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(func=cmd_video_status)

    p = sub.add_parser("translate", help="zh -> en prompt helper")
    add_common(p)
    p.add_argument("text")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("smoke-test", help="end-to-end connectivity check")
    add_common(p)
    p.add_argument("--full", action="store_true", help="also run one real image generation")
    p.set_defaults(func=cmd_smoke_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy codepage; force UTF-8 so Chinese
    # prompts and descriptions survive the pipe back to the agent.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    api_key, base = resolve_credentials(args)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 1
    try:
        return args.func(args, api_key, base)
    except ApiError as exc:
        hint = {
            401: "Key rejected - check the key, or try --region cn.",
            404: "Wrong host or model - try --region cn or another --base.",
            429: "Rate limited - wait and retry (text 10 RPM, video ~1/min).",
        }.get(exc.status, "")
        print(f"error: HTTP {exc.status} {hint}\n{exc.body[:500]}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
