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
import random
import time
from datetime import datetime, timedelta
from enum import Enum
from io import BytesIO
from pathlib import Path

import httpx
import pandas as pd

from app.core.config import settings
from app.services.bms_fetcher import FetchResult
from app.utils.excel import clean_df_for_excel

logger = logging.getLogger(__name__)

# ai-acc API 路径(相对 base_url)
LOGIN_API_PATH = "/ai-acc/portal/v1/auth/login"
EXPORT_API_PATH = "/ai-acc/cs/v1/assistant/handleRecord/export"

# ai-acc 导出接口限制:单次时间范围不得超过 7 天(正好 7 天允许)
MAX_RANGE_DAYS = 7


class FetchErrorClass(str, Enum):
    """抓取错误分级(A-20260904-002 LOCKED / 工单#2 3.1)。

    TRANSIENT  = 可重试: 5xx / JSON500 / 超时 / 连接重置 / 限流(429/Retry-After)
    PERMANENT  = 不可重试: 401/403(凭证失效) / 400 / 参数错误 / 文件解析错误
    """

    TRANSIENT = "transient"
    PERMANENT = "permanent"


class ClassifiedFetchError(Exception):
    """携带错误分级的抓取异常, 由 _export_chunk 抛出, fetch_one 依据分级重试。"""

    def __init__(self, error_class: FetchErrorClass, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.retry_after = retry_after


def _classify_http_error(resp: httpx.Response) -> ClassifiedFetchError:
    """依据 HTTP 状态码 + 响应体分类错误(区分瞬时/永久)。

    - HTTP >= 500(5xx 服务端瞬时故障)            → TRANSIENT
    - 429 限流(尊重 Retry-After)                  → TRANSIENT
    - JSON 业务错误: 响应含 code 且 >= 500 → TRANSIENT
                    其余 4xx(400/401/403/404...) → PERMANENT
    """
    status = resp.status_code
    text = resp.text[:300] if resp.text else ""
    retry_after: float | None = None
    ra_header = resp.headers.get("retry-after")
    if ra_header:
        try:
            retry_after = float(ra_header)
        except (TypeError, ValueError):
            retry_after = None

    if status >= 500:
        return ClassifiedFetchError(
            FetchErrorClass.TRANSIENT,
            f"导出失败: HTTP {status}: {text}",
            retry_after=retry_after,
        )
    if status == 429:
        return ClassifiedFetchError(
            FetchErrorClass.TRANSIENT,
            f"导出被限流: HTTP 429: {text}",
            retry_after=retry_after,
        )

    # JSON 业务错误(如 ai-acc 常见的 200 + {"code": 500})
    content_type = resp.headers.get("content-type", "")
    if "json" in content_type.lower():
        try:
            jdata = resp.json()
            code = jdata.get("code") if isinstance(jdata, dict) else None
            if isinstance(code, (int, float)) and code >= 500:
                return ClassifiedFetchError(
                    FetchErrorClass.TRANSIENT,
                    f"导出返回 JSON 业务错误(code={code}): {str(jdata)[:300]}",
                    retry_after=retry_after,
                )
            # 其余 JSON(业务拒绝/参数错/凭证错)按永久
            return ClassifiedFetchError(
                FetchErrorClass.PERMANENT,
                f"导出返回 JSON 业务错误(code={code}): {str(jdata)[:300]}",
            )
        except Exception:
            pass

    # 其余 4xx → PERMANENT(凭证失效/参数错)
    return ClassifiedFetchError(
        FetchErrorClass.PERMANENT,
        f"导出失败: HTTP {status}: {text}",
    )


def _backoff_sleep(attempt: int, retry_after: float | None = None) -> None:
    """指数退避 sleep(工单#2 3.1 退避器)。

    base=1s, 指数翻倍, cap 上限; 若上游给了 Retry-After 则至少等那么久(有上限保护)。
    叠加 ±20% 抖动避免多源同时重试风暴。
    """
    base = max(settings.AI_ACC_RETRY_BASE_S, 0.2)
    cap = max(settings.AI_ACC_RETRY_CAP_S, base)
    delay = min(base * (2 ** attempt), cap)
    if retry_after is not None and retry_after > 0:
        # 尊重 Retry-After, 但设上限防死等
        delay = max(delay, min(retry_after, settings.AI_ACC_RETRY_AFTER_CAP_S))
    jitter = 0.8 + 0.4 * random.random()  # ±20%
    time.sleep(delay * jitter)


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
    """导出单个时间分片,返回 xlsx 字节流。

    失败时抛 ClassifiedFetchError(携带 TRANSIENT/PERMANENT 分级), 供上层重试决策。
    """
    params = {
        "createDateStart": start.strftime("%Y-%m-%d %H:%M:%S"),
        "createDateEnd": end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get(f"{base_url}{EXPORT_API_PATH}", params=params, headers=headers)

    # 非 2xx: 分类错误(5xx/429→transient, 其余4xx→permanent)
    if resp.status_code != 200:
        raise _classify_http_error(resp)

    content_type = resp.headers.get("content-type", "")
    if "json" in content_type.lower():
        # 导出返回 JSON 而非文件流 —— 多为业务错误(如 code=500/时间范围超限/无权限)
        raise _classify_http_error(resp)
    return resp.content


async def _fetch_one_chunk(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    data_type: str,
    index: int,
    total: int,
    cs: datetime,
    ce: datetime,
) -> tuple[pd.DataFrame | None, dict | None]:
    """抓取单个时间分片并解析为 DataFrame, 含分片级重试(A-20260904-002 3.1)。

    - TRANSIENT(5xx/JSON500/429/超时/连接重置): 指数退避重试, 上限 AI_ACC_RETRY_MAX 次
    - PERMANENT(401/403/400/参数/解析): 不重试, 立即放弃
    Returns (df, None) 成功 或 (None, failure_dict) 失败
    """
    max_retry = max(settings.AI_ACC_RETRY_MAX, 0)
    failure: dict | None = None

    for attempt in range(max_retry + 1):
        try:
            content = await _export_chunk(client, base_url, token, cs, ce)
            df = pd.read_excel(BytesIO(content), dtype=str, engine="openpyxl")
            logger.info(f"[{data_type}] 分片 {index}/{total}: {cs} ~ {ce} → {len(df)} 行")
            return df, None
        except ClassifiedFetchError as e:
            failure = {
                "start": cs.isoformat(),
                "end": ce.isoformat(),
                "error": str(e),
                "error_class": e.error_class.value,
            }
            if e.error_class == FetchErrorClass.PERMANENT:
                logger.warning(f"[{data_type}] 分片 {index}/{total} 永久失败(不重试): {e}")
                return None, failure
            if attempt >= max_retry:
                logger.error(f"[{data_type}] 分片 {index}/{total} 瞬时失败重试 {max_retry} 次后仍失败: {e}")
                return None, failure
            logger.warning(
                f"[{data_type}] 分片 {index}/{total} 瞬时失败(attempt={attempt + 1}/{max_retry}): {e},退避重试"
            )
            _backoff_sleep(attempt, retry_after=e.retry_after)
        except httpx.ConnectError as e:
            failure = {
                "start": cs.isoformat(),
                "end": ce.isoformat(),
                "error": f"连接失败: {e}",
                "error_class": FetchErrorClass.TRANSIENT.value,
            }
            if attempt >= max_retry:
                return None, failure
            logger.warning(f"[{data_type}] 分片 {index}/{total} 连接失败(attempt={attempt + 1}/{max_retry}): {e}")
            _backoff_sleep(attempt)
        except httpx.TimeoutException as e:
            failure = {
                "start": cs.isoformat(),
                "end": ce.isoformat(),
                "error": f"请求超时: {e}",
                "error_class": FetchErrorClass.TRANSIENT.value,
            }
            if attempt >= max_retry:
                return None, failure
            logger.warning(f"[{data_type}] 分片 {index}/{total} 超时(attempt={attempt + 1}/{max_retry}): {e}")
            _backoff_sleep(attempt)
        except Exception as e:
            # 未知异常(如 xlsx 解析错误)保守按 permanent, 避免死循环重试
            logger.error(f"[{data_type}] 分片 {index}/{total} 解析/未知异常", exc_info=True)
            return None, {
                "start": cs.isoformat(),
                "end": ce.isoformat(),
                "error": f"解析/未知异常: {e}",
                "error_class": FetchErrorClass.PERMANENT.value,
            }
    return None, failure


async def fetch_one(task: dict) -> FetchResult:
    """抓取单个 ai-acc 数据源(task 含 data_type/start/end/config)。

    对超过 7 天的时间范围自动分片抓取并合并为单个 xlsx。
    分片级重试: 单片瞬时失败退避重试, 不整窗丢弃; 部分失败返回 partial_failures。
    按日降级: 整窗(>1天)失败时, 将失败范围按 1 天粒度再分片重试(替代直接放弃整窗)。

    Returns FetchResult(success, file_path, filename, total_rows[, partial_failures])
            全成功 → success=True; 部分成功 → success=True + partial_failures;
            全部失败 → success=False + error/error_class
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

            # ── 第一轮: 按 7 天粒度逐片抓取(含分片级重试) ──
            frames: list[pd.DataFrame] = []
            failed_chunks: list[dict] = []
            for i, (cs, ce) in enumerate(chunks, 1):
                df, failure = await _fetch_one_chunk(client, base_url, token, data_type, i, len(chunks), cs, ce)
                if failure:
                    failed_chunks.append({**failure, "chunk_index": i})
                    continue
                frames.append(df)

            # ── 按日降级: 有 transient 失败片且整窗跨 >1 天时, 将失败范围按 1 天/片重试 ──
            # 目的: 7 天整窗若恰好卡在上游"单次导出范围过大"瞬时故障(9/2 场景),
            #       按日拆分大概率成功 —— 成功的天入库, 失败的天留给 gap_records(3.3)。
            # 仅对 transient 失败降级: permanent(403/401/参数错) 按日重试无意义。
            day_degrade_candidates = [
                fc for fc in failed_chunks
                if fc.get("error_class") == FetchErrorClass.TRANSIENT.value
            ]
            if day_degrade_candidates and (end - start).days >= 1:
                # 把失败片的时间范围取并集再按 1 天切(失败片边界相接,合并简单)
                day_retried: list[str] = []
                still_failed: list[dict] = []
                for fc in failed_chunks:
                    # permanent 失败片不进按日降级, 原样保留
                    if fc.get("error_class") != FetchErrorClass.TRANSIENT.value:
                        still_failed.append(fc)
                        continue
                    f_start = datetime.fromisoformat(fc["start"])
                    f_end = datetime.fromisoformat(fc["end"])
                    day_chunks = _chunk_time_range(f_start, f_end, max_days=1)
                    for dc_index, (dcs, dce) in enumerate(day_chunks, 1):
                        df, failure = await _fetch_one_chunk(
                            client, base_url, token, data_type, dc_index, len(day_chunks), dcs, dce
                        )
                        if failure:
                            still_failed.append({**failure, "chunk_index": f"day{dc_index}"})
                            continue
                        frames.append(df)
                        day_retried.append(f"{dcs:%Y-%m-%d}")
                if day_retried:
                    logger.warning(
                        f"[{data_type}] 按日降级重试成功 {len(day_retried)} 天: {day_retried},"
                        f" 仍失败 {len(still_failed)} 片"
                    )
                failed_chunks = still_failed
                if day_retried:
                    logger.warning(
                        f"[{data_type}] 按日降级重试成功 {len(day_retried)} 天: {day_retried},"
                        f" 仍失败 {len(still_failed)} 片"
                    )
                failed_chunks = still_failed

            # ── 全失败快速返回(避免生成空文件) ──
            if not frames:
                errs = " | ".join(f"{fc.get('error', '')[:120]}" for fc in failed_chunks[:3]) or "全部片失败"
                # 失败分类: 任一 permanent → permanent(重试无意义); 否则 transient
                has_permanent = any(fc.get("error_class") == FetchErrorClass.PERMANENT.value for fc in failed_chunks)
                return FetchResult(
                    data_type=data_type,
                    success=False,
                    error=f"抓取失败(所有分片): {errs}",
                    error_class=FetchErrorClass.PERMANENT.value if has_permanent else FetchErrorClass.TRANSIENT.value,
                )

            # ── 合并 + 去除分片边界(端点 inclusive)可能产生的重复行 ──
            if len(frames) == 1:
                merged = frames[0]
            else:
                merged = pd.concat(frames, ignore_index=True)
                before = len(merged)
                merged = merged.drop_duplicates()
                if len(merged) < before:
                    logger.info(f"[{data_type}] 分片边界去重: {before} → {len(merged)} 行")

            # ── 写入单个 xlsx(openpyxl,单行表头 + 全文本,与原始导出口径一致) ──
            now = datetime.now()
            month_dir = f"{now.year}-{now.month:02d}"
            ts = now.strftime("%Y%m%d%H%M%S")
            filename = f"ai_acc_{data_type}_{ts}.xlsx"
            rel_path = f"{month_dir}/{filename}"
            full_path = Path(settings.UPLOAD_DIR) / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(full_path, engine="openpyxl") as writer:
                clean_df_for_excel(merged).to_excel(writer, index=False, sheet_name="Sheet1")

            # 部分成功提示(有失败片时) / 全成功
            if failed_chunks:
                logger.warning(
                    f"[{data_type}] 部分成功: {len(frames)} 片成功合并入 {filename} "
                    f"({len(merged)} 行), {len(failed_chunks)} 片失败待补: "
                    f"{[fc['start'] + '~' + fc['end'] for fc in failed_chunks[:5]]}"
                )
            else:
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
                partial_failures=failed_chunks or None,
            )

    except httpx.ConnectError as e:
        return FetchResult(data_type=data_type, success=False, error=f"连接失败: {e}",
                           error_class=FetchErrorClass.TRANSIENT.value)
    except httpx.TimeoutException as e:
        return FetchResult(data_type=data_type, success=False, error=f"请求超时: {e}",
                           error_class=FetchErrorClass.TRANSIENT.value)
    except Exception as e:
        logger.error(f"[{data_type}] 抓取异常", exc_info=True)
        return FetchResult(data_type=data_type, success=False, error=f"抓取失败: {e}",
                           error_class=FetchErrorClass.PERMANENT.value)
