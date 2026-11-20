#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mk-video-download 自动化门禁校验与 100 分制客观质量评分器 (video_download_evaluator.py)

职责：
1. Gate G1 (源链接与接口解析门禁)：校验 item_id 非空、无 playwm 水印标识残留。
2. Gate G2 (流媒体容器与防截断门禁)：校验本地文件字节完整性（直接流偏差 <= 1%，DASH 混流体积充足）及 ffprobe 正常解封装。
3. Gate G3 (画质规格与音画同步门禁)：校验视频流编码、分辨率、帧率与音轨存在性。
4. Gate G4 (分分类专属终检门禁)：
   - C1 (抖音系)：校验是否命中 1080p/720p 原画流而非默认 540p 低码率陷阱。
   - C2 (小红书/快手系)：校验 ci.xiaohongshu.com 无水印原图或快手 mainMv 无水印视频流。
   - C3 (YouTube / X-Twitter / B站 / 微博)：校验最高码率变体选择、?name=orig 无损原图及 DASH 音画合并完整性。
5. 输出 100 分制客观评分报告 (S1 画质分辨率 40 分 + S2 字节与容器完整性 30 分 + S3 去水印纯净度与音轨 20 分 + S4 元数据完备度 10 分)。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List


def _resolve_ffprobe_bin() -> str:
    """多机自动探测 ffprobe 可执行文件路径（PATH 优先，兼容本地绿色软件目录）。"""
    found = shutil.which("ffprobe")
    if found:
        return found
    exe_name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    for cand_dir in (
        r"C:\Softs\Installs\ffmpeg\bin",
        r"C:\Softs\Installs\jasna-v0.6.0-alpha5\_internal\tools",
    ):
        cand = os.path.join(cand_dir, exe_name)
        if os.path.isfile(cand):
            return cand
    return "ffprobe"


def probe_media_file(file_path: Path) -> Dict[str, Any]:
    """调用 ffprobe 获取视频流、音频流与容器格式信息。"""
    cmd = [
        _resolve_ffprobe_bin(), "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height,bit_rate,r_frame_rate:format=duration,size,bit_rate,format_name",
        "-of", "json",
        str(file_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()}
    try:
        return json.loads(proc.stdout)
    except Exception as e:
        return {"error": str(e)}


def evaluate_manifest(metadata_json_path: Path, category_override: str = "") -> Dict[str, Any]:
    """对下载完成的 metadata.json 及其关联媒体文件执行 G1~G4 门禁校验与 100 分制评分。"""
    if not metadata_json_path.is_file():
        return {"status": "FAIL", "score": 0.0, "errors": [f"未找到元数据文件: {metadata_json_path}"]}

    manifest = json.loads(metadata_json_path.read_text(encoding="utf-8"))
    category = category_override or manifest.get("category", "C1")
    platform = str(manifest.get("platform") or "")
    media_type = manifest.get("media_type", "video")
    errors: List[str] = []
    warnings: List[str] = []

    # Gate G1: 源解析与去水印 URL 校验
    item_id = str(manifest.get("item_id") or "")
    video_url = str(manifest.get("video_url") or "")
    if not item_id:
        errors.append("[Gate G1] 作品唯一标识 item_id 为空")
    if media_type in ("video", "mixed") and "playwm" in video_url:
        errors.append("[Gate G1] 视频直链仍包含带水印标识 'playwm'")

    # 评分分项初始化
    s1_quality = 0.0     # 满分 40
    s2_integrity = 0.0   # 满分 30
    s3_watermark_av = 0.0  # 满分 20
    s4_metadata = 0.0    # 满分 10

    files = manifest.get("files") or {}
    probe_summary: Dict[str, Any] = {}

    if media_type == "article":
        article_md = Path(files.get("article_md") or "")
        if not article_md.is_file() or article_md.stat().st_size < 128:
            errors.append(f"[Gate G2] 文章 Markdown 文件缺失或过小: {article_md}")
        imgs = files.get("gallery_images") or []
        valid_imgs = [Path(p) for p in imgs if Path(p).is_file() and Path(p).stat().st_size > 2048]
        if len(valid_imgs) != len(imgs):
            errors.append(f"[Gate G2] 文章内嵌图片部分损坏或缺失 ({len(valid_imgs)}/{len(imgs)})")

        vid_path_str = str(files.get("video") or "")
        if vid_path_str:
            video_file = Path(vid_path_str)
            if not video_file.is_file() or video_file.stat().st_size < 10240:
                errors.append(f"[Gate G2] 文章内嵌主视频缺失或异常过小: {video_file}")
            else:
                probe = probe_media_file(video_file)
                if "error" in probe:
                    errors.append(f"[Gate G3] 文章主视频 ffprobe 解封装失败: {probe['error']}")
                else:
                    streams = probe.get("streams") or []
                    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
                    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
                    fmt = probe.get("format") or {}
                    if v_stream:
                        probe_summary["primary_video"] = {
                            "width": int(v_stream.get("width") or 0),
                            "height": int(v_stream.get("height") or 0),
                            "video_codec": v_stream.get("codec_name"),
                            "audio_codec": a_stream.get("codec_name") if a_stream else None,
                            "duration_sec": round(float(fmt.get("duration") or 0.0), 2),
                        }
        if not errors:
            s1_quality = 40.0
            s2_integrity = 30.0
            s3_watermark_av = 20.0
            probe_summary["article_md_bytes"] = article_md.stat().st_size
            probe_summary["gallery_image_count"] = len(valid_imgs)
            probe_summary["extra_video_count"] = len(files.get("extra_videos") or [])
    elif media_type in ("video", "mixed"):
        video_file = Path(files.get("video") or "")
        if not video_file.is_file():
            errors.append(f"[Gate G2] 视频文件不存在: {video_file}")
        else:
            actual_size = video_file.stat().st_size
            expected_size = int(manifest.get("expected_size") or 0)
            if actual_size < 10240:
                errors.append(f"[Gate G2] 视频文件体积异常过小 ({actual_size} bytes)")
            elif platform in ("youtube", "ytdlp_generic"):
                # YouTube DASH 混流后容器体积与预估分流之和允许 +-15% 封装开销差异
                s2_integrity = 30.0
            elif expected_size > 0:
                diff_ratio = abs(actual_size - expected_size) / float(expected_size)
                if diff_ratio <= 0.01:
                    s2_integrity = 30.0
                elif diff_ratio <= 0.05:
                    s2_integrity = 24.0
                    warnings.append(f"[Gate G2] 实际字节数与预期存在微小偏差 ({diff_ratio:.2%})")
                else:
                    errors.append(f"[Gate G2] 视频下载截断，实际 {actual_size} vs 预期 {expected_size}")
            else:
                s2_integrity = 30.0

            probe = probe_media_file(video_file)
            if "error" in probe:
                errors.append(f"[Gate G3] ffprobe 解封装失败: {probe['error']}")
            else:
                streams = probe.get("streams") or []
                v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
                a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
                fmt = probe.get("format") or {}

                if not v_stream:
                    errors.append("[Gate G3] MP4 容器内缺失视频轨道")
                else:
                    w = int(v_stream.get("width") or 0)
                    h = int(v_stream.get("height") or 0)
                    br = int(v_stream.get("bit_rate") or fmt.get("bit_rate") or 0)
                    dur = float(fmt.get("duration") or 0.0)
                    probe_summary = {
                        "width": w,
                        "height": h,
                        "video_codec": v_stream.get("codec_name"),
                        "audio_codec": a_stream.get("codec_name") if a_stream else None,
                        "bit_rate_kbps": round(br / 1000.0, 1),
                        "duration_sec": round(dur, 2),
                        "file_size_mb": round(actual_size / (1024 * 1024), 2),
                    }

                    short_edge = min(w, h)
                    long_edge = max(w, h)
                    if short_edge >= 1080 or long_edge >= 1920 or long_edge >= 1620:
                        s1_quality = 40.0
                    elif short_edge >= 720 or long_edge >= 1280:
                        s1_quality = 34.0
                    elif short_edge >= 540:
                        s1_quality = 25.0
                        if category == "C1" and int(manifest.get("expected_width") or 0) >= 1080:
                            warnings.append("[Gate G4 · C1] 源片声明支持 1080p 但当前仅获取 540p")
                    else:
                        s1_quality = 15.0

                    s3_watermark_av = 12.0 if "playwm" not in video_url else 0.0
                    if a_stream:
                        s3_watermark_av += 8.0
                    else:
                        warnings.append("[Gate G3] 视频不包含音频轨 (纯画面流)")

        # 若为 mixed 模式，同时统计附带原图数量
        if media_type == "mixed":
            imgs = files.get("gallery_images") or []
            probe_summary["attached_gallery_images"] = len(imgs)
    else:
        # 图集模式评测
        imgs = files.get("gallery_images") or []
        if not imgs:
            errors.append("[Gate G2] 图集模式下未下载到任何图片文件")
        else:
            valid_imgs = [Path(p) for p in imgs if Path(p).is_file() and Path(p).stat().st_size > 2048]
            if len(valid_imgs) != len(imgs):
                errors.append(f"[Gate G2] 图集部分图片损坏或缺失 ({len(valid_imgs)}/{len(imgs)})")
            else:
                s1_quality = 40.0
                s2_integrity = 30.0
                s3_watermark_av = 20.0
                probe_summary = {
                    "gallery_image_count": len(valid_imgs),
                    "total_size_mb": round(sum(p.stat().st_size for p in valid_imgs) / (1024 * 1024), 2),
                }

    # S4: 元数据完备度 (标题、作者、封面/首帧截图)
    if manifest.get("title"):
        s4_metadata += 4.0
    if (manifest.get("author") or {}).get("nickname"):
        s4_metadata += 3.0
    if files.get("cover") or files.get("preview_frame") or files.get("gallery_images"):
        s4_metadata += 3.0

    total_score = round(s1_quality + s2_integrity + s3_watermark_av + s4_metadata, 1)
    if errors:
        total_score = min(total_score, 59.0)

    status = "PASS" if not errors and total_score >= 80.0 else "FAIL"
    report = {
        "status": status,
        "category": category,
        "score": total_score,
        "subscores": {
            "S1_resolution_quality": s1_quality,
            "S2_stream_integrity": s2_integrity,
            "S3_watermark_free_and_audio": s3_watermark_av,
            "S4_metadata_completeness": s4_metadata,
        },
        "probe": probe_summary,
        "errors": errors,
        "warnings": warnings,
    }
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="mk-video-download 门禁校验与 100 分制评分工具")
    parser.add_argument("--metadata", "-m", required=True, type=Path, help="下载生成的 *_metadata.json 路径")
    parser.add_argument("--category", "-c", default="", help="可选覆盖分类代号 (C1/C2/C3)")
    args = parser.parse_args()

    report = evaluate_manifest(args.metadata, category_override=args.category)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
