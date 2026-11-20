"""Prepare time-indexed evidence from the files actually downloaded."""
from pathlib import Path
from tempfile import mkdtemp

from video_download_evaluator import probe_media_file


def collect_evidence(files, directory, extract_frame):
    videos = [
        *([files["video"]] if files.get("video") else []),
        *files.get("extra_videos", []),
        *files.get("gallery_live_videos", []),
    ]
    evidence = {"videos": [], "images": files.get("gallery_images", [])}
    if files.get("article_md"):
        evidence["article"] = files["article_md"]
    for video in videos:
        path = Path(video)
        probe = probe_media_file(path)
        duration = float(probe.get("format", {}).get("duration") or 0)
        if duration <= 0:
            raise ValueError(f"无法取得视频时长: {path.name}")
        frame_dir = Path(mkdtemp(prefix=f"{path.stem}_frames_", dir=directory))
        frames = []
        for index in range(5):
            seconds = round(duration * index / 5, 3)
            frame = frame_dir / f"{index:02}.jpg"
            if extract_frame(path, frame, seconds) and frame.stat().st_size:
                frames.append({"seconds": seconds, "file": frame.as_posix()})
        if not frames:
            raise ValueError(f"无法读取视频画面: {path.name}")
        evidence["videos"].append({"file": video, "duration_sec": duration, "frames": frames})
    if not any(evidence.values()):
        raise ValueError("下载清单缺少可研究的媒体")
    return evidence
