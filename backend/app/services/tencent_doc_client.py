"""腾讯文档开放平台 API 客户端

能力:
  1. OAuth2.0: 生成授权链接 / code 换 token / refresh_token 刷新
  2. 分享链接解析: 提取 encodedID → 调用 fileID 转换接口得到 fileID
  3. 表格读取: 查询子表(sheets-info) / 读取区域内容(分页, 遵守 1000行/200列/10000格限制)

文档参考:
  - OAuth2.0:  https://docs.qq.com/open/document/app/oauth2/
  - 表格内容:  https://docs.qq.com/open/document/app/openapi/v3/sheet/get/get_range.html
  - fileID转换: https://docs.qq.com/open/document/app/openapi/v2/file/util/converter.html

说明: 本客户端只做"取数", 不落库。数据清洗/去重/关联工单由 doc_sync_service
复用 complaint_sync 管线完成。所有返回结构均做了防御性解析(上游结构可能因
接口版本变化略有差异), 解析失败抛 DocApiError 并给出可读信息。
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)

# ── 端点 ─────────────────────────────────────────────────────────────
OAUTH_AUTHORIZE_URL = "https://docs.qq.com/oauth/v2/authorize"
OAUTH_TOKEN_URL = "https://docs.qq.com/oauth/v2/token"
OPENAPI_BASE = "https://docs.qq.com/openapi"

# OAuth scope: 读取在线表格 + 文件/文件夹元信息所需权限
SCOPE = "scope.sheet.readonly,scope.sheet,scope.drive,scope.file.editable,scope.folder.readonly"

# 分享链接常见格式: docs.qq.com/s|sheet|doc|form|smartsheet|ppt|mind/{encodedID}
_SHARE_URL_RE = re.compile(
    r"https?://docs\.qq\.com/(?:s|sheet|doc|form|smartsheet|ppt|mind)/([0-9A-Za-z_-]+)",
    re.IGNORECASE,
)
_ENCODED_ID_RE = re.compile(r"^[0-9A-Za-z_-]{6,64}$")

# 读取分页参数(遵守 v3 接口限制: 行<=1000, 列<=200, 单元格<=10000)
_CHUNK_ROWS = 900
_MAX_COLS = 200


class DocApiError(Exception):
    """腾讯文档 API 调用失败(带可读信息)"""


@dataclass
class TencentSheetInfo:
    sheet_id: str
    title: str
    row_count: int = 0
    col_count: int = 0


def _ts_now() -> datetime:
    return datetime.now(timezone.utc)


def _raise_for_ret(payload: dict, endpoint: str) -> None:
    """腾讯文档 openapi 统一返回 {ret, msg, data}; ret!=0 视为失败"""
    ret = payload.get("ret")
    if ret not in (None, 0):
        raise DocApiError(f"{endpoint} 失败: ret={ret}, msg={payload.get('msg') or payload.get('message') or ''}")


# ── OAuth 2.0 ────────────────────────────────────────────────────────
def build_authorize_url(client_id: str, redirect_uri: str, state: str = "") -> str:
    """构造用户授权链接(用户点击 → 腾讯文档授权页 → 扫码/登录同意 → 回跳 redirect_uri?code=...)"""
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
    }
    if state:
        params["state"] = state
    return f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_token(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """授权码 → access_token/refresh_token

    返回: {access_token, token_type, expires_in, refresh_token, user_id(openid), scope}
    """
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    resp = httpx.get(OAUTH_TOKEN_URL, params=params, timeout=30)
    if resp.status_code != 200:
        raise DocApiError(f"换取 access_token 失败: HTTP {resp.status_code} - {resp.text[:300]}")
    data = resp.json()
    if "access_token" not in data:
        raise DocApiError(f"换取 access_token 失败: {data}")
    return data


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """refresh_token → 新 access_token(可能同时返回新 refresh_token)

    返回: {access_token, token_type, expires_in, scope, user_id}
    """
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = httpx.get(OAUTH_TOKEN_URL, params=params, timeout=30)
    if resp.status_code != 200:
        raise DocApiError(f"刷新 access_token 失败: HTTP {resp.status_code} - {resp.text[:300]}")
    data = resp.json()
    if "access_token" not in data:
        raise DocApiError(f"刷新 access_token 失败: {data}")
    return data


def token_expires_at(expires_in: int | None, now: datetime | None = None) -> datetime | None:
    """由 expires_in(秒) 计算过期时间; 预留 60s 缓冲避免边界过期"""
    if not expires_in:
        return None
    return (now or _ts_now()) + timedelta(seconds=max(int(expires_in) - 60, 0))


# ── 分享链接解析 ─────────────────────────────────────────────────────
def parse_share_url(url: str | None) -> str | None:
    """从腾讯文档分享链接提取 encodedID

    支持: https://docs.qq.com/sheet/DREudXRHhXZSezsNXFwZKVD
         https://docs.qq.com/s/xxxxx?tab=BB0000  (分享短链)
         https://docs.qq.com/doc/xxxxx
    返回 encodedID(不含 query);无法解析返回 None。
    """
    if not url:
        return None
    url = url.strip()
    # 兼容带 query 的链接
    path = urlparse(url).path
    m = _SHARE_URL_RE.match(url)
    if not m:
        # 允许域名前有 /s/ 短链等已覆盖, 其他形态直接尝试从 path 尾部取 token
        parts = [p for p in path.split("/") if p]
        if parts:
            tail = parts[-1]
            if _ENCODED_ID_RE.match(tail):
                return tail
        return None
    encoded = m.group(1)
    if not _ENCODED_ID_RE.match(encoded):
        return None
    return encoded


def convert_encoded_id(client_id: str, open_id: str, access_token: str, encoded_id: str) -> str:
    """encodedID → fileID(在线表格 bookID)

    GET /openapi/drive/v2/util/converter?type=encoded_id&value={encoded_id}
    响应形如 {ret:0, data:{fileID/encodedID...}}; 做多形态防御解析。
    """
    url = f"{OPENAPI_BASE}/drive/v2/util/converter"
    headers = {
        "Access-Token": access_token,
        "Open-Id": open_id,
        "Client-Id": client_id,
    }
    resp = httpx.get(url, params={"type": "encoded_id", "value": encoded_id}, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise DocApiError(f"fileID 转换失败: HTTP {resp.status_code} - {resp.text[:300]}")
    data = resp.json()
    _raise_for_ret(data, "fileID转换")
    # data 可能为 {fileID:...} / {encodedID:...} / {id:...} / 直接字符串
    inner = data.get("data") or {}
    if isinstance(inner, str):
        return inner
    if isinstance(inner, dict):
        for key in ("fileID", "file_id", "id", "bookID", "book_id"):
            if inner.get(key):
                return str(inner[key])
    raise DocApiError(f"fileID 转换返回无法识别: {str(data)[:300]}")


# ── 表格读取 ─────────────────────────────────────────────────────────
def _openapi_headers(client_id: str, open_id: str, access_token: str) -> dict:
    return {
        "Access-Token": access_token,
        "Open-Id": open_id,
        "Client-Id": client_id,
    }


def get_sheets_info(client_id: str, open_id: str, access_token: str, file_id: str) -> list[TencentSheetInfo]:
    """查询在线表格的子表列表

    GET /openapi/sheetbook/v2/{bookID}/sheets-info
    响应形如 {ret:0, data:{sheets:[{sheetID,title,rowCount,colCount}]}}(v2)
             {ret:0, data:{gridData/sheets...}}(不同版本)
    防御解析: 遍历 data 中所有可能装 sheet 数组的 key。
    """
    url = f"{OPENAPI_BASE}/sheetbook/v2/{file_id}/sheets-info"
    resp = httpx.get(url, headers=_openapi_headers(client_id, open_id, access_token), timeout=30)
    if resp.status_code != 200:
        raise DocApiError(f"查询子表失败: HTTP {resp.status_code} - {resp.text[:300]}")
    payload = resp.json()
    _raise_for_ret(payload, "查询子表")
    data = payload.get("data") or {}
    sheets_raw: list = []
    if isinstance(data, dict):
        for key in ("sheets", "sheetsInfo", "sheetList", "data"):
            if isinstance(data.get(key), list):
                sheets_raw = data[key]
                break
    elif isinstance(data, list):
        sheets_raw = data

    sheets: list[TencentSheetInfo] = []
    for s in sheets_raw:
        if not isinstance(s, dict):
            continue
        sheet_id = s.get("sheetID") or s.get("sheetId") or s.get("id")
        if not sheet_id:
            continue
        sheets.append(
            TencentSheetInfo(
                sheet_id=str(sheet_id),
                title=str(s.get("title") or s.get("name") or ""),
                row_count=int(s.get("rowCount") or s.get("row_count") or 0),
                col_count=int(s.get("colCount") or s.get("col_count") or 0),
            )
        )
    if not sheets:
        raise DocApiError(f"查询子表未解析到任何 sheet: {str(payload)[:300]}")
    return sheets


def _cell_to_str(cell: dict | None) -> str:
    """gridData 单元格 → 字符串(cellValue 内 text/number/bool 等取原始展示值)"""
    if not isinstance(cell, dict):
        return ""
    cv = cell.get("cellValue")
    if not isinstance(cv, dict):
        return ""
    for key in ("text", "number", "bool", "date", "rawText"):
        v = cv.get(key)
        if v is not None and v != "":
            return str(v)
    # formula 等: 有值就转字符串
    for key in ("formula", "result"):
        v = cv.get(key)
        if v is not None and v != "":
            return str(v)
    return ""


def read_range(
    client_id: str,
    open_id: str,
    access_token: str,
    file_id: str,
    sheet_id: str,
    range_a1: str,
) -> list[list[str]]:
    """读取表格区域内容(单次)

    GET /openapi/spreadsheet/v3/files/{fileId}/{sheetId}/{range}
    响应 gridData.rows[].values[] → list[list[str]](空单元格为 "")
    """
    url = f"{OPENAPI_BASE}/spreadsheet/v3/files/{file_id}/{sheet_id}/{range_a1}"
    resp = httpx.get(url, headers=_openapi_headers(client_id, open_id, access_token), timeout=60)
    if resp.status_code != 200:
        raise DocApiError(f"读取区域 {range_a1} 失败: HTTP {resp.status_code} - {resp.text[:300]}")
    payload = resp.json()
    _raise_for_ret(payload, f"读取区域 {range_a1}")
    data = payload.get("data") or {}
    grid = data.get("gridData") or {}
    rows_raw = grid.get("rows") or []
    rows: list[list[str]] = []
    for r in rows_raw:
        if not isinstance(r, dict):
            continue
        values = r.get("values") or []
        rows.append([_cell_to_str(v) for v in values])
    return rows


def _col_letter(idx: int) -> str:
    """0-based 列索引 → Excel 列字母(A, B, ..., Z, AA, ...)"""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def read_sheet_rows(
    client_id: str,
    open_id: str,
    access_token: str,
    file_id: str,
    sheet: TencentSheetInfo,
) -> list[list[str]]:
    """读取整张子表(分页, 遵守行<=1000/列<=200/格<=10000 限制)

    返回二维数组(含表头行); 空行过滤。列数取 sheets-info 的 colCount,
    缺失时按实际数据探测(首块读 200 列, 超过再按数据长度扩展)。
    """
    col_count = sheet.col_count if 0 < sheet.col_count <= _MAX_COLS else _MAX_COLS
    row_count = sheet.row_count
    all_rows: list[list[str]] = []
    start_row = 1  # A1 表示法从 1 开始
    # 先探测表头宽度: 读首块, 之后按实际最大列宽扩展(避免列数不足截断)
    while True:
        end_row = min(start_row + _CHUNK_ROWS - 1, row_count or start_row + _CHUNK_ROWS - 1)
        range_a1 = f"A{start_row}:{_col_letter(col_count)}{end_row}"
        chunk = read_range(client_id, open_id, access_token, file_id, sheet.sheet_id, range_a1)
        if not chunk:
            break
        all_rows.extend(chunk)
        if row_count:
            if end_row >= row_count:
                break
        elif len(chunk) < _CHUNK_ROWS:
            break  # 未配置行数时, 数据不足一块视为读完
        start_row = end_row + 1

    # 计算有效列数(取所有行最大非空宽度), 裁剪列
    max_cols = 0
    for row in all_rows:
        for i, v in enumerate(row):
            if v.strip():
                max_cols = max(max_cols, i + 1)
    if max_cols:
        all_rows = [row[:max_cols] for row in all_rows]

    # 过滤全空行
    return [row for row in all_rows if any(v.strip() for v in row)]


def rows_to_records(rows: list[list[str]]) -> list[dict]:
    """二维数组(首行为表头) → [{列名: 值}] 列表(空表头列丢弃)

    表头去首尾空白; 重复表头加后缀(同列名保留第一条, 后续空值列丢弃)。
    """
    if not rows:
        return []
    headers = rows[0]
    # 表头列映射: 空表头列不参与(腾讯文档可能含空列)
    seen: dict[str, int] = {}
    col_map: dict[int, str] = {}
    for idx, h in enumerate(headers):
        name = h.strip()
        if not name:
            continue
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        col_map[idx] = name
    records: list[dict] = []
    for row in rows[1:]:
        rec = {}
        for idx, v in enumerate(row):
            name = col_map.get(idx)
            if name is None:
                continue
            val = v.strip()
            if val:
                rec[name] = val
        if rec:
            records.append(rec)
    return records


def fetch_doc_meta(client_id: str, open_id: str, access_token: str, file_id: str) -> dict:
    """查询文档元信息(文件名等, 用于 source_file 展示)

    GET /openapi/drive/v2/files/{fileID}/metadata
    解析失败不致命(返回空 dict)。
    """
    url = f"{OPENAPI_BASE}/drive/v2/files/{file_id}/metadata"
    try:
        resp = httpx.get(url, headers=_openapi_headers(client_id, open_id, access_token), timeout=30)
        if resp.status_code != 200:
            return {}
        payload = resp.json()
        _raise_for_ret(payload, "查询文档元信息")
        data = payload.get("data") or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[tencent_doc] 查询文档元信息失败(忽略): {e}")
        return {}
