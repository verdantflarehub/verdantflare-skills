#!/usr/bin/env python3
"""VerdantFlare Image 客户端命令行工具。

支持向 VerdantFlare Studio MCP (`/mcp` JSON-RPC) 或兼容 REST 网关提交原子生图/编辑任务、
受控导入本地参考图（`image.import` / 分片导入）、异步轮询执行状态（`image.status` / `image.result`）、
通过 Studio 受控路由（`/apps/image/artifacts/{artifact_id}/content`）下载不可变 Artifact 图片并校验 SHA-256 完整性，
以及提交生成后内容分级评估（`image.rate_content`）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
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
from typing import Any

DEFAULT_BASE_URL = ""
PROJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SINGLE_IMPORT_MAX_BYTES = 3 * 1024 * 1024
CHUNK_IMPORT_SIZE_BYTES = 512 * 1024


class ImageClientError(Exception):
    """客户端执行异常。"""


def validate_project_id(value: str) -> str:
    """只接受 Studio 生成的 UUIDv7 Project ID。"""
    if not isinstance(value, str) or not PROJECT_ID_PATTERN.fullmatch(value):
        raise ImageClientError("project_id 必须是当前 Studio Project 生成的小写 UUIDv7，不能使用 default 或路径别名")
    return value


def find_env_file() -> Path | None:
    """寻找工作区 .env 配置文件。"""
    explicit = os.environ.get("IMAGE_MCP_ENV_FILE")
    if explicit and Path(explicit).is_file():
        return Path(explicit)

    curr = Path.cwd().resolve()
    for parent in [curr, *curr.parents]:
        env_path = parent / ".env"
        if env_path.is_file():
            return env_path

    skill_root = Path(__file__).resolve().parents[1]
    for parent in [skill_root, *skill_root.parents]:
        env_path = parent / ".env"
        if env_path.is_file():
            return env_path
    return None


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 .env 键值对。"""
    res: dict[str, str] = {}
    if not path.is_file():
        return res
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'\"")
        res[k] = v
    return res


def load_config() -> tuple[str, str]:
    """读取 Studio / Image MCP 基础地址与 Bearer Token。"""
    base_url = (os.environ.get("STUDIO_MCP_URL", "") or os.environ.get("IMAGE_MCP_URL", "")).strip()
    token = (os.environ.get("STUDIO_MCP_BEARER_TOKEN", "") or os.environ.get("IMAGE_MCP_BEARER_TOKEN", "")).strip()

    if not (base_url and token):
        env_file = find_env_file()
        if env_file:
            env_vars = parse_env_file(env_file)
            if not base_url:
                base_url = (env_vars.get("STUDIO_MCP_URL", "") or env_vars.get("IMAGE_MCP_URL", "")).strip()
            if not token:
                token = (env_vars.get("STUDIO_MCP_BEARER_TOKEN", "") or env_vars.get("IMAGE_MCP_BEARER_TOKEN", "")).strip()

    if not base_url:
        raise ImageClientError(
            "未检测到 STUDIO_MCP_URL 或 IMAGE_MCP_URL 配置。请在 .env 文件或环境变量中配置 STUDIO_MCP_URL。"
        )

    base_url = base_url.rstrip("/")
    return base_url, token


def is_mcp_endpoint(base_url: str) -> bool:
    """判断当前地址是否为 Studio JSON-RPC `/mcp` 端点。"""
    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    return parsed.path.rstrip("/").endswith("/mcp")


def resolve_studio_root(base_url: str) -> str:
    """从 Studio `/mcp` 地址还原受控根地址（不含 `/mcp`）。"""
    clean = base_url.rstrip("/")
    if clean.endswith("/mcp"):
        return clean[:-4]
    return clean


def resolve_download_url(base_url: str, artifact_id: str, download_path: str = "") -> str:
    """将原生 Artifact ID 或 download_path 解析为 Studio 受控下载 URL。"""
    if is_mcp_endpoint(base_url):
        root = resolve_studio_root(base_url)
        if download_path:
            dp = download_path.strip()
            if dp.startswith("/apps/image/"):
                return f"{root}{dp}"
            if dp.startswith("/image/"):
                return f"{root}/apps{dp}"
            if dp.startswith("/artifacts/"):
                return f"{root}/apps/image{dp}"
        return f"{root}/apps/image/artifacts/{artifact_id}/content"
    return f"{base_url.rstrip('/')}/artifacts/{artifact_id}/content"


def make_request(
    url: str,
    method: str = "GET",
    token: str = "",
    data: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any] | bytes]:
    """发起 HTTP 请求并处理响应。"""
    headers = {
        "Accept": "application/json",
        "User-Agent": "VerdantFlare-ImageClient/1.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body_bytes = None
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")

    for attempt in range(4):
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "")
                content = resp.read()
                if "application/json" in content_type:
                    try:
                        return status, json.loads(content.decode("utf-8"))
                    except Exception:
                        return status, {"raw": content.decode("utf-8", errors="replace")}
                return status, content
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            err_body = e.read()
            try:
                parsed = json.loads(err_body.decode("utf-8"))
            except Exception:
                parsed = {"error": f"HTTP {e.code}: {e.reason}", "raw": err_body.decode("utf-8", errors="replace")}
            return e.code, parsed
        except Exception as e:
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise ImageClientError(f"请求失败: {e}") from e
    return 502, {"error": "HTTP 502: Bad Gateway"}


def call_mcp_tool(
    base_url: str,
    token: str,
    name: str,
    arguments: dict[str, Any],
    timeout: float = 60.0,
) -> dict[str, Any]:
    """向 Studio `/mcp` JSON-RPC 网关调用指定工具并解析结果。"""
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 1000000000,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
        },
    }
    status_code, res = make_request(base_url, method="POST", token=token, data=payload, timeout=timeout)
    if status_code != 200 or not isinstance(res, dict):
        raise ImageClientError(f"MCP 工具 {name} 调用失败 (HTTP {status_code}): {res}")

    if "error" in res and res["error"]:
        raise ImageClientError(f"MCP 工具 {name} 返回 JSON-RPC 错误: {res['error']}")

    result = res.get("result", {})
    if not isinstance(result, dict):
        raise ImageClientError(f"MCP 工具 {name} 返回无效 result: {res}")

    if result.get("isError"):
        content_list = result.get("content", [])
        err_text = content_list[0].get("text", "") if content_list and isinstance(content_list[0], dict) else str(result)
        raise ImageClientError(f"MCP 工具 {name} 执行报错: {err_text}")

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    content_list = result.get("content", [])
    if content_list and isinstance(content_list[0], dict):
        text = content_list[0].get("text", "")
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {"raw_text": text}
    return result


def calculate_sha256(data: bytes) -> str:
    """计算二进制数据的 SHA-256 十六进制值。"""
    return hashlib.sha256(data).hexdigest()


def cmd_stats(args: argparse.Namespace) -> int:
    """查询服务全局任务排队统计或 MCP 渠道能力。"""
    base_url, token = load_config()
    if is_mcp_endpoint(base_url):
        try:
            res = call_mcp_tool(base_url, token, "image.capabilities", {})
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        except ImageClientError as e:
            print(f"[-] 获取 MCP 能力失败: {e}", file=sys.stderr)
            return 1

    stats_url = f"{base_url}/api/tasks/stats"
    status, res = make_request(stats_url, method="GET", token=token)
    if status != 200:
        print(f"[-] 获取指标失败 (HTTP {status}): {res}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """列出任务列表（兼容 REST 接口）。"""
    base_url, token = load_config()
    if is_mcp_endpoint(base_url):
        print("[-] 当前 Studio `/mcp` 网关未注册 `image.list`，请使用 `status <task_id>` 查询已知任务。", file=sys.stderr)
        return 1

    query = {}
    if args.project_id:
        query["project_id"] = validate_project_id(args.project_id)
    if args.engine:
        query["engine"] = args.engine
    if args.status:
        query["status"] = args.status
    if args.limit:
        query["limit"] = str(args.limit)

    qs = urllib.parse.urlencode(query)
    url = f"{base_url}/api/tasks" + (f"?{qs}" if qs else "")

    status, res = make_request(url, method="GET", token=token)
    if status != 200:
        print(f"[-] 查询任务列表失败 (HTTP {status}): {res}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    tasks = res.get("tasks", [])
    total = res.get("total", len(tasks))
    print(f"[*] 共查询到 {total} 笔任务 (当前展示前 {len(tasks)} 笔):")
    print(f"{'TASK ID':<22} {'STATUS':<12} {'ENGINE':<8} {'MODEL':<20} {'DURATION':<10} {'PROMPT'}")
    print("-" * 90)
    for t in tasks:
        tid = t.get("task_id", "")
        st = t.get("status", "")
        eng = t.get("engine", "")
        mod = t.get("model") or "-"
        dur = f"{t.get('duration_seconds', 0.0):.1f}s"
        p = (t.get("prompt_preview") or "").replace("\n", " ")[:35]
        print(f"{tid:<22} {st:<12} {eng:<8} {mod:<20} {dur:<10} {p}")
    return 0


def poll_task_completion(
    base_url: str,
    token: str,
    task_id: str,
    project_id: str,
    timeout: float = 180.0,
    poll_interval: float = 2.0,
) -> dict[str, Any]:
    """轮询等待单笔任务进入终态。"""
    start_time = time.time()
    last_status = ""
    mcp_mode = is_mcp_endpoint(base_url)

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise ImageClientError(f"任务 {task_id} 轮询超时 ({timeout:.0f}s)")

        if mcp_mode:
            status_res = call_mcp_tool(base_url, token, "image.status", {"task_id": task_id}, timeout=30.0)
            curr_status = status_res.get("status", "")
            if curr_status != last_status:
                last_status = curr_status
                print(f"[*] 任务状态: {curr_status} (已耗时 {elapsed:.1f}s)")

            if curr_status == "completed":
                res_data = call_mcp_tool(base_url, token, "image.result", {"task_id": task_id}, timeout=30.0)
                merged = dict(status_res)
                merged.update(res_data)
                if "artifact_id" not in merged and isinstance(merged.get("artifact"), dict):
                    merged["artifact_id"] = merged["artifact"].get("artifact_id")
                return merged
            if curr_status in ("failed", "canceled"):
                err = status_res.get("error") or "未知错误"
                raise ImageClientError(f"任务已终结 ({curr_status}): {err}")
        else:
            query = {"project_id": project_id, "limit": "50"}
            url = f"{base_url}/api/tasks?{urllib.parse.urlencode(query)}"
            status_code, res = make_request(url, method="GET", token=token)
            if status_code == 200 and isinstance(res, dict):
                tasks = res.get("tasks", [])
                target = next((t for t in tasks if t.get("task_id") == task_id), None)
                if target:
                    curr_status = target.get("status", "")
                    if curr_status != last_status:
                        last_status = curr_status
                        print(f"[*] 任务状态: {curr_status} (已耗时 {elapsed:.1f}s)")

                    if curr_status == "completed":
                        return target
                    if curr_status in ("failed", "canceled"):
                        err = target.get("error") or "未知错误"
                        raise ImageClientError(f"任务已终结 ({curr_status}): {err}")

        time.sleep(poll_interval)


def download_artifact(
    base_url: str,
    token: str,
    artifact_id: str,
    output_path: Path,
    expected_sha256: str = "",
    download_path: str = "",
) -> str:
    """从服务端受控路由下载产物并校验哈希，返回实际 SHA-256。"""
    dl_url = resolve_download_url(base_url, artifact_id, download_path=download_path)
    print(f"[*] 正在下载产物: {dl_url}")
    status_code, content = make_request(dl_url, method="GET", token=token, timeout=60.0)
    if status_code != 200 or not isinstance(content, bytes):
        raise ImageClientError(f"产物下载失败 (HTTP {status_code}): {content}")

    actual_sha = calculate_sha256(content)
    if expected_sha256 and actual_sha.lower() != expected_sha256.lower():
        raise ImageClientError(
            f"SHA-256 哈希校验不匹配! 期望: {expected_sha256}, 实际: {actual_sha}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    print(f"[+] 图像已保存至: {output_path} (大小: {len(content):,} bytes, SHA-256: {actual_sha})")
    return actual_sha


def upload_artifact_file(
    base_url: str,
    token: str,
    project_id: str,
    file_path: Path,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """通过 Studio MCP `image.import`（或分片导入）或兼容 REST 接口上传本地素材为受控 Artifact。"""
    file_bytes = file_path.read_bytes()
    filename = file_path.name
    sha256_hex = calculate_sha256(file_bytes)

    if is_mcp_endpoint(base_url):
        if len(file_bytes) <= SINGLE_IMPORT_MAX_BYTES:
            b64 = base64.b64encode(file_bytes).decode("ascii")
            return call_mcp_tool(
                base_url,
                token,
                "image.import",
                {
                    "project_id": project_id,
                    "filename": filename,
                    "expected_sha256": sha256_hex,
                    "content_base64": b64,
                },
                timeout=timeout,
            )
        # 超过 3 MiB 时走分片导入
        idem_key = f"import-{project_id[:8]}-{sha256_hex[:16]}"
        prep = call_mcp_tool(
            base_url,
            token,
            "image.import_prepare",
            {
                "project_id": project_id,
                "idempotency_key": idem_key,
                "filename": filename,
                "size": len(file_bytes),
                "sha256": sha256_hex,
                "purpose": "reference",
            },
            timeout=timeout,
        )
        session_id = prep.get("upload_id") or prep.get("session_id") or prep.get("import_id")
        offset = int(prep.get("offset", 0))
        while offset < len(file_bytes):
            chunk = file_bytes[offset : offset + CHUNK_IMPORT_SIZE_BYTES]
            chunk_res = call_mcp_tool(
                base_url,
                token,
                "image.import_chunk",
                {
                    "project_id": project_id,
                    "upload_id": session_id,
                    "offset": offset,
                    "content_base64": base64.b64encode(chunk).decode("ascii"),
                },
                timeout=timeout,
            )
            offset = int(chunk_res.get("offset", offset + len(chunk)))
        return call_mcp_tool(
            base_url,
            token,
            "image.import_commit",
            {
                "project_id": project_id,
                "upload_id": session_id,
                "expected_sha256": sha256_hex,
            },
            timeout=timeout,
        )

    boundary = f"----ImageClientUpload{int(time.time() * 1000)}"
    content_type = f"multipart/form-data; boundary={boundary}"
    media_type = mimetypes.guess_type(filename)[0] or "image/png"

    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="project_id"\r\n\r\n')
    body.extend(f"{project_id}\r\n".encode("utf-8"))

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {media_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = f"{base_url}/api/artifacts/upload"
    headers = {
        "Content-Type": content_type,
        "Accept": "application/json",
        "User-Agent": "VerdantFlare-ImageClient/1.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8")
            return json.loads(resp_body)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise ImageClientError(f"素材上传 MCP 失败 (HTTP {e.code}): {err}") from e
    except Exception as e:
        raise ImageClientError(f"素材上传 MCP 异常: {e}") from e


def submit_rate_content(
    base_url: str,
    token: str,
    version_id: str,
    project_id: str,
    rating: str,
    reason: str,
    rubric_version: str = "rubric v1.0",
) -> dict[str, Any]:
    """调用 `image.rate_content` 提交生成后内容分级评估。"""
    args_dict: dict[str, Any] = {
        "version_id": version_id,
        "rating": rating,
        "reason": reason,
        "rubric_version": rubric_version,
    }
    if project_id:
        args_dict["project_id"] = validate_project_id(project_id)
    return call_mcp_tool(base_url, token, "image.rate_content", args_dict, timeout=30.0)


def cmd_rate(args: argparse.Namespace) -> int:
    """独立子命令：提交内容分级评估。"""
    base_url, token = load_config()
    try:
        res = submit_rate_content(
            base_url=base_url,
            token=token,
            version_id=args.version_id,
            project_id=args.project_id or "",
            rating=args.rating,
            reason=args.reason,
            rubric_version=args.rubric_version,
        )
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    except ImageClientError as e:
        print(f"[-] 内容分级提交失败: {e}", file=sys.stderr)
        return 1


def cmd_generate(args: argparse.Namespace) -> int:
    """提交原子生图/编辑任务（统一经由 Studio Image MCP 或兼容 REST 网关）。"""
    base_url, token = load_config()
    prompt = args.prompt.strip()
    if not prompt:
        print("[-] 错误: --prompt 不能为空", file=sys.stderr)
        return 1

    engine = (args.engine or "codex").lower()
    project_id = validate_project_id(args.project_id)
    idempotency_key = args.idempotency_key or f"cli-{int(time.time() * 1000)}-{os.urandom(3).hex()}"
    resolved_model = args.model or ("gpt-image-2.6-sunburst" if engine == "codex" else "gemini-nano-banana-2.1")

    target_quality = getattr(args, "quality", "high") or "high"
    background = getattr(args, "background", "auto") or "auto"

    source_artifact_id = getattr(args, "source_artifact_id", "") or ""
    if getattr(args, "image", None):
        img_path = Path(args.image).resolve()
        if not img_path.is_file():
            print(f"[-] 错误: 参考底图不存在: {img_path}", file=sys.stderr)
            return 1
        print(f"[*] 正在将本地参考图导入至 Image MCP 服务: {img_path.name}...")
        try:
            upload_res = upload_artifact_file(
                base_url=base_url,
                token=token,
                project_id=project_id,
                file_path=img_path,
            )
        except ImageClientError as e:
            print(f"[-] 上传参考底图失败: {e}", file=sys.stderr)
            return 1

        artifact_data = upload_res.get("artifact", {})
        source_artifact_id = artifact_data.get("artifact_id", "") or upload_res.get("artifact_id", "")
        if not source_artifact_id:
            print(f"[-] 上传响应中未包含有效的 artifact_id: {upload_res}", file=sys.stderr)
            return 1
        print(
            f"[+] 本地主参考图已通过 MCP 登记为受控 Artifact: {source_artifact_id} (SHA-256: {artifact_data.get('sha256', '')[:16]}...)"
        )

    ref_artifact_ids: list[str] = []
    if getattr(args, "ref_images", None):
        for ref_img_str in args.ref_images:
            r_path = Path(ref_img_str).resolve()
            if not r_path.is_file():
                print(f"[-] 错误: 附加参考底图不存在: {r_path}", file=sys.stderr)
                return 1
            print(f"[*] 正在将附加参考图导入至 Image MCP 服务: {r_path.name}...")
            try:
                upload_res = upload_artifact_file(
                    base_url=base_url,
                    token=token,
                    project_id=project_id,
                    file_path=r_path,
                )
            except ImageClientError as e:
                print(f"[-] 上传附加参考图失败: {e}", file=sys.stderr)
                return 1
            rid = upload_res.get("artifact", {}).get("artifact_id", "") or upload_res.get("artifact_id", "")
            if rid:
                ref_artifact_ids.append(rid)
                print(f"[+] 附加参考图已登记为受控 Artifact: {rid}")

    mcp_mode = is_mcp_endpoint(base_url)

    if mcp_mode:
        tool_name = "image.edit" if source_artifact_id else "image.create"
        mcp_args: dict[str, Any] = {
            "project_id": project_id,
            "idempotency_key": idempotency_key,
            "prompt": prompt,
            "engine": engine,
            "model": resolved_model,
            "aspect_ratio": args.aspect_ratio,
            "resolution": args.resolution,
            "quality": target_quality,
            "background": background,
        }
        if source_artifact_id:
            mcp_args["source_artifact_id"] = source_artifact_id
        if ref_artifact_ids:
            mcp_args["reference_artifact_ids"] = ref_artifact_ids
        if engine in ("codex", "gemini", "grok") and getattr(args, "budget_limit", ""):
            mcp_args["budget_limit"] = args.budget_limit
        if getattr(args, "channel_account_id", ""):
            mcp_args["channel_account_id"] = args.channel_account_id

        print(
            f"[*] 提交 MCP 生图请求 -> {base_url} [{tool_name}] (engine={engine}, model={resolved_model}, ratio={args.aspect_ratio}, res={args.resolution})"
        )
        try:
            res = call_mcp_tool(base_url, token, tool_name, mcp_args, timeout=60.0)
        except ImageClientError as e:
            print(f"[-] 提交失败: {e}", file=sys.stderr)
            return 1
    else:
        ratio_size_map = {
            "16:9": "2048x1152",
            "9:16": "1152x2048",
            "1:1": "1024x1024",
            "4:3": "1792x1344",
            "3:4": "1344x1792",
            "3:2": "1536x1024",
            "2:3": "1024x1536",
        }
        target_size = ratio_size_map.get(args.aspect_ratio, "2048x1152")
        payload: dict[str, Any] = {
            "project_id": project_id,
            "idempotency_key": idempotency_key,
            "prompt": prompt,
            "engine": engine,
            "model": resolved_model,
            "size": target_size,
            "aspect_ratio": args.aspect_ratio,
            "resolution": args.resolution,
            "quality": target_quality,
            "background": background,
        }
        if source_artifact_id:
            payload["source_artifact_id"] = source_artifact_id
        if ref_artifact_ids:
            payload["reference_artifact_ids"] = ref_artifact_ids

        post_url = f"{base_url}/api/tasks"
        print(f"[*] 提交生图请求 -> {post_url} (engine={engine}, ratio={args.aspect_ratio}, res={args.resolution})")
        status_code, res = make_request(post_url, method="POST", token=token, data=payload)
        if status_code not in (200, 201) or not isinstance(res, dict):
            print(f"[-] 提交失败 (HTTP {status_code}): {res}", file=sys.stderr)
            return 1

    task_id = res.get("task_id")
    initial_status = res.get("status")
    print(f"[+] 任务已成功提交! Task ID: {task_id}, 初始状态: {initial_status}")

    should_wait = args.wait or bool(args.output)
    if not should_wait:
        print(f"[*] 可使用以下命令查询状态:")
        print(f"    python3 {sys.argv[0]} status {task_id}")
        return 0

    print(f"[*] 正在等待任务完成 (超时时间: {args.timeout}s)...")
    try:
        task_record = poll_task_completion(
            base_url=base_url,
            token=token,
            task_id=task_id,
            project_id=project_id,
            timeout=args.timeout,
        )
    except ImageClientError as e:
        print(f"[-] {e}", file=sys.stderr)
        return 1

    print(f"[+] 任务生成完成! 实际耗时: {task_record.get('duration_seconds', 0):.2f}s")
    artifact_info = task_record.get("artifact") if isinstance(task_record.get("artifact"), dict) else {}
    artifact_id = task_record.get("artifact_id") or artifact_info.get("artifact_id")
    expected_sha256 = task_record.get("sha256") or artifact_info.get("sha256") or ""
    download_path = task_record.get("download_path") or ""

    if args.output and artifact_id:
        out_path = Path(args.output).resolve()
        try:
            download_artifact(
                base_url=base_url,
                token=token,
                artifact_id=artifact_id,
                output_path=out_path,
                expected_sha256=expected_sha256,
                download_path=download_path,
            )
        except ImageClientError as e:
            print(f"[-] {e}", file=sys.stderr)
            return 1
    elif artifact_id:
        print(f"[*] 产物 Artifact ID: {artifact_id}")
        print(f"[*] 下载地址: {resolve_download_url(base_url, artifact_id, download_path=download_path)}")

    if getattr(args, "rate_rating", None) and (artifact_id or task_id):
        target_vid = artifact_id or task_id
        reason_str = getattr(args, "rate_reason", "") or "未触发 18+ 类别（S1-S4、V1），符合全年龄标准"
        try:
            rate_res = submit_rate_content(
                base_url=base_url,
                token=token,
                version_id=target_vid,
                project_id=project_id,
                rating=args.rate_rating,
                reason=reason_str,
            )
            print(f"[+] 已完成内容分级评估提交 (K09): version_id={target_vid}, rating={args.rate_rating}, res={rate_res}")
        except ImageClientError as e:
            print(f"[-] 内容分级评估提交失败: {e}", file=sys.stderr)
            return 1

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """查询单笔任务状态。"""
    base_url, token = load_config()
    task_id = args.task_id
    if is_mcp_endpoint(base_url):
        try:
            status_res = call_mcp_tool(base_url, token, "image.status", {"task_id": task_id})
            if status_res.get("status") == "completed":
                try:
                    res_data = call_mcp_tool(base_url, token, "image.result", {"task_id": task_id})
                    status_res["result"] = res_data
                except ImageClientError:
                    pass
            print(json.dumps(status_res, indent=2, ensure_ascii=False))
            return 0
        except ImageClientError as e:
            print(f"[-] 查询失败: {e}", file=sys.stderr)
            return 1

    url = f"{base_url}/api/tasks?limit=100"
    status_code, res = make_request(url, method="GET", token=token)
    if status_code != 200 or not isinstance(res, dict):
        print(f"[-] 查询失败 (HTTP {status_code}): {res}", file=sys.stderr)
        return 1

    tasks = res.get("tasks", [])
    target = next((t for t in tasks if t.get("task_id") == task_id), None)
    if not target:
        print(f"[-] 未找到任务: {task_id}", file=sys.stderr)
        return 1

    print(json.dumps(target, indent=2, ensure_ascii=False))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    """单独下载指定产物。"""
    base_url, token = load_config()
    out_path = Path(args.output).resolve()
    try:
        download_artifact(
            base_url=base_url,
            token=token,
            artifact_id=args.artifact_id,
            output_path=out_path,
            expected_sha256=args.expected_sha256 or "",
        )
        return 0
    except ImageClientError as e:
        print(f"[-] {e}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VerdantFlare Image 客户端命令行工具 (支持 Studio /mcp JSON-RPC 与受控下载)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # stats
    sub_stats = subparsers.add_parser("stats", help="查看服务端排队指标统计或 MCP 渠道能力")
    sub_stats.set_defaults(func=cmd_stats)

    # list
    sub_list = subparsers.add_parser("list", help="列出任务列表")
    sub_list.add_argument("--project-id", default=None, help="按项目筛选")
    sub_list.add_argument("--engine", choices=["codex", "gemini"], default=None, help="按引擎筛选")
    sub_list.add_argument("--status", choices=["queued", "running", "completed", "failed", "canceled"], default=None, help="按状态筛选")
    sub_list.add_argument("--limit", type=int, default=20, help="展示条数")
    sub_list.add_argument("--json", action="store_true", help="以完整 JSON 输出")
    sub_list.set_defaults(func=cmd_list)

    # status
    sub_status = subparsers.add_parser("status", help="查询单笔任务状态")
    sub_status.add_argument("task_id", help="任务 ID")
    sub_status.set_defaults(func=cmd_status)

    # generate
    sub_gen = subparsers.add_parser("generate", help="提交生图或编辑任务")
    sub_gen.add_argument("--prompt", required=True, help="生图提示词")
    sub_gen.add_argument("--engine", choices=["codex", "gemini", "grok", "moss-image"], default="codex", help="底层引擎驱动 (默认 codex)")
    sub_gen.add_argument("--model", default="", help="指定模型覆盖默认值 (codex 默认 gpt-image-2.6-sunburst，可选 gpt-image-2.6-flare；gemini 默认 gemini-nano-banana-2.1)")
    sub_gen.add_argument("--aspect-ratio", choices=["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"], default="16:9", help="画幅比例 (默认 16:9)")
    sub_gen.add_argument("--resolution", choices=["1k", "2k", "4k"], default="2k", help="分辨率 (默认 2k)")
    sub_gen.add_argument("--quality", choices=["auto", "low", "medium", "high", "xhigh", "max", "hd", "standard"], default="high", help="图像质量 (默认 high)")
    sub_gen.add_argument("--background", choices=["auto", "transparent", "opaque"], default="auto", help="背景模式 (transparent 生成纯透明通道 PNG)")
    sub_gen.add_argument("--project-id", required=True, help="当前 Studio Project 的小写 UUIDv7 标识")
    sub_gen.add_argument("--idempotency-key", default="", help="客户端幂等业务键")
    sub_gen.add_argument("--budget-limit", default="1.00", help="USD 预算上限预留 (默认 1.00，moss-image 自动忽略)")
    sub_gen.add_argument("--channel-account-id", default="", help="可选渠道账号 ID")
    sub_gen.add_argument("--wait", action="store_true", help="等待任务生成完成")
    sub_gen.add_argument("--timeout", type=float, default=600.0, help="轮询超时时间 (秒，默认 600)")
    sub_gen.add_argument("--image", "--reference", dest="image", default="", help="主参考底图本地路径")
    sub_gen.add_argument("--source-artifact-id", dest="source_artifact_id", default="", help="已导入的主参考图原生 Artifact ID")
    sub_gen.add_argument("--ref-image", "--ref", dest="ref_images", action="append", default=[], help="附加参考图路径（可多次指定）")
    sub_gen.add_argument("-o", "--output", default="", help="下载并保存产物的目标路径")
    sub_gen.add_argument("--rate-rating", choices=["general", "nsfw"], default=None, help="生成完成后自动调用 image.rate_content 提交分级 (K09)")
    sub_gen.add_argument("--rate-reason", default="", help="内容分级评估说明")
    sub_gen.set_defaults(func=cmd_generate)

    # download
    sub_dl = subparsers.add_parser("download", help="下载指定 Artifact 图像")
    sub_dl.add_argument("--artifact-id", required=True, help="Artifact ID")
    sub_dl.add_argument("-o", "--output", required=True, help="保存目标路径")
    sub_dl.add_argument("--expected-sha256", default="", help="预期校验的 SHA-256")
    sub_dl.set_defaults(func=cmd_download)

    # rate
    sub_rate = subparsers.add_parser("rate", help="提交生成后内容分级评估 (image.rate_content)")
    sub_rate.add_argument("--version-id", required=True, help="目标 artifact_id 或 task_id")
    sub_rate.add_argument("--project-id", default="", help="所属 Studio Project ID")
    sub_rate.add_argument("--rating", choices=["general", "nsfw"], required=True, help="分级结论")
    sub_rate.add_argument("--reason", required=True, help="分级理由")
    sub_rate.add_argument("--rubric-version", default="rubric v1.0", help="评估规范版本 (默认 rubric v1.0)")
    sub_rate.set_defaults(func=cmd_rate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
