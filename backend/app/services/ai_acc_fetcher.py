"""ai-acc 智能助手处理记录 HTTP 抓取器(非 Playwright)

与 BMS fetcher(Playwright 浏览器自动化)不同,ai-acc 平台提供 REST API,
可直接用 httpx 走 HTTP 请求完成登录 + 导出,无需浏览器:

1. 登录: POST {base_url}/ai-acc/portal/v1/auth/login
   body: {"username":"...","password":"..."}
   response: {"token":"...","userId":"...","username":"..."}  # token 在顶层
   登录同时下发 session cookie(httpx 共享 client 自动保持)

2. 导出: GET {base_url}/ai-acc/cs/v1/assistant/handleRecord/export
   params: createDateStart=YYYY-MM-DD HH:mm:ss & createDateEnd=YYYY-MM-DD HH:mm:ss
   auth: Bearer token + session cookie
   response: xlsx 文件流(application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)

接口限制: 单次导出时间范围不得超过 7 天。本抓取器对超过 7 天的区间自动分片:
  - 切成 <=7 天的连续子区间(边界相接),分别导出
  - pandas 合并为单个 xlsx(读用 dtype=str,与 data_parser 解析口径一致)
  - drop_duplicates() 去除分片边界(inclusive 端点)可能产生的重复行

时间范围由 bms_sync.calculate_time_range 计算(当天 00:00 ~ now 或增量)。
"""

import logging
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pandas as pd

from app.core.config import settings
from app.services.bms_fetcher import FetchResult

logger = logging.getLogger(__name__)

# ai-acc API 路径(相对 base_url)
LOGIN_API_PATH = "/ai-acc/portal/v1/auth/login"
EXPORT_API_PATH = "/ai-acc/cs/v1/assistant/handleRecord/export"

# ai-acc 导出接口限制:单次时间范围不得超过 7 天(正好 7 天允许)
MAX_RANGE_DAYS = 7


def _chunk_time_range(
    start: datetime,
    end: datetime,
    max_days: int = MAX_RANGE_DAYS,
) -> list[tuple[datetime, datetime]]:
    """将 [start, end] 切成若干 <= max_days 的连续子区间(边界相接)。

    边界相接(chunk_i.end == chunk_{i+1}.start)且 API 端点 inclusive,
    可能导致边界秒的记录在两片中都出现 —— 合并时用 drop_duplicates() 去重。
    """
    if end <= start:
        return []
    chunks: list[tuple[datetime, datetime]] = []
    cur = start
    step = timedelta(days=max_days)
    while cur < end:
        chunk_end = min(cur + step, end)
        chunks.append((cur, chunk_end))
        cur = chunk_end
    return chunks


async def _login(client: httpx.AsyncClient, base_url: str, username: str, password: str) -> str:
    """登录返回 token(ai-acc token 在顶层,兼容嵌套 data.token 的服务)"""
    resp = await client.post(
        f"{base_url}{LOGIN_API_PATH}",
        json={"username": username, "password": password},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败: HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    token = data.get("token") or (data.get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"登录失败: 响应无 token 字段: {str(data)[:200]}")
    return token


async def _export_chunk(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    start: datetime,
    end: datetime,
) -> bytes:
    """导出单个时间分片,返回 xlsx 字节流;非文件流(错误/空)时抛异常"""
    params = {
        "createDateStart": start.strftime("%Y-%m-%d %H:%M:%S"),
        "createDateEnd": end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get(f"{base_url}{EXPORT_API_PATH}", params=params, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"导出失败: HTTP {resp.status_code}: {resp.text[:200]}")

    content_type = resp.headers.get("content-type", "")
    if "json" in content_type.lower():
        # 导出返回 JSON 而非文件流 —— 多为业务错误(如时间范围超限/无权限/空数据)
        raise RuntimeError(f"导出返回 JSON 而非文件流: {resp.text[:300]}")
    return resp.content


async def fetch_one(task: dict) -> FetchResult:
    """抓取单个 ai-acc 数据源(task 含 data_type/start/end/config)。

    对超过 7 天的时间范围自动分片抓取并合并为单个 xlsx。
    Returns FetchResult(success, file_path, filename, total_rows) 或 (success=False, error)
    """
    data_type = task["data_type"]
    config = task["config"]
    start = task["start"]
    end = task["end"]

    base_url = (config.get("base_url") or "").rstrip("/")
    username = config.get("username") or ""
    password = config.get("password") or ""

    if not base_url:
        return FetchResult(data_type=data_type, success=False, error="base_url 未配置")
    if not username or not password:
        return FetchResult(data_type=data_type, success=False, error="账号/密码未配置(去同步设置页填写)")

    # 时间范围分片(ai-acc 单次导出不得超过 7 天)
    chunks = _chunk_time_range(start, end, MAX_RANGE_DAYS)
    if not chunks:
        return FetchResult(data_type=data_type, success=False, error=f"无效时间范围: {start} ~ {end}")
    if len(chunks) > 1:
        logger.info(f"[{data_type}] 时间范围 {start} ~ {end} 超过 7 天单次上限,分 {len(chunks)} 片抓取")
    else:
        logger.info(f"[{data_type}] 抓取 {start} ~ {end}")

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            token = await _login(client, base_url, username, password)
            logger.info(f"[{data_type}] 登录成功,token len={len(token)}")

            # 逐片导出 → DataFrame(dtype=str,与 data_parser 解析口径一致)
            frames: list[pd.DataFrame] = []
            for i, (cs, ce) in enumerate(chunks, 1):
                content = await _export_chunk(client, base_url, token, cs, ce)
                df = pd.read_excel(BytesIO(content), dtype=str, engine="openpyxl")
                logger.info(f"[{data_type}] 分片 {i}/{len(chunks)}: {cs} ~ {ce} → {len(df)} 行")
                frames.append(df)

            # 合并 + 去除分片边界(端点 inclusive)可能产生的重复行
            if len(frames) == 1:
                merged = frames[0]
            else:
                merged = pd.concat(frames, ignore_index=True)
                before = len(merged)
                merged = merged.drop_duplicates()
                if len(merged) < before:
                    logger.info(f"[{data_type}] 分片边界去重: {before} → {len(merged)} 行")

            # 写入单个 xlsx(openpyxl,单行表头 + 全文本,与原始导出口径一致)
            now = datetime.now()
            month_dir = f"{now.year}-{now.month:02d}"
            ts = now.strftime("%Y%m%d%H%M%S")
            filename = f"ai_acc_{data_type}_{ts}.xlsx"
            rel_path = f"{month_dir}/{filename}"
            full_path = Path(settings.UPLOAD_DIR) / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(full_path, engine="openpyxl") as writer:
                merged.to_excel(writer, index=False, sheet_name="Sheet1")

            logger.info(
                f"[{data_type}] 抓取成功: {filename} ({len(merged)} 行, {len(chunks)} 片合并, "
                f"{full_path.stat().st_size} bytes)"
            )

            return FetchResult(
                data_type=data_type,
                success=True,
                file_path=rel_path,
                filename=filename,
                total_rows=len(merged),
            )

    except httpx.ConnectError as e:
        return FetchResult(data_type=data_type, success=False, error=f"连接失败: {e}")
    except httpx.TimeoutException as e:
        return FetchResult(data_type=data_type, success=False, error=f"请求超时: {e}")
    except Exception as e:
        logger.error(f"[{data_type}] 抓取异常", exc_info=True)
        return FetchResult(data_type=data_type, success=False, error=f"抓取失败: {e}")
