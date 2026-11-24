#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mk-video-download 总控调度与全网无水印视频/图集解析下载器 (mk_video_download.py)

职责：
1. Stage 1 (口令净化与分类路由)：从混杂分享口令或 URL 中提取干净链接，
   识别平台归属并自动路由至 C1 (抖音/字节系) / C2 (小红书与快手系) / C3 (YouTube、X/Twitter、B站、微博与全网 yt-dlp 兜底)。
2. Stage 2 (无水印最高画质直链解析)：
   - C1 抖音/iesdouyin：采用双路 slidesinfo 专用接口绕过 SSR videoInfoRes 缺失风控，
     并利用 play_addr.uri 主动探测 aweme.snssdk.com 1080p 原画流（突破默认 540p 降质陷阱），
     同时支持抖音图文笔记无损非 WebP 原图、Live Photo 动图视频与背景音乐提取。
   - C2 小红书/快手：解析 __INITIAL_STATE__ 与 INIT_STATE，重构 ci.xiaohongshu.com 无水印原图节点与 mainMvUrls。
   - C3 YouTube / X(Twitter) / B站 / 微博 / 全网：
     * YouTube：调用 yt-dlp 配合 --js-runtimes node 破解 EJS 签名挑战，优选 1080p H.264(avc1)+AAC(m4a) 8 线程并发拉流混流。
     * X / Twitter：采用 Syndication π-Token 免登接口提取最高码率 MP4 视频流、?name=orig 无损原图，支持图文+视频混合推文与多视频推文全量下载。
     * B站 / 微博：调用 view+playurl 与 Component_Play_Playinfo 免登直解。
3. Stage 3 (流式下载、元数据归档与可选 Preview 转码)：流式落盘视频/图集/封面，输出标准化 metadata.json，
   并联动 video_download_evaluator.py 执行 Gate G1~G4 门禁校验与 100 分制评分。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
import yaml

IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

URL_EXTRACT_RE = re.compile(r"https?://[^\s\"'<>，。！？、；：（）【】《》]+", re.IGNORECASE)


@dataclasses.dataclass
class MediaImageItem:
    index: int
    url: str
    width: int = 0
    height: int = 0
    live_photo_url: str = ""
    local_path: str = ""
    live_local_path: str = ""


@dataclasses.dataclass
class MediaAuthor:
    uid: str = ""
    sec_uid: str = ""
    nickname: str = ""
    avatar_url: str = ""


@dataclasses.dataclass
class ParsedMediaResult:
    category: str
    platform: str
    item_id: str
    input_text: str
    source_url: str
    resolved_url: str
    title: str
    media_type: str  # "video" | "gallery" | "mixed"
    video_url: str = ""
    video_uri: str = ""
    quality_tag: str = ""
    expected_width: int = 0
    expected_height: int = 0
    expected_size: int = 0
    duration_sec: float = 0.0
    cover_url: str = ""
    music_url: str = ""
    images: List[MediaImageItem] = dataclasses.field(default_factory=list)
    extra_video_urls: List[str] = dataclasses.field(default_factory=list)
    author: MediaAuthor = dataclasses.field(default_factory=MediaAuthor)
    extra: Dict[str, Any] = dataclasses.field(default_factory=dict)


def extract_first_url(raw_text: str) -> str:
    """从包含中文分享文案的字符串中提取第一个干净的 HTTP(S) 链接。"""
    matches = URL_EXTRACT_RE.findall(raw_text or "")
    if not matches:
        raise ValueError(f"[G1 VETO] 未在输入文本中检出有效 HTTP/HTTPS 链接: {raw_text!r}")
    url = matches[0].rstrip(".,;:!?)'\"]>）】")
    return url


def classify_platform_and_category(url: str) -> Tuple[str, str]:
    """根据域名路由分类 (C1 / C2 / C3) 与平台代号。"""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if any(host == k or host.endswith("." + k) for k in ("douyin.com", "iesdouyin.com")):
        return "C1", "douyin"
    if any(host == k or host.endswith("." + k) for k in ("ixigua.com", "365yg.com")):
        return "C1", "xigua"
    if any(host == k or host.endswith("." + k) for k in ("xiaohongshu.com", "xhslink.com")):
        return "C2", "xiaohongshu"
    if any(host == k or host.endswith("." + k) for k in ("kuaishou.com", "chenzhongtech.com")):
        return "C2", "kuaishou"
    if any(host == k or host.endswith("." + k) for k in ("youtube.com", "youtu.be", "youtube-nocookie.com")):
        return "C3", "youtube"
    if any(host == k or host.endswith("." + k) for k in ("twitter.com", "x.com", "t.co")):
        return "C3", "twitter"
    if any(host == k or host.endswith("." + k) for k in ("bilibili.com", "b23.tv")):
        return "C3", "bilibili"
    if any(host == k or host.endswith("." + k) for k in ("weibo.com", "weibo.cn", "t.cn")):
        return "C3", "weibo"
    return "C3", "ytdlp_generic"


def sanitize_filename(name: str, max_len: int = 56) -> str:
    """清理文件名非法字符并限制长度。"""
    text = html.unescape(name or "")
    text = re.sub(r"https?://\S+", "", text)
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t•]+', "_", text.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned:
        cleaned = "untitled"
    return cleaned[:max_len].strip(" ._")


def select_non_webp_url(url_list: List[str]) -> str:
    """优先挑选非 .webp 格式的原图链接。"""
    for u in url_list or []:
        if u and not u.split("?")[0].lower().endswith(".webp"):
            return u
    return url_list[0] if url_list else ""


# ============================================================================
# C1: 抖音 / iesdouyin 专属解析器 (slidesinfo + snssdk 1080p 原画探针)
# ============================================================================

def _extract_douyin_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("modal_id", "aweme_id", "item_ids"):
        if key in qs and qs[key]:
            m = re.search(r"(\d{15,22})", qs[key][0])
            if m:
                return m.group(1)
    m_path = re.search(r"/(?:video|note|slides)/(\d{15,22})", parsed.path)
    if m_path:
        return m_path.group(1)
    m_any = re.search(r"(\d{18,20})", parsed.path)
    if m_any:
        return m_any.group(1)
    return ""


def _probe_douyin_best_stream(
    client: httpx.Client,
    video_uri: str,
    fallback_urls: List[str],
    prefer_quality: str = "1080p",
) -> Tuple[str, str, int]:
    ratios = ["1080p", "720p", "540p"]
    if prefer_quality in ratios:
        ratios = [prefer_quality] + [r for r in ratios if r != prefer_quality]

    headers = {"User-Agent": IOS_UA, "Referer": "https://www.iesdouyin.com/"}
    best_candidate: Tuple[str, str, int] = ("", "", 0)

    if video_uri:
        for ratio in ratios:
            probe_url = (
                f"https://aweme.snssdk.com/aweme/v1/play/"
                f"?video_id={video_uri}&ratio={ratio}&line=0"
            )
            try:
                resp = client.get(probe_url, headers=headers, follow_redirects=False, timeout=12.0)
                cdn_url = resp.headers.get("location", "")
                if not cdn_url and resp.status_code == 200:
                    cdn_url = str(resp.url)
                if cdn_url:
                    head_resp = client.head(cdn_url, headers=headers, follow_redirects=True, timeout=12.0)
                    size = int(head_resp.headers.get("content-length") or 0)
                    if head_resp.status_code in (200, 206) and size > 10000:
                        if size > best_candidate[2]:
                            best_candidate = (str(head_resp.url), ratio, size)
                        if ratio == prefer_quality:
                            return best_candidate
            except Exception:
                continue

    if best_candidate[0]:
        return best_candidate

    for raw_u in fallback_urls:
        clean_u = raw_u.replace("playwm", "play")
        try:
            head_resp = client.head(clean_u, headers=headers, follow_redirects=True, timeout=12.0)
            size = int(head_resp.headers.get("content-length") or 0)
            if head_resp.status_code in (200, 206):
                return (str(head_resp.url), "play_addr_default", size)
        except Exception:
            continue

    fallback = fallback_urls[0].replace("playwm", "play") if fallback_urls else ""
    return (fallback, "fallback_unverified", 0)


def parse_douyin(url: str, input_text: str, prefer_quality: str = "1080p") -> ParsedMediaResult:
    headers = {"User-Agent": IOS_UA, "Referer": "https://www.iesdouyin.com/"}
    with httpx.Client(timeout=20.0, follow_redirects=False, headers=headers) as client:
        resolved_url = url
        video_id = _extract_douyin_id_from_url(url)
        if not video_id:
            resp = client.get(url)
            resolved_url = resp.headers.get("location") or str(resp.url)
            video_id = _extract_douyin_id_from_url(resolved_url)
            if not video_id:
                resp2 = client.get(resolved_url, follow_redirects=True)
                resolved_url = str(resp2.url)
                video_id = _extract_douyin_id_from_url(resolved_url)

        if not video_id:
            raise ValueError(f"[G1 VETO] 无法从抖音链接解析出作品 ID (aweme_id): {url} -> {resolved_url}")

        api_candidates = [
            f"https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/?aweme_ids=%5B{video_id}%5D",
            f"https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/?aweme_ids=%5B{video_id}%5D&request_source=200",
        ]
        detail: Optional[Dict[str, Any]] = None
        for api_url in api_candidates:
            try:
                r = client.get(api_url)
                if r.status_code == 200:
                    payload = r.json()
                    details = payload.get("aweme_details")
                    if isinstance(details, list) and len(details) > 0:
                        detail = details[0]
                        break
            except Exception:
                continue

        if not detail:
            share_page_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
            r_html = client.get(share_page_url, follow_redirects=True)
            m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})</script>", r_html.text, re.S)
            if m:
                router_data = json.loads(m.group(1))
                loader = router_data.get("loaderData", {})
                for page_key in ("video_(id)/page", "note_(id)/page"):
                    info_res = (loader.get(page_key) or {}).get("videoInfoRes") or {}
                    item_list = info_res.get("item_list") or []
                    if item_list:
                        detail = item_list[0]
                        break

        if not detail:
            raise RuntimeError(f"[G1 VETO] 抖音作品 {video_id} 详情获取为空（可能已删除、私密或触发接口变更）")

        desc = detail.get("desc") or f"douyin_{video_id}"
        author_raw = detail.get("author") or {}
        avatar_list = (author_raw.get("avatar_thumb") or {}).get("url_list") or []
        author = MediaAuthor(
            uid=str(author_raw.get("unique_id") or author_raw.get("short_id") or ""),
            sec_uid=str(author_raw.get("sec_uid") or ""),
            nickname=str(author_raw.get("nickname") or ""),
            avatar_url=avatar_list[0] if avatar_list else "",
        )

        images_raw = detail.get("images") or []
        images: List[MediaImageItem] = []
        if isinstance(images_raw, list) and len(images_raw) > 0:
            for idx, img in enumerate(images_raw, start=1):
                u_list = img.get("url_list") or []
                img_url = select_non_webp_url(u_list)
                live_url = ""
                img_video = img.get("video") or {}
                live_list = (img_video.get("play_addr") or {}).get("url_list") or []
                if live_list:
                    live_url = live_list[0].replace("playwm", "play")
                if img_url:
                    images.append(
                        MediaImageItem(
                            index=idx,
                            url=img_url,
                            width=int(img.get("width") or 0),
                            height=int(img.get("height") or 0),
                            live_photo_url=live_url,
                        )
                    )

        vid_raw = detail.get("video") or {}
        cover_list = (vid_raw.get("origin_cover") or vid_raw.get("cover") or {}).get("url_list") or []
        cover_url = select_non_webp_url(cover_list)
        duration_ms = float(vid_raw.get("duration") or 0.0)
        duration_sec = round(duration_ms / 1000.0, 3) if duration_ms > 100 else duration_ms
        width = int(vid_raw.get("width") or 0)
        height = int(vid_raw.get("height") or 0)

        play_addr = vid_raw.get("play_addr") or {}
        video_uri = str(play_addr.get("uri") or "")
        url_list = play_addr.get("url_list") or []

        music_url = ""
        music_raw = detail.get("music") or {}
        music_play = (music_raw.get("play_url") or {}).get("url_list") or []
        if music_play:
            music_url = music_play[0]

        if images:
            return ParsedMediaResult(
                category="C1",
                platform="douyin",
                item_id=video_id,
                input_text=input_text,
                source_url=url,
                resolved_url=resolved_url,
                title=desc,
                media_type="gallery",
                video_url="",
                video_uri=video_uri,
                quality_tag="gallery_original",
                expected_width=width,
                expected_height=height,
                expected_size=0,
                duration_sec=duration_sec,
                cover_url=cover_url or images[0].url,
                music_url=music_url,
                images=images,
                author=author,
                extra={"aweme_type": detail.get("aweme_type"), "ratio": vid_raw.get("ratio")},
            )

        best_url, quality_tag, expected_size = _probe_douyin_best_stream(
            client, video_uri=video_uri, fallback_urls=url_list, prefer_quality=prefer_quality
        )
        if not best_url:
            raise RuntimeError(f"[G1 VETO] 未能解析出抖音作品 {video_id} 的有效无水印视频流")

        return ParsedMediaResult(
            category="C1",
            platform="douyin",
            item_id=video_id,
            input_text=input_text,
            source_url=url,
            resolved_url=resolved_url,
            title=desc,
            media_type="video",
            video_url=best_url,
            video_uri=video_uri,
            quality_tag=quality_tag,
            expected_width=width,
            expected_height=height,
            expected_size=expected_size,
            duration_sec=duration_sec,
            cover_url=cover_url,
            music_url=music_url,
            images=[],
            author=author,
            extra={
                "default_ratio": vid_raw.get("ratio", ""),
                "bit_rate_gears": [b.get("gear_name") for b in (vid_raw.get("bit_rate") or [])],
            },
        )


# ============================================================================
# C2: 小红书 / 快手 专属解析器
# ============================================================================

def parse_xiaohongshu(url: str, input_text: str) -> ParsedMediaResult:
    headers = {"User-Agent": DESKTOP_UA, "Referer": "https://www.xiaohongshu.com/"}
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        resolved_url = str(resp.url)
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>", resp.text, re.S)
        if not m:
            raise ValueError("[G1 VETO] 小红书页面未检出 window.__INITIAL_STATE__ 状态数据")
        state_raw = m.group(1).strip().replace("undefined", "null")
        state = yaml.safe_load(state_raw)
        note_id = str((state.get("note") or {}).get("currentNoteId") or "")
        if not note_id or note_id == "null":
            raise ValueError("[G1 VETO] 小红书分享链接已过期或触发登录拦截 (currentNoteId 为空)")
        note_map = (state.get("note") or {}).get("noteDetailMap") or {}
        note = (note_map.get(note_id) or {}).get("note") or {}
        if not note:
            raise ValueError(f"[G1 VETO] 小红书笔记 {note_id} 详情字典为空")

        title = note.get("title") or note.get("desc") or f"xhs_{note_id}"
        user = note.get("user") or {}
        author = MediaAuthor(
            uid=str(user.get("userId") or ""),
            nickname=str(user.get("nickname") or ""),
            avatar_url=str(user.get("avatar") or ""),
        )

        h264_streams = (((note.get("video") or {}).get("media") or {}).get("stream") or {}).get("h264") or []
        video_url = h264_streams[0].get("masterUrl", "") if h264_streams else ""
        width = int(h264_streams[0].get("width") or 0) if h264_streams else 0
        height = int(h264_streams[0].get("height") or 0) if h264_streams else 0

        img_list = note.get("imageList") or []
        cover_url = img_list[0].get("urlDefault", "") if img_list else ""
        images: List[MediaImageItem] = []
        if not video_url:
            for idx, item in enumerate(img_list, start=1):
                default_u = item.get("urlDefault") or ""
                if "notes_pre_post" in default_u:
                    image_token = default_u.split("/")[-1].split("!")[0]
                    spec = "spectrum/" if "spectrum" in default_u else ""
                    clean_img_u = f"https://ci.xiaohongshu.com/notes_pre_post/{spec}{image_token}?imageView2/format/jpg"
                else:
                    clean_img_u = default_u
                live_u = ""
                if item.get("livePhoto"):
                    lp_h264 = (item.get("stream") or {}).get("h264") or []
                    if lp_h264:
                        live_u = lp_h264[0].get("masterUrl", "")
                if clean_img_u:
                    images.append(
                        MediaImageItem(
                            index=idx,
                            url=clean_img_u,
                            width=int(item.get("width") or 0),
                            height=int(item.get("height") or 0),
                            live_photo_url=live_u,
                        )
                    )

        return ParsedMediaResult(
            category="C2",
            platform="xiaohongshu",
            item_id=note_id,
            input_text=input_text,
            source_url=url,
            resolved_url=resolved_url,
            title=title,
            media_type="video" if video_url else "gallery",
            video_url=video_url,
            quality_tag="xhs_master_h264" if video_url else "xhs_ci_original",
            expected_width=width,
            expected_height=height,
            cover_url=cover_url,
            images=images,
            author=author,
        )


def parse_kuaishou(url: str, input_text: str) -> ParsedMediaResult:
    headers = {"User-Agent": IOS_UA, "Referer": "https://v.kuaishou.com/"}
    with httpx.Client(timeout=20.0, follow_redirects=False, headers=headers) as client:
        r_share = client.get(url)
        loc = r_share.headers.get("location") or url
        loc = loc.replace("/fw/long-video/", "/fw/photo/")
        r_page = client.get(loc, cookies=r_share.cookies, follow_redirects=True)
        m = re.search(r"window\.INIT_STATE\s*=\s*(.*?)</script>", r_page.text, re.S)
        if not m:
            raise ValueError("[G1 VETO] 快手页面未检出 window.INIT_STATE 数据")
        init_state = json.loads(m.group(1).strip())
        photo_node = None
        for val in init_state.values():
            if isinstance(val, dict) and "result" in val and "photo" in val:
                photo_node = val
                break
        if not photo_node or photo_node.get("result") != 1:
            raise ValueError("[G1 VETO] 快手作品状态异常或触发风控拦截")
        photo = photo_node["photo"]
        photo_id = str(photo.get("photoId") or photo.get("id") or int(time.time()))
        title = photo.get("caption") or f"kuaishou_{photo_id}"
        author = MediaAuthor(
            uid=str(photo.get("userId") or ""),
            nickname=str(photo.get("userName") or ""),
            avatar_url=str(photo.get("headUrl") or ""),
        )
        mv_urls = photo.get("mainMvUrls") or []
        video_url = mv_urls[0].get("url", "") if mv_urls else ""
        covers = photo.get("coverUrls") or []
        cover_url = covers[0].get("url", "") if covers else ""

        atlas = (photo.get("ext_params") or {}).get("atlas") or {}
        cdns = atlas.get("cdn") or []
        paths = atlas.get("list") or []
        images: List[MediaImageItem] = []
        if cdns and paths:
            for idx, p in enumerate(paths, start=1):
                images.append(MediaImageItem(index=idx, url=f"https://{cdns[0]}/{p.lstrip('/')}"))

        return ParsedMediaResult(
            category="C2",
            platform="kuaishou",
            item_id=photo_id,
            input_text=input_text,
            source_url=url,
            resolved_url=str(r_page.url),
            title=title,
            media_type="video" if video_url else "gallery",
            video_url=video_url,
            quality_tag="kuaishou_mainMv",
            cover_url=cover_url,
            images=images,
            author=author,
        )


# ============================================================================
# C3: YouTube / X(Twitter) / B站 / 微博 / yt-dlp 全网通用解析器
# ============================================================================

def _find_js_runtime_args() -> List[str]:
    """检测本机安装的 Node/Deno/Bun 运行时，供 yt-dlp 破解 YouTube EJS 签名使用。"""
    for rt in ("node", "deno", "bun"):
        if shutil.which(rt):
            return ["--js-runtimes", rt]
    for candidate in (r"C:\Softs\Installs\nodejs\node.exe", r"C:\Program Files\nodejs\node.exe"):
        if os.path.isfile(candidate):
            return ["--js-runtimes", f"node:{candidate}"]
    return []


def _resolve_ffmpeg_bin(binary_name: str = "ffmpeg") -> str:
    """多机自动探测 ffmpeg / ffprobe 可执行文件路径（PATH 优先，兼容本地绿色软件目录）。"""
    found = shutil.which(binary_name)
    if found:
        return found
    exe_name = f"{binary_name}.exe" if os.name == "nt" and not binary_name.lower().endswith(".exe") else binary_name
    for cand_dir in (
        r"C:\Softs\Installs\ffmpeg\bin",
        r"C:\Softs\Installs\jasna-v0.6.0-alpha5\_internal\tools",
        r"C:\Softs\Greens\[softs]lada\_internal\bin",
        r"C:\Softs\Greens\[Softs]Jianyingv8\8.2.0.13017",
    ):
        cand = os.path.join(cand_dir, exe_name)
        if os.path.isfile(cand):
            return cand
    return binary_name


def _resolve_ytdlp_cmd() -> List[str]:
    """多机自动探测 yt-dlp 调用入口（PATH 优先，回退用户 Scripts 目录与当前 Python -m yt_dlp）。"""
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    if os.name == "nt":
        py_ver = f"Python{sys.version_info.major}{sys.version_info.minor}"
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        for cand in (
            os.path.join(appdata, "Python", py_ver, "Scripts", "yt-dlp.exe"),
            os.path.join(localappdata, "Programs", "Python", py_ver, "Scripts", "yt-dlp.exe"),
            os.path.join(os.path.dirname(sys.executable), "Scripts", "yt-dlp.exe"),
        ):
            if cand and os.path.isfile(cand):
                return [cand]
    return [sys.executable, "-m", "yt_dlp"]


def _find_ffmpeg_location_args() -> List[str]:
    """为 yt-dlp 显式注入 ffmpeg 路径，避免 ffmpeg 未加入系统 PATH 时无法混流 DASH 音视频轨。"""
    ff_bin = _resolve_ffmpeg_bin("ffmpeg")
    if os.path.isfile(ff_bin):
        return ["--ffmpeg-location", ff_bin]
    return []


def parse_youtube(url: str, input_text: str, prefer_quality: str = "1080p") -> ParsedMediaResult:
    """
    YouTube 专属解析器：挂载本机 Node 运行时消除 EJS 警告，
    根据画质偏好筛选最高码率 H.264(avc1)/VP9 视频轨 + m4a 音频轨。
    """
    height_map = {"4k": 2160, "2k": 1440, "1080p": 1080, "720p": 720, "540p": 540}
    max_h = height_map.get(prefer_quality.lower(), 1080)
    fmt_selector = (
        f"bestvideo[height<={max_h}][vcodec^=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={max_h}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={max_h}]+bestaudio/"
        f"best[height<={max_h}]/best"
    )
    cmd = [
        *_resolve_ytdlp_cmd(),
        *_find_js_runtime_args(),
        *_find_ffmpeg_location_args(),
        "--dump-json",
        "--no-playlist",
        "-f",
        fmt_selector,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"[G1 VETO] YouTube 解析失败: {proc.stderr.strip()[:500]}")

    info = json.loads(proc.stdout.strip().splitlines()[0])
    vid_id = str(info.get("id") or "")
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    duration = float(info.get("duration") or 0.0)
    req_fmts = info.get("requested_formats") or []
    approx_size = 0
    if req_fmts:
        for rf in req_fmts:
            approx_size += int(rf.get("filesize") or rf.get("filesize_approx") or 0)
    else:
        approx_size = int(info.get("filesize") or info.get("filesize_approx") or 0)

    return ParsedMediaResult(
        category="C3",
        platform="youtube",
        item_id=vid_id,
        input_text=input_text,
        source_url=url,
        resolved_url=str(info.get("webpage_url") or url),
        title=str(info.get("title") or f"youtube_{vid_id}"),
        media_type="video",
        video_url=str(info.get("webpage_url") or url),
        quality_tag=f"youtube_{height}p_{info.get('format_id', 'merged')}",
        expected_width=width,
        expected_height=height,
        expected_size=approx_size,
        duration_sec=duration,
        cover_url=str(info.get("thumbnail") or ""),
        author=MediaAuthor(
            uid=str(info.get("channel_id") or info.get("uploader_id") or ""),
            nickname=str(info.get("uploader") or info.get("channel") or ""),
        ),
        extra={"format_selector": fmt_selector, "vcodec": info.get("vcodec"), "acodec": info.get("acodec")},
    )


def _to_twitter_orig_photo_url(raw_url: str) -> str:
    """将 pbs.twimg.com 图片地址转换为 ?format=jpg&name=orig 无损原图地址。"""
    if not raw_url:
        return ""
    base = raw_url.split("?")[0]
    m_ext = re.search(r"\.([a-zA-Z0-9]+)$", base)
    ext = m_ext.group(1).lower() if m_ext else "jpg"
    if "pbs.twimg.com/media/" in base:
        return f"{base}?format={ext}&name=orig"
    return raw_url


def _utf16_offset_map(text: str) -> List[int]:
    """构建 UTF-16 code unit 偏移到 Python 字符串索引的映射表，兼容 Emoji 代理对。"""
    mapping: List[int] = []
    for py_idx, ch in enumerate(text):
        units = 2 if ord(ch) > 0xFFFF else 1
        for _ in range(units):
            mapping.append(py_idx)
    mapping.append(len(text))
    return mapping


def _u16_to_py_idx(u16_map: List[int], u16_offset: int, text_len: int) -> int:
    if u16_offset <= 0:
        return 0
    if u16_offset >= len(u16_map):
        return text_len
    return u16_map[u16_offset]


def _format_draftjs_inline_text(
    text: str,
    inline_styles: List[Dict[str, Any]],
    entity_ranges: List[Dict[str, Any]],
    block_data: Dict[str, Any],
    emap: Dict[str, Dict[str, Any]],
    is_heading: bool = False,
) -> str:
    """将 X Article (Draft.js) 单段落内的 Bold/Italic/Code 与 LINK/URL 转换为干净的 CommonMark 文本。"""
    if not text:
        return ""
    u16_map = _utf16_offset_map(text)
    n = len(text)
    spans: List[Tuple[int, int, str, str]] = []

    for er in entity_ranges or []:
        ev = emap.get(str(er.get("key"))) or {}
        if ev.get("type") == "LINK":
            link_url = str((ev.get("data") or {}).get("url") or "").strip()
            if link_url:
                s = _u16_to_py_idx(u16_map, int(er.get("offset") or 0), n)
                e = _u16_to_py_idx(u16_map, int(er.get("offset") or 0) + int(er.get("length") or 0), n)
                if 0 <= s < e <= n:
                    spans.append((s, e, "LINK", link_url))

    for u_item in (block_data or {}).get("urls") or []:
        u_text = str(u_item.get("text") or "").strip()
        s = _u16_to_py_idx(u16_map, int(u_item.get("fromIndex") or 0), n)
        e = _u16_to_py_idx(u16_map, int(u_item.get("toIndex") or 0), n)
        if u_text and 0 <= s < e <= n:
            if not any(not (e <= es or s >= ee) for es, ee, _, _ in spans):
                spans.append((s, e, "URL", u_text))

    for sr in inline_styles or []:
        style_name = str(sr.get("style") or "")
        if is_heading and style_name == "Bold":
            continue
        if style_name not in ("Bold", "Italic", "Code"):
            continue
        s = _u16_to_py_idx(u16_map, int(sr.get("offset") or 0), n)
        e = _u16_to_py_idx(u16_map, int(sr.get("offset") or 0) + int(sr.get("length") or 0), n)
        if 0 <= s < e <= n:
            if not any(not (e <= es or s >= ee) for es, ee, _, _ in spans):
                spans.append((s, e, style_name, ""))

    if not spans:
        return text

    spans.sort(key=lambda item: (item[0], item[1]))
    parts: List[str] = []
    cursor = 0
    for s, e, kind, payload in spans:
        if s < cursor:
            continue
        if s > cursor:
            parts.append(text[cursor:s])
        seg = text[s:e]
        if kind == "LINK":
            parts.append(f"[{seg}]({payload})")
        elif kind == "URL":
            parts.append(f"<{payload}>" if seg == payload else f"[{seg}]({payload})")
        elif kind == "Bold":
            stripped = seg.rstrip("。，、；：！？")
            tail = seg[len(stripped):]
            if stripped:
                parts.append(f"**{stripped}**{tail}" if e < n and tail else f"**{seg}**")
            else:
                parts.append(seg)
        elif kind == "Italic":
            parts.append(f"*{seg}*")
        elif kind == "Code":
            parts.append(f"`{seg}`")
        cursor = e
    if cursor < n:
        parts.append(text[cursor:])
    return "".join(parts)


def _normalize_embedded_markdown_fence(md_text: str) -> str:
    """确保内嵌代码块具备语言标记（默认补 txt），满足 Markdown 结构规范。"""
    lines = (md_text or "").strip().splitlines()
    if not lines:
        return ""
    out: List[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                lang = stripped[3:].strip()
                out.append(f"```{lang or 'txt'}")
                in_fence = True
            else:
                out.append("```")
                in_fence = False
        else:
            out.append(line)
    return "\n".join(out)


def _parse_twitter_article(
    client: httpx.Client,
    tweet_id: str,
    url: str,
    resolved: str,
    input_text: str,
    screen_name: str,
    author: MediaAuthor,
    syn_data: Dict[str, Any],
) -> ParsedMediaResult:
    """解析 X 长文章（X Article / Draft.js blocks + media_entities + 内嵌推文视频）。"""
    fx_url = f"https://api.fxtwitter.com/{screen_name or 'i'}/status/{tweet_id}"
    r_fx = client.get(fx_url, timeout=20.0)
    r_fx.raise_for_status()
    fx_tweet = (r_fx.json() or {}).get("tweet") or {}
    art = fx_tweet.get("article") or syn_data.get("article") or {}
    if not art:
        raise ValueError(f"[G1 VETO] X 文章 {tweet_id} 元数据为空")

    art_id = str(art.get("id") or art.get("rest_id") or tweet_id)
    art_title = html.unescape(str(art.get("title") or f"x_article_{tweet_id}")).strip()
    created_at = str(art.get("created_at") or syn_data.get("created_at") or "")
    cover_info = ((art.get("cover_media") or {}).get("media_info")) or {}
    cover_raw_url = str(cover_info.get("original_img_url") or "")
    cover_url = _to_twitter_orig_photo_url(cover_raw_url) if cover_raw_url else ""

    media_map: Dict[str, Dict[str, Any]] = {}
    for me in art.get("media_entities") or []:
        mid = str(me.get("media_id") or "")
        if mid:
            media_map[mid] = me.get("media_info") or {}

    content_obj = art.get("content") or {}
    blocks = content_obj.get("blocks") or []
    emap: Dict[str, Dict[str, Any]] = {}
    for entry in content_obj.get("entityMap") or []:
        emap[str(entry.get("key"))] = entry.get("value") or {}

    images: List[MediaImageItem] = []
    video_items: List[Dict[str, Any]] = []
    img_slot_by_mid: Dict[str, int] = {}
    vid_slot_by_key: Dict[str, int] = {}
    embedded_tweets: Dict[str, Dict[str, Any]] = {}

    # 预先按阅读顺序收集全部文章内嵌图片、视频与引用推文媒体
    for blk in blocks:
        if blk.get("type") != "atomic":
            continue
        for er in blk.get("entityRanges") or []:
            ev = emap.get(str(er.get("key"))) or {}
            etype = ev.get("type")
            edata = ev.get("data") or {}
            if etype == "MEDIA":
                caption = str(edata.get("caption") or "").strip()
                for m_item in edata.get("mediaItems") or []:
                    mid = str(m_item.get("mediaId") or "")
                    minfo = media_map.get(mid) or {}
                    mtypename = str(minfo.get("__typename") or "")
                    if mtypename == "ApiImage":
                        raw_u = str(minfo.get("original_img_url") or "")
                        if raw_u and mid not in img_slot_by_mid:
                            idx = len(images) + 1
                            images.append(
                                MediaImageItem(
                                    index=idx,
                                    url=_to_twitter_orig_photo_url(raw_u),
                                    width=int(minfo.get("original_img_width") or 0),
                                    height=int(minfo.get("original_img_height") or 0),
                                )
                            )
                            img_slot_by_mid[mid] = idx
                    elif mtypename in ("ApiVideo", "ApiGif"):
                        variants = minfo.get("variants") or []
                        mp4s = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
                        if mp4s and f"media:{mid}" not in vid_slot_by_key:
                            mp4s.sort(key=lambda x: int(x.get("bit_rate") or x.get("bitrate") or 0), reverse=True)
                            prev_img = minfo.get("preview_image") or {}
                            v_idx = len(video_items) + 1
                            video_items.append(
                                {
                                    "index": v_idx,
                                    "url": str(mp4s[0]["url"]),
                                    "bitrate": int(mp4s[0].get("bit_rate") or mp4s[0].get("bitrate") or 0),
                                    "width": int(prev_img.get("original_img_width") or 0),
                                    "height": int(prev_img.get("original_img_height") or 0),
                                    "duration_sec": round(float(minfo.get("duration_millis") or 0.0) / 1000.0, 3),
                                    "caption": caption,
                                    "source_kind": "article_media",
                                }
                            )
                            vid_slot_by_key[f"media:{mid}"] = v_idx
            elif etype == "TWEET":
                emb_tid = str(edata.get("tweetId") or "").strip()
                if emb_tid and emb_tid not in embedded_tweets:
                    try:
                        emb_parsed = parse_twitter(
                            f"https://x.com/i/status/{emb_tid}",
                            f"https://x.com/i/status/{emb_tid}",
                        )
                        emb_vid_indices: List[int] = []
                        all_emb_vids = ([emb_parsed.video_url] if emb_parsed.video_url else []) + list(emb_parsed.extra_video_urls)
                        for sub_i, v_u in enumerate(all_emb_vids, start=1):
                            v_idx = len(video_items) + 1
                            video_items.append(
                                {
                                    "index": v_idx,
                                    "url": v_u,
                                    "bitrate": 0,
                                    "width": emb_parsed.expected_width if sub_i == 1 else 0,
                                    "height": emb_parsed.expected_height if sub_i == 1 else 0,
                                    "duration_sec": emb_parsed.duration_sec if sub_i == 1 else 0.0,
                                    "caption": f"引用推文 @{emb_parsed.author.sec_uid or emb_parsed.author.nickname} ({emb_tid})",
                                    "source_kind": "embedded_tweet",
                                }
                            )
                            emb_vid_indices.append(v_idx)
                        emb_img_indices: List[int] = []
                        for emb_img in emb_parsed.images:
                            idx = len(images) + 1
                            images.append(
                                MediaImageItem(
                                    index=idx,
                                    url=emb_img.url,
                                    width=emb_img.width,
                                    height=emb_img.height,
                                )
                            )
                            emb_img_indices.append(idx)
                        embedded_tweets[emb_tid] = {
                            "tweet_id": emb_tid,
                            "url": f"https://x.com/{emb_parsed.author.sec_uid or 'i'}/status/{emb_tid}",
                            "author_name": emb_parsed.author.nickname,
                            "screen_name": emb_parsed.author.sec_uid,
                            "text": emb_parsed.title,
                            "video_indices": emb_vid_indices,
                            "image_indices": emb_img_indices,
                        }
                    except Exception as exc:
                        embedded_tweets[emb_tid] = {
                            "tweet_id": emb_tid,
                            "url": f"https://x.com/i/status/{emb_tid}",
                            "error": str(exc),
                            "video_indices": [],
                            "image_indices": [],
                        }

    primary_video = video_items[0] if video_items else None
    primary_video_url = str(primary_video["url"]) if primary_video else ""
    expected_size = 0
    if primary_video_url:
        try:
            h_resp = client.head(primary_video_url, follow_redirects=True, timeout=10.0)
            expected_size = int(h_resp.headers.get("content-length") or 0)
        except Exception:
            expected_size = 0

    return ParsedMediaResult(
        category="C3",
        platform="twitter",
        item_id=tweet_id,
        input_text=input_text,
        source_url=url,
        resolved_url=resolved,
        title=art_title,
        media_type="article",
        video_url=primary_video_url,
        quality_tag="twitter_article_md",
        expected_width=int(primary_video["width"]) if primary_video else (images[0].width if images else 0),
        expected_height=int(primary_video["height"]) if primary_video else (images[0].height if images else 0),
        expected_size=expected_size,
        duration_sec=float(primary_video["duration_sec"]) if primary_video else 0.0,
        cover_url=cover_url or (images[0].url if images else ""),
        images=images,
        extra_video_urls=[str(v["url"]) for v in video_items[1:]],
        author=author,
        extra={
            "screen_name": screen_name,
            "favorite_count": syn_data.get("favorite_count"),
            "article_id": art_id,
            "article_url": f"https://x.com/i/article/{art_id}",
            "created_at": created_at,
            "article_blocks": blocks,
            "article_entity_map": emap,
            "img_slot_by_mid": img_slot_by_mid,
            "vid_slot_by_key": vid_slot_by_key,
            "video_items": video_items,
            "embedded_tweets": embedded_tweets,
        },
    )


def render_twitter_article_markdown(
    parsed: ParsedMediaResult,
    has_cover: bool,
    img_rel_by_idx: Dict[int, str],
    vid_rel_by_idx: Dict[int, Dict[str, str]],
) -> str:
    """将解析后的 X Article 渲染为结构清晰、符合本地预览规范的离线 Markdown 文档。"""
    ext = parsed.extra
    screen_name = str(ext.get("screen_name") or parsed.author.sec_uid or "")
    art_url = str(ext.get("article_url") or parsed.resolved_url)
    created_at = str(ext.get("created_at") or "")
    blocks: List[Dict[str, Any]] = ext.get("article_blocks") or []
    emap: Dict[str, Dict[str, Any]] = ext.get("article_entity_map") or {}
    img_slot_by_mid: Dict[str, int] = ext.get("img_slot_by_mid") or {}
    vid_slot_by_key: Dict[str, int] = ext.get("vid_slot_by_key") or {}
    embedded_tweets: Dict[str, Dict[str, Any]] = ext.get("embedded_tweets") or {}

    lines: List[str] = [f"# {parsed.title}", ""]
    if has_cover:
        lines.extend(["![文章封面](./cover.jpg)", ""])

    author_label = f"{parsed.author.nickname} (@{screen_name})" if screen_name else parsed.author.nickname
    lines.extend(
        [
            "| 项目 | 内容 |",
            "| :--- | :--- |",
            f"| 作者 | {author_label} |",
            f"| 推文链接 | <{parsed.source_url}> |",
            f"| 文章直链 | <{art_url}> |",
            f"| 发布时间 | `{created_at}` |",
            "",
        ]
    )

    ordered_counter = 0
    h2_counter = 0

    for blk in blocks:
        btype = str(blk.get("type") or "unstyled")
        raw_text = str(blk.get("text") or "")
        inline_styles = blk.get("inlineStyleRanges") or []
        entity_ranges = blk.get("entityRanges") or []
        bdata = blk.get("data") or {}

        if btype != "ordered-list-item":
            ordered_counter = 0

        if btype == "atomic":
            for er in entity_ranges:
                ev = emap.get(str(er.get("key"))) or {}
                etype = ev.get("type")
                edata = ev.get("data") or {}
                if etype == "MEDIA":
                    caption = str(edata.get("caption") or "").strip()
                    for m_item in edata.get("mediaItems") or []:
                        mid = str(m_item.get("mediaId") or "")
                        if mid in img_slot_by_mid:
                            idx = img_slot_by_mid[mid]
                            rel_img = img_rel_by_idx.get(idx, f"./assets/img_{idx:02d}.jpg")
                            alt_txt = caption or f"文章配图 {idx:02d}"
                            lines.append(f"![{alt_txt}]({rel_img})")
                            lines.append("")
                            if caption:
                                lines.append(f"*图：{caption}*")
                                lines.append("")
                        elif f"media:{mid}" in vid_slot_by_key:
                            v_idx = vid_slot_by_key[f"media:{mid}"]
                            v_paths = vid_rel_by_idx.get(v_idx) or {}
                            play_src = v_paths.get("preview") or v_paths.get("raw") or f"./assets/vid_{v_idx:02d}.mp4"
                            raw_src = v_paths.get("raw") or play_src
                            lines.append(f'<video src="{play_src}" controls preload="metadata" width="100%"></video>')
                            lines.append("")
                            cap_prefix = f"视频：{caption}" if caption else f"视频 {v_idx:02d}"
                            lines.append(f"*{cap_prefix}（[打开原画 MP4]({raw_src})）*")
                            lines.append("")
                elif etype == "MARKDOWN":
                    fenced = _normalize_embedded_markdown_fence(str(edata.get("markdown") or ""))
                    if fenced:
                        lines.append(fenced)
                        lines.append("")
                elif etype == "TWEET":
                    emb_tid = str(edata.get("tweetId") or "").strip()
                    emb = embedded_tweets.get(emb_tid) or {}
                    emb_url = str(emb.get("url") or f"https://x.com/i/status/{emb_tid}")
                    emb_text = str(emb.get("text") or "").strip()
                    emb_text_clean = re.sub(r"https?://t\.co/\S+", "", emb_text).strip()
                    emb_author = str(emb.get("author_name") or "")
                    emb_sn = str(emb.get("screen_name") or "")
                    header_cite = f"**{emb_author} (@{emb_sn})**" if emb_sn else f"**引用推文 {emb_tid}**"
                    lines.append(f"> {header_cite}（[原推链接]({emb_url})）")
                    if emb_text_clean:
                        for qline in emb_text_clean.splitlines():
                            lines.append(f"> {qline}" if qline.strip() else ">")
                    lines.append("")
                    for v_idx in emb.get("video_indices") or []:
                        v_paths = vid_rel_by_idx.get(v_idx) or {}
                        play_src = v_paths.get("preview") or v_paths.get("raw") or f"./assets/vid_{v_idx:02d}.mp4"
                        raw_src = v_paths.get("raw") or play_src
                        lines.append(f'<video src="{play_src}" controls preload="metadata" width="100%"></video>')
                        lines.append("")
                        lines.append(f"*引用推文成片视频（[打开原画 MP4]({raw_src})）*")
                        lines.append("")
                    for idx in emb.get("image_indices") or []:
                        rel_img = img_rel_by_idx.get(idx, f"./assets/img_{idx:02d}.jpg")
                        lines.append(f"![引用推文配图 {idx:02d}]({rel_img})")
                        lines.append("")
                elif etype == "DIVIDER":
                    lines.extend(["---", ""])
            continue

        if not raw_text.strip():
            continue

        formatted = _format_draftjs_inline_text(
            raw_text,
            inline_styles,
            entity_ranges,
            bdata,
            emap,
            is_heading=btype.startswith("header-"),
        )

        if btype in ("header-one", "header-two"):
            h2_counter += 1
            clean_h = re.sub(r"^\d+\s*[、.．]\s*", "", formatted.strip())
            lines.extend([f"## {h2_counter}. {clean_h}", ""])
        elif btype == "header-three":
            clean_h3 = re.sub(r"^\d+(?:\.\d+)?\s*[、.．]\s*", "", formatted.strip())
            prefix = f"{h2_counter}.1 " if h2_counter > 0 else ""
            lines.extend([f"### {prefix}{clean_h3}", ""])
        elif btype == "blockquote":
            for qline in formatted.splitlines():
                lines.append(f"> {qline}" if qline.strip() else ">")
            lines.append("")
        elif btype == "unordered-list-item":
            lines.append(f"- {formatted}")
            lines.append("")
        elif btype == "ordered-list-item":
            ordered_counter += 1
            lines.append(f"{ordered_counter}. {formatted}")
            lines.append("")
        elif btype == "code-block":
            lines.extend(["```txt", raw_text, "```", ""])
        else:
            lines.extend([formatted, ""])

    # 合并多余连续空行，确保以单个 LF 结尾
    compact: List[str] = []
    for line in lines:
        if line == "" and compact and compact[-1] == "":
            continue
        compact.append(line)
    return "\n".join(compact).rstrip() + "\n"


def parse_twitter(url: str, input_text: str) -> ParsedMediaResult:
    """
    X / Twitter Syndication π-Token 免登解析器：
    1. 支持纯视频推文、纯多图推文（自动升级 ?name=orig 无损原图）、图文+视频混合推文（Mixed Media）以及 X 长文章（Article -> Markdown）。
    2. 对视频流自动筛选 content_type == 'video/mp4' 且 bitrate 最高的 H.264 直链，并探测 Content-Length。
    """
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept": "application/json",
        "Referer": "https://platform.twitter.com/",
    }
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resolved = url
        if "t.co/" in urlparse(url).netloc:
            r_tco = client.get(url)
            resolved = str(r_tco.url)
        m_id = re.search(r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)", resolved)
        if not m_id:
            raise ValueError(f"[G1 VETO] 无法从 X/Twitter 链接提取推文 ID: {resolved}")
        tweet_id = m_id.group(1)
        token = str((float(tweet_id) / 1e15) * math.pi).replace("0", "").replace(".", "")
        syn_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token={token}"
        r = client.get(syn_url)
        r.raise_for_status()
        data = r.json()

        user = data.get("user") or {}
        screen_name = str(user.get("screen_name") or "")
        display_name = str(user.get("name") or screen_name)
        author = MediaAuthor(
            uid=str(user.get("id_str") or screen_name),
            sec_uid=screen_name,
            nickname=display_name,
            avatar_url=str(user.get("profile_image_url_https") or "").replace("_normal.", "_400x400."),
        )

        # 若该推文挂载了 X 长文章（Article），走完整图文音视频 + Markdown 文章解析分支
        if data.get("article"):
            return _parse_twitter_article(
                client=client,
                tweet_id=tweet_id,
                url=url,
                resolved=resolved,
                input_text=input_text,
                screen_name=screen_name,
                author=author,
                syn_data=data,
            )

        title = html.unescape(data.get("text") or f"tweet_{tweet_id}")
        full_text = title
        if data.get("note_tweet"):
            try:
                fx_url = f"https://api.fxtwitter.com/{screen_name or 'i'}/status/{tweet_id}"
                r_fx = client.get(fx_url, timeout=10.0)
                if r_fx.status_code == 200:
                    fx_tweet = (r_fx.json() or {}).get("tweet") or {}
                    if fx_tweet.get("text"):
                        full_text = html.unescape(str(fx_tweet["text"]))
                        title = full_text
            except Exception:
                pass

        video_urls: List[str] = []
        best_bitrate = 0
        video_w = 0
        video_h = 0
        duration_sec = 0.0
        cover_url = ""
        images: List[MediaImageItem] = []

        for media in data.get("mediaDetails") or []:
            mtype = media.get("type")
            orig_info = media.get("original_info") or {}
            mw = int(orig_info.get("width") or 0)
            mh = int(orig_info.get("height") or 0)

            if mtype in ("video", "animated_gif"):
                if not cover_url:
                    cover_url = _to_twitter_orig_photo_url(media.get("media_url_https") or "")
                vinfo = media.get("video_info") or {}
                dur_ms = float(vinfo.get("duration_millis") or 0.0)
                if dur_ms > 0 and duration_sec == 0.0:
                    duration_sec = round(dur_ms / 1000.0, 3)
                variants = vinfo.get("variants") or []
                mp4s = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
                if mp4s:
                    mp4s.sort(key=lambda x: int(x.get("bitrate") or 0), reverse=True)
                    chosen_u = mp4s[0]["url"]
                    video_urls.append(chosen_u)
                    if len(video_urls) == 1:
                        best_bitrate = int(mp4s[0].get("bitrate") or 0)
                        video_w, video_h = mw, mh
            elif mtype == "photo":
                raw_img_u = media.get("media_url_https") or ""
                if raw_img_u:
                    orig_u = _to_twitter_orig_photo_url(raw_img_u)
                    images.append(
                        MediaImageItem(
                            index=len(images) + 1,
                            url=orig_u,
                            width=mw,
                            height=mh,
                        )
                    )

        if not video_urls and not images:
            card_bv = ((data.get("card") or {}).get("binding_values") or {})
            for card_key in ("photo_image_full_size_original", "summary_photo_image_original", "thumbnail_image_original", "photo_image_full_size_large"):
                img_val = (card_bv.get(card_key) or {}).get("image_value") or {}
                card_img_u = str(img_val.get("url") or "")
                if card_img_u:
                    images.append(
                        MediaImageItem(
                            index=1,
                            url=card_img_u,
                            width=int(img_val.get("width") or 0),
                            height=int(img_val.get("height") or 0),
                        )
                    )
                    break

        if not video_urls and not images:
            raise ValueError(f"[G1 VETO] X 推文 {tweet_id} 中未找到公开视频或图片媒体")

        primary_video_url = video_urls[0] if video_urls else ""
        expected_size = 0
        if primary_video_url:
            try:
                h_resp = client.head(primary_video_url, follow_redirects=True, timeout=10.0)
                expected_size = int(h_resp.headers.get("content-length") or 0)
            except Exception:
                expected_size = 0

        if primary_video_url and images:
            media_type = "mixed"
        elif primary_video_url:
            media_type = "video"
        else:
            media_type = "gallery"
            if images and (video_w == 0):
                video_w, video_h = images[0].width, images[0].height

        return ParsedMediaResult(
            category="C3",
            platform="twitter",
            item_id=tweet_id,
            input_text=input_text,
            source_url=url,
            resolved_url=resolved,
            title=title,
            media_type=media_type,
            video_url=primary_video_url,
            quality_tag=f"twitter_mp4_{best_bitrate // 1000}kbps" if primary_video_url else "twitter_photo_orig",
            expected_width=video_w,
            expected_height=video_h,
            expected_size=expected_size,
            duration_sec=duration_sec,
            cover_url=cover_url or (images[0].url if images else ""),
            images=images,
            extra_video_urls=video_urls[1:],
            author=author,
            extra={"screen_name": screen_name, "favorite_count": data.get("favorite_count")},
        )


def parse_bilibili(url: str, input_text: str, prefer_quality: str = "1080p") -> ParsedMediaResult:
    """
    B 站免登 1080p DASH 解析器：
    优先调用 fnval=16&platform=pc&high_quality=1&try_look=1 获取免登 1080P (qn=80, avc1) DASH 视频轨
    与 192kbps (30280, mp4a) 音频轨及多 CDN 镜像备份流；若无 DASH 流则回退 html5 durl 直连流。
    """
    headers = {"User-Agent": DESKTOP_UA, "Referer": "https://www.bilibili.com/"}
    qn_map = {"4k": 120, "2k": 116, "1080p": 80, "720p": 64, "540p": 32}
    target_qn = qn_map.get(prefer_quality.lower(), 80)

    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resolved = url
        if "b23.tv" in urlparse(url).netloc:
            r_short = client.get(url)
            resolved = str(r_short.url)
        m_bv = re.search(r"(BV[0-9A-Za-z]{10})", resolved)
        if not m_bv:
            raise ValueError(f"[G1 VETO] 未能从 B 站链接提取出 BVID: {resolved}")
        bvid = m_bv.group(1)
        r_view = client.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}").json()
        if r_view.get("code") != 0:
            raise ValueError(f"[G1 VETO] B站 view 接口报错: {r_view.get('message')}")
        data = r_view["data"]
        cid = data["pages"][0]["cid"]
        dim = data["pages"][0].get("dimension") or {}
        owner = data.get("owner") or {}
        author = MediaAuthor(
            uid=str(owner.get("mid") or ""),
            nickname=str(owner.get("name") or ""),
            avatar_url=str(owner.get("face") or ""),
        )

        # 1. 优先尝试 DASH + try_look=1 免登 1080p
        r_dash = client.get(
            f"https://api.bilibili.com/x/player/playurl?otype=json&fnver=0&fnval=16&qn={target_qn}"
            f"&bvid={bvid}&cid={cid}&platform=pc&high_quality=1&try_look=1"
        ).json()
        dash = ((r_dash.get("data") or {}).get("dash")) or {}
        dash_videos = dash.get("video") or []
        dash_audios = dash.get("audio") or []

        if dash_videos:
            avc_videos = [v for v in dash_videos if str(v.get("codecs") or "").startswith("avc1")] or dash_videos
            le_videos = [v for v in avc_videos if int(v.get("id") or 0) <= target_qn]
            candidates = le_videos if le_videos else avc_videos
            candidates.sort(key=lambda v: (int(v.get("id") or 0), int(v.get("bandwidth") or 0)), reverse=True)
            chosen_v = candidates[0]

            v_url = str(chosen_v.get("baseUrl") or chosen_v.get("base_url") or "")
            v_backups = list(chosen_v.get("backupUrl") or chosen_v.get("backup_url") or [])
            v_width = int(chosen_v.get("width") or dim.get("width") or 0)
            v_height = int(chosen_v.get("height") or dim.get("height") or 0)
            v_qn = int(chosen_v.get("id") or target_qn)

            a_url = ""
            a_backups: List[str] = []
            if dash_audios:
                dash_audios.sort(key=lambda a: int(a.get("bandwidth") or 0), reverse=True)
                chosen_a = dash_audios[0]
                a_url = str(chosen_a.get("baseUrl") or chosen_a.get("base_url") or "")
                a_backups = list(chosen_a.get("backupUrl") or chosen_a.get("backup_url") or [])

            v_size = 0
            a_size = 0
            try:
                hv = client.head(v_url, follow_redirects=True, timeout=10.0)
                v_size = int(hv.headers.get("content-length") or 0)
            except Exception:
                pass
            if a_url:
                try:
                    ha = client.head(a_url, follow_redirects=True, timeout=10.0)
                    a_size = int(ha.headers.get("content-length") or 0)
                except Exception:
                    pass

            return ParsedMediaResult(
                category="C3",
                platform="bilibili",
                item_id=bvid,
                input_text=input_text,
                source_url=url,
                resolved_url=resolved,
                title=data.get("title") or bvid,
                media_type="video",
                video_url=v_url,
                quality_tag=f"bili_dash_qn{v_qn}_{v_height}p",
                expected_width=v_width,
                expected_height=v_height,
                expected_size=v_size + a_size,
                duration_sec=float(data.get("duration") or 0.0),
                cover_url=data.get("pic") or "",
                author=author,
                extra={
                    "dash_mode": True,
                    "dash_video_url": v_url,
                    "dash_video_backups": v_backups,
                    "dash_video_size": v_size,
                    "dash_audio_url": a_url,
                    "dash_audio_backups": a_backups,
                    "dash_audio_size": a_size,
                },
            )

        # 2. 回退 html5 durl 直连 MP4 流
        r_play = client.get(
            f"https://api.bilibili.com/x/player/playurl?otype=json&fnver=0&fnval=0&qn={target_qn}&bvid={bvid}&cid={cid}&platform=html5"
        ).json()
        durl = (r_play.get("data") or {}).get("durl") or []
        if not durl:
            raise ValueError("[G1 VETO] B站 playurl 未返回有效视频流")
        video_url = durl[0].get("url", "")
        size = int(durl[0].get("size") or 0)
        return ParsedMediaResult(
            category="C3",
            platform="bilibili",
            item_id=bvid,
            input_text=input_text,
            source_url=url,
            resolved_url=resolved,
            title=data.get("title") or bvid,
            media_type="video",
            video_url=video_url,
            quality_tag="bili_durl_mp4",
            expected_width=int(dim.get("width") or 0),
            expected_height=int(dim.get("height") or 0),
            expected_size=size,
            duration_sec=float(data.get("duration") or 0.0),
            cover_url=data.get("pic") or "",
            author=author,
        )


def parse_via_ytdlp(url: str, input_text: str, platform: str) -> ParsedMediaResult:
    """调用本机 yt-dlp --dump-json 作为 C3 全网通用解析兜底。"""
    cmd = [*_resolve_ytdlp_cmd(), *_find_js_runtime_args(), *_find_ffmpeg_location_args(), "--dump-json", "--no-playlist", url]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"[G1 VETO] yt-dlp 解析失败: {proc.stderr.strip()[:400]}")
    info = json.loads(proc.stdout.strip().splitlines()[0])
    return ParsedMediaResult(
        category="C3",
        platform=platform,
        item_id=str(info.get("id") or int(time.time())),
        input_text=input_text,
        source_url=url,
        resolved_url=str(info.get("webpage_url") or url),
        title=str(info.get("title") or info.get("description") or "ytdlp_video"),
        media_type="video",
        video_url=str(info.get("url") or info.get("webpage_url") or url),
        quality_tag=str(info.get("format_id") or "ytdlp_best"),
        expected_width=int(info.get("width") or 0),
        expected_height=int(info.get("height") or 0),
        expected_size=int(info.get("filesize") or info.get("filesize_approx") or 0),
        duration_sec=float(info.get("duration") or 0.0),
        cover_url=str(info.get("thumbnail") or ""),
        author=MediaAuthor(
            uid=str(info.get("uploader_id") or ""),
            nickname=str(info.get("uploader") or ""),
        ),
    )


def parse_media_input(raw_input: str, prefer_quality: str = "1080p") -> ParsedMediaResult:
    """统一入口：提取链接 -> 平台分类 -> 调用专属解析器。"""
    url = extract_first_url(raw_input)
    cat, platform = classify_platform_and_category(url)
    if platform in ("douyin", "xigua"):
        return parse_douyin(url, raw_input, prefer_quality=prefer_quality)
    if platform == "xiaohongshu":
        return parse_xiaohongshu(url, raw_input)
    if platform == "kuaishou":
        return parse_kuaishou(url, raw_input)
    if platform == "youtube":
        return parse_youtube(url, raw_input, prefer_quality=prefer_quality)
    if platform == "twitter":
        return parse_twitter(url, raw_input)
    if platform == "bilibili":
        return parse_bilibili(url, raw_input, prefer_quality=prefer_quality)
    return parse_via_ytdlp(url, raw_input, platform=platform)


# ============================================================================
# Stage 3: 流式下载、B站/YouTube DASH 并发拉流、封面抽帧与可选 VS Code Preview 转码
# ============================================================================

def stream_download_file(url: str, dest: Path, referer: str = "", ua: str = IOS_UA) -> int:
    """支持流式分块写入与原子重命名的 HTTP 下载算子。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": ua}
    if referer:
        headers["Referer"] = referer
    part_path = dest.with_suffix(dest.suffix + ".part")
    total_bytes = 0
    with httpx.Client(timeout=120.0, follow_redirects=True, headers=headers) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(part_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=262144):
                    if chunk:
                        f.write(chunk)
                        total_bytes += len(chunk)
    if dest.exists():
        dest.unlink()
    part_path.rename(dest)
    return total_bytes


def parallel_range_download_file(
    urls: List[str],
    dest: Path,
    referer: str = "",
    ua: str = DESKTOP_UA,
    total_size: int = 0,
    workers: int = 16,
    chunk_size: int = 8 * 1024 * 1024,
) -> int:
    """
    多线程 HTTP Range 分片并发下载算子（轮询多 CDN 镜像突破单连接限速）。
    """
    valid_urls = [u for u in urls if u]
    if not valid_urls:
        raise ValueError("parallel_range_download_file: urls 列表为空")
    dest.parent.mkdir(parents=True, exist_ok=True)
    base_headers = {"User-Agent": ua}
    if referer:
        base_headers["Referer"] = referer

    if total_size <= 0:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers=base_headers) as client:
            h = client.head(valid_urls[0])
            total_size = int(h.headers.get("content-length") or 0)

    if total_size < chunk_size * 2:
        return stream_download_file(valid_urls[0], dest, referer=referer, ua=ua)

    part_path = dest.with_suffix(dest.suffix + ".part")
    with open(part_path, "wb") as f:
        f.truncate(total_size)

    ranges: List[Tuple[int, int, int]] = []
    idx = 0
    for start in range(0, total_size, chunk_size):
        end = min(start + chunk_size - 1, total_size - 1)
        ranges.append((idx, start, end))
        idx += 1

    write_lock = threading.Lock()

    def _download_segment(seg: Tuple[int, int, int]) -> int:
        seg_idx, start, end = seg
        expected_len = end - start + 1
        for attempt in range(4):
            chosen_url = valid_urls[(seg_idx + attempt) % len(valid_urls)]
            req_headers = dict(base_headers)
            req_headers["Range"] = f"bytes={start}-{end}"
            try:
                with httpx.Client(timeout=60.0, follow_redirects=True, headers=req_headers) as client:
                    r = client.get(chosen_url)
                    if r.status_code in (200, 206) and len(r.content) == expected_len:
                        with write_lock:
                            with open(part_path, "r+b") as wf:
                                wf.seek(start)
                                wf.write(r.content)
                        return expected_len
            except Exception:
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Range segment {start}-{end} failed after retries")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_download_segment, seg) for seg in ranges]
        for fut in concurrent.futures.as_completed(futures):
            fut.result()

    if dest.exists():
        dest.unlink()
    part_path.rename(dest)
    return total_size


def download_bilibili_dash_and_mux(parsed: ParsedMediaResult, dest_mp4: Path, referer: str, ua: str) -> int:
    """并发拉取 B 站 1080p DASH 视频轨与音频轨，并通过 ffmpeg -c copy 无损封装为 MP4。"""
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    v_url = str(parsed.extra.get("dash_video_url") or parsed.video_url)
    v_backups = list(parsed.extra.get("dash_video_backups") or [])
    v_size = int(parsed.extra.get("dash_video_size") or 0)
    a_url = str(parsed.extra.get("dash_audio_url") or "")
    a_backups = list(parsed.extra.get("dash_audio_backups") or [])
    a_size = int(parsed.extra.get("dash_audio_size") or 0)

    v_tmp = dest_mp4.with_suffix(".video.m4s")
    a_tmp = dest_mp4.with_suffix(".audio.m4s")
    try:
        parallel_range_download_file(
            [v_url, *v_backups], v_tmp, referer=referer, ua=ua, total_size=v_size, workers=16
        )
        if a_url:
            parallel_range_download_file(
                [a_url, *a_backups], a_tmp, referer=referer, ua=ua, total_size=a_size, workers=8
            )
            cmd = [
                _resolve_ffmpeg_bin("ffmpeg"), "-y",
                "-i", str(v_tmp),
                "-i", str(a_tmp),
                "-c", "copy",
                "-movflags", "+faststart",
                str(dest_mp4),
            ]
        else:
            cmd = [
                _resolve_ffmpeg_bin("ffmpeg"), "-y",
                "-i", str(v_tmp),
                "-c", "copy",
                "-movflags", "+faststart",
                str(dest_mp4),
            ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        for tmp_f in (v_tmp, a_tmp):
            if tmp_f.exists():
                try:
                    tmp_f.unlink()
                except Exception:
                    pass
    return dest_mp4.stat().st_size if dest_mp4.is_file() else 0


def download_via_ytdlp_dash(url: str, dest_mp4: Path, fmt_selector: str = "") -> int:
    """使用 yt-dlp + Node JS runtime + 8 线程分片并发拉取并合并 YouTube/通用 DASH 音视频流为 MP4。"""
    dest_mp4.parent.mkdir(parents=True, exist_ok=True)
    if not fmt_selector:
        fmt_selector = (
            "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "best[ext=mp4]/best"
        )
    cmd = [
        *_resolve_ytdlp_cmd(),
        *_find_js_runtime_args(),
        *_find_ffmpeg_location_args(),
        "--no-playlist",
        "--concurrent-fragments", "8",
        "-f", fmt_selector,
        "--merge-output-format", "mp4",
        "-o", str(dest_mp4),
        url,
    ]
    subprocess.run(cmd, check=True)
    return dest_mp4.stat().st_size if dest_mp4.is_file() else 0


def extract_preview_frame(video_path: Path, frame_path: Path, timestamp_sec: float = 1.5) -> bool:
    """使用 ffmpeg 抽取一张高清预览帧，供 Artifact 直显与封面校验。"""
    cmd = [
        _resolve_ffmpeg_bin("ffmpeg"), "-y", "-ss", f"{timestamp_sec:.2f}",
        "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2",
        str(frame_path),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode == 0 and frame_path.is_file()


def _has_audio_stream(media_path: Path) -> bool:
    """探测媒体文件是否包含音频轨道，防止无音轨视频执行 loudnorm 失败。"""
    cmd = [
        _resolve_ffmpeg_bin("ffprobe"), "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0 and bool(proc.stdout.strip())


def transcode_for_vscode_preview(src_mp4: Path, dst_mp4: Path) -> bool:
    """
    按 AGENTS.md 第 1.2 节铁律：将 AAC 音轨转码封装为 VP9 + MP3 (.mp4)，
    解决 VS Code 内置 ffmpeg.dll 无 AAC 解码器导致 <video> 无声的问题，并确保单文件 < 18MB。
    """
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)
    has_audio = _has_audio_stream(src_mp4)
    cmd = [
        _resolve_ffmpeg_bin("ffmpeg"), "-y", "-i", str(src_mp4),
        "-vf", "scale='min(1080,iw)':-2",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-b:v", "2M",
        "-deadline", "realtime", "-cpu-used", "4", "-row-mt", "1",
    ]
    if has_audio:
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
    else:
        cmd.append("-an")
    cmd.extend(["-movflags", "+faststart", str(dst_mp4)])
    proc = subprocess.run(cmd, capture_output=True)
    return proc.returncode == 0 and dst_mp4.is_file()


def execute_download(
    parsed: ParsedMediaResult,
    output_dir: Path,
    vscode_preview: bool = False,
) -> Dict[str, Any]:
    """执行媒体资产下载（支持纯视频、纯图集、图文+视频混合推文与 X Article 离线 Markdown）并生成标准化落盘清单。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_author = sanitize_filename(parsed.author.nickname or parsed.platform, max_len=24)
    safe_title = sanitize_filename(parsed.title, max_len=48)
    stem = f"{parsed.platform}_{parsed.item_id}_{safe_author}_{safe_title}"

    referer_map = {
        "douyin": "https://www.iesdouyin.com/",
        "xiaohongshu": "https://www.xiaohongshu.com/",
        "kuaishou": "https://v.kuaishou.com/",
        "bilibili": "https://www.bilibili.com/",
        "twitter": "https://x.com/",
        "youtube": "https://www.youtube.com/",
    }
    referer = referer_map.get(parsed.platform, "")
    ua = DESKTOP_UA if parsed.platform in ("bilibili", "xiaohongshu", "twitter", "youtube") else IOS_UA

    manifest: Dict[str, Any] = {
        "category": parsed.category,
        "platform": parsed.platform,
        "item_id": parsed.item_id,
        "media_type": parsed.media_type,
        "title": parsed.title,
        "author": dataclasses.asdict(parsed.author),
        "source_url": parsed.source_url,
        "resolved_url": parsed.resolved_url,
        "quality_tag": parsed.quality_tag,
        "expected_width": parsed.expected_width,
        "expected_height": parsed.expected_height,
        "expected_size": parsed.expected_size,
        "duration_sec": parsed.duration_sec,
        "video_uri": parsed.video_uri,
        "video_url": parsed.video_url,
        "cover_url": parsed.cover_url,
        "files": {},
    }

    # 专属分支：X Article 长文章（落盘自包含目录：article.md + cover.jpg + assets/* + metadata.json）
    if parsed.media_type == "article":
        raw_sn = sanitize_filename(parsed.author.sec_uid or parsed.author.nickname or "x", max_len=24)
        article_dir = (
            output_dir
            if parsed.item_id in output_dir.name
            else output_dir / f"x_{raw_sn}_{parsed.item_id}"
        )
        assets_dir = article_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        has_cover = False
        if parsed.cover_url:
            cover_path = article_dir / "cover.jpg"
            try:
                stream_download_file(parsed.cover_url, cover_path, referer=referer, ua=ua)
                manifest["files"]["cover"] = str(cover_path.as_posix())
                has_cover = True
            except Exception as e:
                manifest["cover_download_warning"] = str(e)

        img_rel_by_idx: Dict[int, str] = {}
        img_files: List[str] = []
        for item in parsed.images:
            img_dest = assets_dir / f"img_{item.index:02d}.jpg"
            stream_download_file(item.url, img_dest, referer=referer, ua=ua)
            item.local_path = str(img_dest.as_posix())
            img_files.append(item.local_path)
            img_rel_by_idx[item.index] = f"./assets/img_{item.index:02d}.jpg"
        if img_files:
            manifest["files"]["gallery_images"] = img_files

        vid_rel_by_idx: Dict[int, Dict[str, str]] = {}
        raw_vid_files: List[str] = []
        preview_vid_files: List[str] = []
        all_video_urls = ([parsed.video_url] if parsed.video_url else []) + list(parsed.extra_video_urls)
        for v_idx, v_url in enumerate(all_video_urls, start=1):
            v_dest = assets_dir / f"vid_{v_idx:02d}.mp4"
            dl_bytes = stream_download_file(v_url, v_dest, referer=referer, ua=ua)
            raw_vid_files.append(str(v_dest.as_posix()))
            rel_entry = {"raw": f"./assets/vid_{v_idx:02d}.mp4"}
            if v_idx == 1:
                manifest["files"]["video"] = str(v_dest.as_posix())
                manifest["downloaded_bytes"] = dl_bytes
                frame_path = assets_dir / "vid_01_frame.jpg"
                snap_ts = min(3.0, max(0.5, parsed.duration_sec * 0.1)) if parsed.duration_sec > 0 else 1.0
                if extract_preview_frame(v_dest, frame_path, timestamp_sec=snap_ts):
                    manifest["files"]["preview_frame"] = str(frame_path.as_posix())

            # 文章阅读默认转码生成 VP9 + MP3 预览版，确保 VS Code Markdown Preview 有声播放
            vp9_dest = assets_dir / f"vid_{v_idx:02d}_preview_vp9.mp4"
            if transcode_for_vscode_preview(v_dest, vp9_dest):
                preview_vid_files.append(str(vp9_dest.as_posix()))
                rel_entry["preview"] = f"./assets/vid_{v_idx:02d}_preview_vp9.mp4"
            vid_rel_by_idx[v_idx] = rel_entry

        if len(raw_vid_files) > 1:
            manifest["files"]["extra_videos"] = raw_vid_files[1:]
        if preview_vid_files:
            manifest["files"]["vscode_preview_videos"] = preview_vid_files

        md_content = render_twitter_article_markdown(
            parsed=parsed,
            has_cover=has_cover,
            img_rel_by_idx=img_rel_by_idx,
            vid_rel_by_idx=vid_rel_by_idx,
        )
        article_md_path = article_dir / "article.md"
        with open(article_md_path, "w", encoding="utf-8", newline="\n") as mf:
            mf.write(md_content)
        manifest["files"]["article_md"] = str(article_md_path.as_posix())

        meta_path = article_dir / "metadata.json"
        with open(meta_path, "w", encoding="utf-8", newline="\n") as jf:
            jf.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        manifest["files"]["metadata_json"] = str(meta_path.as_posix())
        return manifest

    if parsed.cover_url:
        cover_path = output_dir / f"{stem}_cover.jpg"
        try:
            stream_download_file(parsed.cover_url, cover_path, referer=referer, ua=ua)
            manifest["files"]["cover"] = str(cover_path.as_posix())
        except Exception as e:
            manifest["cover_download_warning"] = str(e)

    # 1. 下载视频（含 video 模式与 mixed 图文+视频混合模式）
    if parsed.media_type in ("video", "mixed") and parsed.video_url:
        video_path = output_dir / f"{stem}.mp4"
        if parsed.platform in ("youtube", "ytdlp_generic"):
            fmt_sel = str(parsed.extra.get("format_selector") or "")
            downloaded_bytes = download_via_ytdlp_dash(parsed.source_url, video_path, fmt_selector=fmt_sel)
        elif parsed.platform == "bilibili" and parsed.extra.get("dash_mode"):
            downloaded_bytes = download_bilibili_dash_and_mux(parsed, video_path, referer=referer, ua=ua)
        else:
            downloaded_bytes = stream_download_file(parsed.video_url, video_path, referer=referer, ua=ua)

        manifest["files"]["video"] = str(video_path.as_posix())
        manifest["downloaded_bytes"] = downloaded_bytes

        # 若一条推文包含多个视频，依次落盘附加视频
        if parsed.extra_video_urls:
            extra_vids: List[str] = []
            for v_idx, ext_u in enumerate(parsed.extra_video_urls, start=2):
                ext_path = output_dir / f"{stem}_v{v_idx:02d}.mp4"
                stream_download_file(ext_u, ext_path, referer=referer, ua=ua)
                extra_vids.append(str(ext_path.as_posix()))
            manifest["files"]["extra_videos"] = extra_vids

        frame_path = output_dir / f"{stem}_frame.jpg"
        snap_ts = min(3.0, max(0.5, parsed.duration_sec * 0.1)) if parsed.duration_sec > 0 else 1.0
        if extract_preview_frame(video_path, frame_path, timestamp_sec=snap_ts):
            manifest["files"]["preview_frame"] = str(frame_path.as_posix())

        if vscode_preview:
            preview_mp4 = output_dir / f"{stem}_preview_vp9.mp4"
            if transcode_for_vscode_preview(video_path, preview_mp4):
                manifest["files"]["vscode_preview_video"] = str(preview_mp4.as_posix())

    # 2. 下载图集图片（含 gallery 模式与 mixed 图文+视频混合模式）
    if parsed.images:
        gallery_dir = output_dir / f"{stem}_gallery"
        gallery_dir.mkdir(parents=True, exist_ok=True)
        img_files: List[str] = []
        live_files: List[str] = []
        for item in parsed.images:
            img_dest = gallery_dir / f"img_{item.index:02d}.jpg"
            stream_download_file(item.url, img_dest, referer=referer, ua=ua)
            item.local_path = str(img_dest.as_posix())
            img_files.append(item.local_path)
            if item.live_photo_url:
                live_dest = gallery_dir / f"img_{item.index:02d}_live.mp4"
                stream_download_file(item.live_photo_url, live_dest, referer=referer, ua=ua)
                item.live_local_path = str(live_dest.as_posix())
                live_files.append(item.live_local_path)
        manifest["files"]["gallery_images"] = img_files
        if live_files:
            manifest["files"]["gallery_live_videos"] = live_files

    meta_path = output_dir / f"{stem}_metadata.json"
    meta_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"]["metadata_json"] = str(meta_path.as_posix())
    return manifest


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="mk-video-download 全网无水印视频与图集解析下载总控工具")
    parser.add_argument("--input", "-i", required=True, help="分享文案口令或视频/图集 URL")
    parser.add_argument("--output-dir", "-o", default="./.gdrive/downloads", help="下载落盘目录 (默认 ./.gdrive/downloads)")
    parser.add_argument("--quality", default="1080p", choices=["4k", "2k", "1080p", "720p", "540p"], help="目标画质偏好 (默认 1080p)")
    parser.add_argument("--parse-only", action="store_true", help="仅解析无水印直链与元数据，不下载媒体文件")
    parser.add_argument("--vscode-preview", action="store_true", help="额外转码生成兼容 VS Code Markdown Preview 的 VP9+MP3 视频")
    parser.add_argument("--evaluate", action="store_true", help="下载完成后自动运行 Gate G1~G4 门禁校验与 100 分制评分")
    parser.add_argument("--research", action="store_true", help="下载媒体后生成带时间码的研究素材索引")
    args = parser.parse_args()
    if args.parse_only and args.research:
        parser.error("--parse-only 与 --research 不能同时使用")

    t0 = time.time()
    parsed = parse_media_input(args.input, prefer_quality=args.quality)
    if args.parse_only:
        print(json.dumps(dataclasses.asdict(parsed), ensure_ascii=False, indent=2))
        return 0

    out_dir = Path(args.output_dir)
    manifest = execute_download(parsed, output_dir=out_dir, vscode_preview=args.vscode_preview)
    manifest["elapsed_sec"] = round(time.time() - t0, 2)

    if args.research:
        from research_media import collect_evidence
        manifest["research"] = collect_evidence(manifest["files"], out_dir, extract_preview_frame)

    if args.evaluate:
        from video_download_evaluator import evaluate_manifest
        eval_report = evaluate_manifest(Path(manifest["files"]["metadata_json"]))
        manifest["evaluation"] = eval_report
    if args.evaluate or args.research:
        Path(manifest["files"]["metadata_json"]).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
