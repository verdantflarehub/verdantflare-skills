#!/usr/bin/env python3
"""VerdantFlare Image 客户端命令行工具。

支持向 VerdantFlare Image MCP / REST 网关提交原子生图任务、
异步轮询执行状态、下载不可变 Artifact 图片并校验 SHA-256 完整性。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = ""


class ImageClientError(Exception):
    """客户端执行异常。"""


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
    """读取 Image MCP 基础地址与 Bearer Token。"""
    base_url = os.environ.get("IMAGE_MCP_URL", "").strip()
    token = os.environ.get("IMAGE_MCP_BEARER_TOKEN", "").strip()

    if not (base_url and token):
        env_file = find_env_file()
        if env_file:
            env_vars = parse_env_file(env_file)
            if not base_url:
                base_url = env_vars.get("IMAGE_MCP_URL", "").strip()
            if not token:
                token = env_vars.get("IMAGE_MCP_BEARER_TOKEN", "").strip()

    if not base_url:
        raise ImageClientError(
            "未检测到 IMAGE_MCP_URL 配置。请在 .env 文件或环境变量中配置 IMAGE_MCP_URL。"
        )

    # 去除末尾斜杠
    base_url = base_url.rstrip("/")

    return base_url, token


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
        "User-Agent": "VerdantFlare-ImageClient/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body_bytes = None
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")

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
        err_body = e.read()
        try:
            parsed = json.loads(err_body.decode("utf-8"))
        except Exception:
            parsed = {"error": f"HTTP {e.code}: {e.reason}", "raw": err_body.decode("utf-8", errors="replace")}
        return e.code, parsed
    except Exception as e:
        raise ImageClientError(f"请求失败: {e}") from e


def calculate_sha256(data: bytes) -> str:
    """计算二进制数据的 SHA-256 十六进制值。"""
    return hashlib.sha256(data).hexdigest()


def cmd_stats(args: argparse.Namespace) -> int:
    """查询服务全局任务排队统计。"""
    base_url, token = load_config()
    stats_url = f"{base_url}/api/tasks/stats"
    status, res = make_request(stats_url, method="GET", token=token)
    if status != 200:
        print(f"[-] 获取指标失败 (HTTP {status}): {res}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """列出任务列表。"""
    base_url, token = load_config()
    query = {}
    if args.project_id:
        query["project_id"] = args.project_id
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
    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            raise ImageClientError(f"任务 {task_id} 轮询超时 ({timeout:.0f}s)")

        # 通过列表接口按 project_id 检索
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
) -> None:
    """从服务端下载产物并校验哈希。"""
    dl_url = f"{base_url}/artifacts/{artifact_id}/content"
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


def parse_sse_data_events(response: urllib.response.addinfourl) -> list[dict[str, Any]]:
    """解析 SSE 流数据事件。"""
    events: list[dict[str, Any]] = []
    event_type = ""
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_type, data_lines
        if not data_lines:
            event_type = ""
            return
        current_event_type = event_type
        raw = "\n".join(data_lines).strip()
        event_type = ""
        data_lines = []
        if not raw or raw == "[DONE]":
            return
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            return
        if current_event_type and "type" not in item:
            item["type"] = current_event_type
        events.append(item)

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
            continue

    flush()
    return events


def collect_b64_values(value: Any) -> list[str]:
    """从结构化响应中递归提取 base64 图像数据。"""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"result", "b64_json", "partial_image_b64"} and isinstance(child, str) and child:
                found.append(child)
            else:
                found.extend(collect_b64_values(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_b64_values(child))
    return found


def execute_reference_generation(args: argparse.Namespace) -> int:
    """基于参考底图执行生图任务（遵循 Picture 1 / 角色圣经引导生成）。"""
    image_path = Path(args.image).resolve()
    if not image_path.is_file():
        print(f"[-] 错误: 参考底图不存在: {image_path}", file=sys.stderr)
        return 1

    prompt = args.prompt.strip()
    if not prompt:
        print("[-] 错误: --prompt 不能为空", file=sys.stderr)
        return 1

    env_file = find_env_file()
    env_vars = parse_env_file(env_file) if env_file else {}
    base_url = os.environ.get("OPENAI_BASE_URL") or env_vars.get("OPENAI_BASE_URL", "https://sub2api.wodcloud.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY") or env_vars.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[-] 错误: 未检测到 OPENAI_API_KEY 配置", file=sys.stderr)
        return 1

    base_url = base_url.rstrip("/")
    endpoint = f"{base_url}/responses"

    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    data_url = f"data:{mime_type};base64,{image_b64}"

    ratio_size_map = {
        "16:9": "2048x1152",
        "9:16": "1152x2048",
        "1:1": "1024x1024",
        "4:3": "1792x1344",
        "3:4": "1344x1792",
    }
    size = ratio_size_map.get(args.aspect_ratio, "2048x1152")

    target_quality = getattr(args, "quality", "high") or "high"
    bg = getattr(args, "background", "auto") or "auto"
    tool = {
        "type": "image_generation",
        "size": size,
        "quality": target_quality,
        "background": bg,
        "output_format": "png",
        "partial_images": 3,
    }
    user_content = [
        {"type": "input_image", "image_url": data_url},
        {"type": "input_text", "text": prompt},
    ]
    payload = {
        "model": args.model or "gpt-image-2.5-sunburst",
        "input": [{"type": "message", "role": "user", "content": user_content}],
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
        "stream": True,
    }

    print(f"[*] 提交参考图驱动生图 -> {endpoint} (参考底图: {image_path.name}, ratio={args.aspect_ratio}, size={size})")
    start_time = time.time()

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            events = parse_sse_data_events(resp)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"[-] 请求失败 HTTP {e.code}: {err_body}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[-] 请求异常: {e}", file=sys.stderr)
        return 1

    images_b64 = collect_b64_values(events)
    if not images_b64:
        print("[-] 未在响应流中提取到生成的图像数据", file=sys.stderr)
        return 1

    image_bytes = base64.b64decode(images_b64[-1])
    sha256 = calculate_sha256(image_bytes)
    duration = time.time() - start_time
    print(f"[+] 任务生成完成! 实际耗时: {duration:.2f}s, 大小: {len(image_bytes):,} bytes, SHA-256: {sha256}")

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image_bytes)
        print(f"[+] 产物已保存至: {out_path}")

    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    """提交原子生图任务。"""
    if getattr(args, "image", None):
        return execute_reference_generation(args)
    base_url, token = load_config()
    prompt = args.prompt.strip()
    if not prompt:
        print("[-] 错误: --prompt 不能为空", file=sys.stderr)
        return 1

    engine = (args.engine or "codex").lower()
    project_id = args.project_id
    idempotency_key = args.idempotency_key or f"cli-{int(time.time() * 1000)}"
    resolved_model = args.model or ("gpt-image-2.5-sunburst" if engine == "codex" else "gemini-3.1-flash-image")

    target_quality = getattr(args, "quality", "high") or "high"
    background = getattr(args, "background", "auto") or "auto"

    payload: dict[str, Any] = {
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
    artifact_id = task_record.get("artifact_id")

    if args.output and artifact_id:
        out_path = Path(args.output).resolve()
        try:
            download_artifact(
                base_url=base_url,
                token=token,
                artifact_id=artifact_id,
                output_path=out_path,
            )
        except ImageClientError as e:
            print(f"[-] {e}", file=sys.stderr)
            return 1
    elif artifact_id:
        print(f"[*] 产物 Artifact ID: {artifact_id}")
        print(f"[*] 下载地址: {base_url}/artifacts/{artifact_id}/content")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """查询单笔任务状态。"""
    base_url, token = load_config()
    task_id = args.task_id
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
        description="VerdantFlare Image 客户端命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # stats
    sub_stats = subparsers.add_parser("stats", help="查看服务端排队指标统计")
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
    sub_gen = subparsers.add_parser("generate", help="提交生图任务")
    sub_gen.add_argument("--prompt", required=True, help="生图提示词")
    sub_gen.add_argument("--engine", choices=["codex", "gemini"], default="codex", help="底层引擎驱动 (默认 codex)")
    sub_gen.add_argument("--model", default="", help="指定模型覆盖默认值 (codex 默认 gpt-image-2.5-sunburst, gemini 默认 gemini-3.1-flash-image)")
    sub_gen.add_argument("--aspect-ratio", choices=["16:9", "9:16", "1:1", "4:3", "3:4"], default="16:9", help="画幅比例 (默认 16:9)")
    sub_gen.add_argument("--resolution", choices=["2k", "4k"], default="2k", help="分辨率 (默认 2k)")
    sub_gen.add_argument("--quality", choices=["auto", "low", "medium", "high", "xhigh", "max", "hd", "standard"], default="high", help="图像质量 (默认 high)")
    sub_gen.add_argument("--background", choices=["auto", "transparent", "opaque"], default="auto", help="背景模式 (transparent 生成纯透明通道 PNG)")
    sub_gen.add_argument("--project-id", default="default", help="项目标识 (默认 default)")
    sub_gen.add_argument("--idempotency-key", default="", help="客户端幂等业务键")
    sub_gen.add_argument("--wait", action="store_true", help="等待任务生成完成")
    sub_gen.add_argument("--timeout", type=float, default=180.0, help="轮询超时时间 (秒，默认 180)")
    sub_gen.add_argument("--image", "--reference", dest="image", default="", help="参考底图路径 (如 01-character-card.png)")
    sub_gen.add_argument("-o", "--output", default="", help="下载并保存产物的目标路径")
    sub_gen.set_defaults(func=cmd_generate)

    # download
    sub_dl = subparsers.add_parser("download", help="下载指定 Artifact 图像")
    sub_dl.add_argument("--artifact-id", required=True, help="Artifact ID")
    sub_dl.add_argument("-o", "--output", required=True, help="保存目标路径")
    sub_dl.add_argument("--expected-sha256", default="", help="预期校验的 SHA-256")
    sub_dl.set_defaults(func=cmd_download)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
