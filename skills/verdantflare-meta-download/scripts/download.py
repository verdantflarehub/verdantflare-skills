"""Download an HTTP resource or resolve a media platform share."""
import argparse
import json
import mimetypes
import re
from email.message import Message
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import unquote, urlsplit

import httpx

from mk_video_download import (
    classify_platform_and_category, execute_download, extract_first_url,
    extract_preview_frame, parse_media_input, sanitize_filename,
)
from research_media import collect_evidence


def download_file(url, directory, research):
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        headers = Message()
        headers["Content-Disposition"] = response.headers.get("content-disposition", "")
        mime = response.headers.get("content-type", "").partition(";")[0].lower()
        raw_name = headers.get_filename() or unquote(response.url.path.split("/")[-1])
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (raw_name or "download"))[:150].strip(" .")
        name = name or "download"
        if not Path(name).suffix:
            name += mimetypes.guess_extension(mime) or ".bin"
        # A fixed prefix also keeps Windows device names and manifest names distinct.
        path = directory / ("resource_" + name)
        partial = directory / "transfer.part"
        try:
            with partial.open("xb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    manifest = {"source_url": url, "content_type": mime, "file": str(path), "bytes": path.stat().st_size}
    if research:
        if mime.startswith("video/") or path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}:
            manifest["research"] = collect_evidence({"video": str(path)}, directory, extract_preview_frame)
        else:
            manifest["research"] = {"document": str(path), "content_type": mime}
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("./.gdrive/downloads"))
    parser.add_argument("--research", action="store_true")
    parser.add_argument("--mode", choices=["auto", "file", "media"], default="auto")
    args = parser.parse_args()
    url = extract_first_url(args.input)
    host = urlsplit(url).hostname
    if not host or urlsplit(url).scheme not in {"http", "https"}:
        parser.error("需要 HTTP(S) 地址")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    directory = Path(mkdtemp(prefix=sanitize_filename(host) + "_", dir=args.output_dir.resolve()))
    _, platform = classify_platform_and_category(url)
    if args.mode == "media" or (args.mode == "auto" and platform != "ytdlp_generic"):
        manifest = execute_download(parse_media_input(args.input), directory)
        if args.research:
            manifest["research"] = collect_evidence(manifest["files"], directory, extract_preview_frame)
    else:
        manifest = download_file(url, directory, args.research)
    index = directory / "manifest.json"
    index.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(index)


if __name__ == "__main__":
    main()
