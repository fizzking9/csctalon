# -*- coding: utf-8 -*-
"""设备档案 SN 补全服务（工单 #293）

## 设计说明（Design Rationale）

- **替谁回答什么问题**：退款/投诉/接待等分析依赖设备维表属性（设备类型、当前套餐、
  商户、激活时间），此前 80% 设备这些字段为空 → 分析页大量「未填」。
- **为什么是 SN 精确查询**：BMS 导出接口被 10 万行截断且时间筛选失效（实测），全量
  翻页需 35 小时（100 条/页 × 6s）；`findPageList?sn=` 单次 0.6s、字段完整、无导出
  限流，按需只补缺字段设备（56 万台存量 + 每日少量增量），10 并发约 8-10 SN/s。
- **口径**：设备档案是 SN 维表特性（设备类型/套餐/商户/激活时间是设备本身的属性），
  退款、投诉、接待只是消费这些维度的业务场景。因此扫描范围 = devices 表所有缺字段
  设备，与业务无关。
- **取舍（不做什么）**：
  - 不覆盖已有值：只填空值（COALESCE），避免覆盖日批/人工修正的数据 → 幂等可重复跑；
  - 不为它新建任务清单表：「devices 缺字段状态」即天然任务清单（SQL 可精确枚举、
    填上即出队），量级（日增数千）不值得建表；
  - 不替代 device_profile 日批导出：日批负责新设备建档 + ICCID/批次等上下文，
    本服务是「档案兜底」，两者互补。
- **工单**：#293。

## 数据流

    调度器每日档(4:00) / scripts CLI(manual)
      → scan_missing_sns(): SQL 扫 devices 缺字段 SN（上限 limit, 仅未探针或探针超30天）
      → BmsFetcher.fetch_one(session_hook=...) 登录 BMS（复用 storage_state/OCR）
      → hook: 捕获 x-token/cookies → httpx 并发 findPageList?sn=xxx
      → apply_item(): UPDATE devices 只填空值 + profile_probe_at 打标; miss 也打标(#293)
      → 记 bms_sync_logs(data_type='device_profile_enrich') 可观测
      → 更新数>0 时失效看板缓存 pattern（缓存铁律）
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# BMS 设备列表接口（登录后页面同源调用, 带页面 x-token）
_FIND_PAGE_LIST_PATH = "/bms-portal/device/findPageList"

# 调度档默认每小时最多补多少个(4:00 档, 预计 ~40 分钟)
DAILY_LIMIT_DEFAULT = 20000
# 并发数(实测 10 并发无 BMS 限流, 0.6s/req → ~8-10 SN/s)
CONCURRENCY_DEFAULT = 10

# 单飞锁: 防止调度档与手动 CLI 同时运行（两个浏览器会话会竞争 storage_state 写入）
_run_lock = asyncio.Lock()


def scan_missing_sns(db: Session, limit: int) -> list[str]:
    """扫描 devices 表缺任一档案字段的 SN（幂等核心：缺什么补什么，已完整自动出队）。

    #293 探针口径: 只看「未探针 OR 探针超 30 天」——永久 miss(BMS 查无此 SN)与
    hit 但无套餐等设备永远无法出队, 不打探针会在每批堆积白查(2026-10-10 实测:
    批1 hit 11018 仅 1073 台退出缺失集, 45% 为永久 miss); 30 天复探窗口给
    后续新买套餐设备留刷新口。

    #293 倒排: 按「最近活跃」倒序(活跃=充值 device_combos.created_at_raw 或
    完整月流量 device_flow_usage.stat_month, 与分品类看板活跃口径一致)——
    最近活跃的 SN 先探, 管理分析尽早可用; 无活动记录的老设备 NULLS LAST 殿后。
    子查询聚合实测 ~1.5s/批(生产 70 万设备), 每批只跑一次可忽略。
    """
    q = text("""
        SELECT d.sn FROM devices d
        LEFT JOIN (
          SELECT device_sn, MAX(ts) AS last_active FROM (
            SELECT device_sn, MAX(created_at_raw) AS ts FROM device_combos
            WHERE device_sn IS NOT NULL GROUP BY device_sn
            UNION ALL
            SELECT device_sn, MAX(TO_DATE(stat_month, 'YYYY-MM')) AS ts FROM device_flow_usage
            WHERE device_sn IS NOT NULL AND month_complete GROUP BY device_sn
          ) a GROUP BY device_sn
        ) act ON act.device_sn = d.sn
        WHERE ((d.device_type IS NULL OR d.device_type = '')
           OR (d.current_combo_name IS NULL OR d.current_combo_name = '')
           OR (d.merchant_name IS NULL OR d.merchant_name = '')
           OR d.active_time IS NULL)
          AND (d.profile_probe_at IS NULL OR d.profile_probe_at < now() - interval '30 days')
        ORDER BY act.last_active DESC NULLS LAST
        LIMIT :lim
    """)
    return [r[0] for r in db.execute(q, {"lim": limit}).fetchall()]


def apply_item(db: Session, item: dict) -> None:
    """单条 BMS 设备数据 → devices 只填空值更新。

    - JSON 字段映射与 data_parser 的 device_profile CSV 口径一致:
      deviceTypeOutterName=设备类型, businessName=所属商户, deviceComboName=当前套餐,
      activeDate=激活时间, serveValidDate=服务有效期, versionName=应用版本。
    - COALESCE(NULLIF(...)): 现值非空则保持, 空才落新值 → 不覆盖日批/人工数据。
    - 时间: BMS activeDate 为北京时间字符串; devices.active_time 为 timestamptz。
      实测(#293 事务实验) CAST 后 COALESCE 写入路径按 Asia/Shanghai 解释, 与
      device_profile 日批导入一致 —— 已验证勿改。
    """
    db.execute(text("""
        UPDATE devices SET
            device_type         = COALESCE(NULLIF(device_type,''), :device_type),
            merchant_name       = COALESCE(NULLIF(merchant_name,''), :merchant_name),
            main_merchant_name  = COALESCE(NULLIF(main_merchant_name,''), :main_merchant_name),
            active_flag         = COALESCE(NULLIF(active_flag,''), :active_flag),
            active_time         = COALESCE(active_time, CAST(:active_time AS timestamp)),
            service_valid_until = COALESCE(service_valid_until, CAST(:svc AS timestamp)),
            current_combo_name  = COALESCE(NULLIF(current_combo_name,''), :combo),
            start_iccid         = COALESCE(NULLIF(start_iccid,''), :s_iccid),
            virtual_iccid       = COALESCE(NULLIF(virtual_iccid,''), :v_iccid),
            app_version         = COALESCE(NULLIF(app_version,''), :app_ver),
            region              = COALESCE(NULLIF(region,''), :region),
            profile_probe_at    = now()
        WHERE sn = :sn
    """), {
        "sn": (item.get("sn") or "").strip(),
        "device_type": (item.get("deviceTypeOutterName") or item.get("deviceTypeInnerName") or "").strip() or None,
        "merchant_name": (item.get("businessName") or "").strip() or None,
        "main_merchant_name": (item.get("rootBusinessName") or "").strip() or None,
        "active_flag": "已激活" if item.get("isActive") else "未激活",
        "active_time": item.get("activeDate") or None,
        "svc": item.get("serveValidDate") or None,
        "combo": (item.get("deviceComboName") or "").strip() or None,
        "s_iccid": (item.get("seedCardIccid") or "").strip() or None,
        "v_iccid": (item.get("virtualCardIccid") or "").strip() or None,
        "app_ver": (item.get("versionName") or "").strip() or None,
        "region": (item.get("region") or "").strip() or None,
    })


# findPageList 无关参数占位（与页面请求一致, 避免后端对缺参的兼容分支）
_EMPTY_PARAMS = {
    "virtualCardIsp": "", "isActive": "", "deviceTypeId": "", "seedCardIccid": "",
    "virtualCardIccid": "", "speedLimitSwitch": "", "versionName": "",
    "importBatchId": "", "bindStartTime": "", "bindEndTime": "",
}


def mark_probe_miss(db: Session, sn: str) -> None:
    """miss(BMS 查无此 SN)也打探针(#293)——否则永久 miss 在每批堆积白查直至停滞。"""
    db.execute(text("UPDATE devices SET profile_probe_at = now() WHERE sn = :sn"), {"sn": sn})


def backfill_from_business(db: Session) -> dict:
    """从 device_combos + device_flow_usage 反填 devices 缺字段（#293 采集后自动回写）。

    BMS 设备列表接口(findPageList)查不到的 SN（约45%，多为14位SN不在BMS设备库），
    但 device_combos（充值订单，每日采集同步）和 device_flow_usage（流量使用，按需采集）
    里有完整的商户名(店铺名)、套餐名、设备类型、激活时间。本函数将这些业务数据
    反填到 devices 主表，只填空值（COALESCE + NULLIF 幂等，可重复跑）。

    触发时机：bms_sync post-sync 钩子中，device_combo 同步成功后自动调用。
    """
    result = db.execute(text("""
        WITH latest_combo AS (
          SELECT DISTINCT ON (device_sn)
            device_sn, merchant_name, package_name, created_at_raw
          FROM device_combos WHERE device_sn IS NOT NULL
          ORDER BY device_sn, created_at_raw DESC
        ),
        earliest_combo AS (
          SELECT DISTINCT ON (device_sn)
            device_sn, created_at_raw AS first_recharge
          FROM device_combos WHERE device_sn IS NOT NULL
          ORDER BY device_sn, created_at_raw ASC
        ),
        flow_info AS (
          SELECT DISTINCT ON (device_sn)
            device_sn, device_type_name, device_combo_name, merchant_name AS flow_merchant
          FROM device_flow_usage
          WHERE device_sn IS NOT NULL AND device_type_name IS NOT NULL AND device_type_name != ''
          ORDER BY device_sn, stat_month DESC
        ),
        combo_info AS (
          SELECT
            lc.device_sn,
            lc.merchant_name, lc.package_name,
            ec.first_recharge,
            fi.device_type_name, fi.device_combo_name, fi.flow_merchant
          FROM latest_combo lc
          LEFT JOIN earliest_combo ec ON ec.device_sn = lc.device_sn
          LEFT JOIN flow_info fi ON fi.device_sn = lc.device_sn
        )
        UPDATE devices d
        SET
          merchant_name      = COALESCE(NULLIF(d.merchant_name,''), ci.merchant_name, ci.flow_merchant),
          current_combo_name = COALESCE(NULLIF(d.current_combo_name,''), ci.package_name, ci.device_combo_name),
          device_type        = COALESCE(NULLIF(d.device_type,''), ci.device_type_name),
          active_time        = COALESCE(d.active_time, ci.first_recharge)
        FROM combo_info ci
        WHERE d.sn = ci.device_sn
        AND (d.device_type IS NULL OR d.device_type = ''
          OR d.current_combo_name IS NULL OR d.current_combo_name = ''
          OR d.merchant_name IS NULL OR d.merchant_name = ''
          OR d.active_time IS NULL)
    """))
    db.commit()
    updated = result.rowcount
    logger.info("[档案反填] 从 device_combos/flow_usage 反填 devices 缺字段: %s 行", updated)
    return {"backfilled": updated}


def _make_session_hook(sns: list[str], concurrency: int, stats: dict):
    """构造 session_hook：登录后抓 token/cookies → 并发 SN 查询 → 只填空值入库。"""

    async def hook(page) -> dict:
        # hook 闭包内独立导入(避免模块级循环依赖; SessionLocal 每次调用时绑定当前配置)
        from app.core.database import SessionLocal

        # 1) 捕获页面请求头里的 x-token（BMS 前端每次请求都带）
        xtoken: dict = {}

        def on_request(req):
            if "x-token" in req.headers:
                xtoken["value"] = req.headers["x-token"]

        page.on("request", on_request)
        # 点一次查询让前端发出带 token 的请求
        try:
            btn = await page.query_selector('button:has-text("搜索")') or await page.query_selector('button:has-text("查询")')
            if btn:
                await btn.click()
                await asyncio.sleep(3)
        except Exception:  # noqa: BLE001 按钮找不到不致命——storage_state 登录态下页面可能已带请求
            pass

        cookies = {c["name"]: c["value"] for c in await page.context.cookies()}
        base_url = page.url.split("/bms/")[0]
        list_url = f"{base_url}{_FIND_PAGE_LIST_PATH}"
        headers = {
            "x-token": xtoken.get("value", ""),
            "accept": "application/json, text/plain, */*",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/153.0 Safari/537.36",
            "referer": page.url,
        }

        queue: asyncio.Queue = asyncio.Queue()
        for s in sns:
            queue.put_nowait(s)
        pause = asyncio.Event()  # 限流全局暂停（BMS 导出接口有 65s 冷却, 列表接口未见限流, 保险起见保留退避）

        # 2) 单 SN 精确查询（rows=1）→ 命中即入库；每 worker 独立 DB 会话（短事务）
        async def worker(cli: httpx.AsyncClient, db: Session):
            while not queue.empty():
                sn = queue.get_nowait()
                if pause.is_set():
                    await pause.wait()
                try:
                    r = await cli.get(
                        list_url,
                        params={"page": 1, "rows": 1, "sn": sn, **_EMPTY_PARAMS},
                        headers=headers,
                    )
                    stats["queried"] += 1
                    if r.status_code != 200:
                        stats["failed"] += 1
                        pause.set()
                        await asyncio.sleep(65)  # BMS 限流冷却
                        pause.clear()
                        queue.put_nowait(sn)  # 重新排队, 下轮再试
                        continue
                    items = ((r.json() or {}).get("data") or {}).get("items") or []
                    if items:
                        stats["hit"] += 1
                        apply_item(db, items[0])
                        stats["updated"] += 1
                    else:
                        stats["miss"] += 1
                        mark_probe_miss(db, sn)
                    # 每 200 个提交一批(短事务, 避免长事务锁表)
                    if stats["queried"] % 200 == 0:
                        db.commit()
                    if stats["queried"] % 500 == 0:
                        logger.info(
                            "[档案补全] 进度 %s/%s hit=%s miss=%s updated=%s failed=%s",
                            stats["queried"], len(sns), stats["hit"],
                            stats["miss"], stats["updated"], stats["failed"],
                        )
                except Exception:  # noqa: BLE001 单条失败不中断整体
                    stats["failed"] += 1
                    db.rollback()
                    await asyncio.sleep(1)
                finally:
                    queue.task_done()

        t0 = time.monotonic()
        async with httpx.AsyncClient(cookies=cookies, timeout=30, follow_redirects=True) as cli:
            dbs = [SessionLocal() for _ in range(concurrency)]
            try:
                await asyncio.gather(*[worker(cli, d) for d in dbs])
                for d in dbs:
                    d.commit()
            finally:
                for d in dbs:
                    d.close()
        stats["elapsed_sec"] = round(time.monotonic() - t0, 1)
        logger.info("[档案补全] 完成: %s", stats)
        return dict(stats)

    return hook


def _invalidate_dashboard_cache(updated: int) -> None:
    """回填改变了 devices 维表属性 → 失效消费这些维度的看板缓存（缓存铁律：写后失效）。

    仅在有实际更新时失效; pattern 与 cache_warmer.SCOPE_PATTERNS 对齐
    （refund/complaint/op/operator/today 覆盖退款·投诉·运营视图的设备属性维度）。
    """
    if updated <= 0:
        return
    try:
        from app.core.redis_client import cache_invalidate_pattern

        n = sum(
            cache_invalidate_pattern(p)
            for p in ["refund:*", "complaint:*", "op:*", "operator:*", "today:*"]
        )
        logger.info("[档案补全] 已失效看板缓存 %s 个 key", n)
    except Exception as e:  # noqa: BLE001 Redis 不可用不阻塞回填结果
        logger.warning("[档案补全] 缓存失效失败(下次 TTL 自然过期): %s", e)


def _log_sync(db: Session, trigger_type: str, stats: dict, error: Optional[str]) -> None:
    """写 bms_sync_logs 可观测（data_type='device_profile_enrich'）。

    不会影响缺口清点(gap_queue 只在 sync_all 收尾按 results 记账, 本服务直写日志表,
    且该 data_type 不在同步源清单)。
    """
    from app.models.bms_sync_log import BmsSyncLog

    now = datetime.now(timezone(timedelta(hours=8)))
    log = BmsSyncLog(
        data_type="device_profile_enrich",
        start_time=now,
        end_time=now,
        status="failed" if error else "success",
        trigger_type=trigger_type,
        triggered_by="scheduler" if trigger_type == "scheduled" else "cli",
        total_rows=stats.get("queried"),
        inserted_rows=stats.get("updated"),  # 语义: 本次实际补全的行数
        updated_rows=stats.get("hit"),
        failed_rows=stats.get("failed"),
        error=(error or "")[:2000] or None,
        finished_at=now,
    )
    db.add(log)
    db.commit()


async def run_enrich(
    db: Session,
    *,
    limit: int = DAILY_LIMIT_DEFAULT,
    concurrency: int = CONCURRENCY_DEFAULT,
    trigger_type: str = "scheduled",
    timeout_sec: int = 7200,
) -> dict:
    """主入口：扫描缺字段 SN → 登录 BMS → SN 查询补全 → 记日志。

    timeout_sec 是整个 SN 补全的外层预算(含登录): 调度档 2 万 SN 约 25-40 分钟;
    CLI 存量清零(数十万 SN)请传更大值(如 86400)。
    返回 {skipped, target, queried, hit, miss, updated, failed, elapsed_sec}。
    并发运行保护：_run_lock 非阻塞获取, 已有实例在跑则返回 skipped。
    """
    if _run_lock.locked():
        logger.warning("[档案补全] 已有实例运行中, 本次跳过")
        return {"skipped": True, "reason": "another enrich run in progress"}

    async with _run_lock:
        # 0) 扫描目标（独立会话, 避免长事务持有连接）
        from app.core.database import SessionLocal

        scan_db = SessionLocal()
        try:
            sns = scan_missing_sns(scan_db, limit)
        finally:
            scan_db.close()

        stats: dict[str, Any] = {
            "skipped": False, "target": len(sns),
            "queried": 0, "hit": 0, "miss": 0, "updated": 0, "failed": 0,
        }
        if not sns:
            logger.info("[档案补全] 无缺字段设备, 无需回填")
            _log_sync(db, trigger_type, stats, None)
            return stats

        # 1) 登录 BMS（复用 device_profile 源的凭证与登录态）并在登录后执行 SN 补全
        from app.services.bms_fetcher._fetch import BmsFetcher
        from app.services.sync_source_config_service import get_sync_source_config

        config_db = SessionLocal()
        try:
            config = get_sync_source_config(config_db, "device_profile")
        finally:
            config_db.close()
        if not config or not config.get("base_url"):
            stats["error"] = "device_profile 源未配置"
            _log_sync(db, trigger_type, stats, stats["error"])
            return stats

        # 占位窗口(仅用于通过登录流程, 不参与 SN 查询)
        tz = timezone(timedelta(hours=8))
        w_start = datetime(2026, 8, 1, 0, 0, 0, tzinfo=tz)
        w_end = datetime(2026, 8, 31, 23, 59, 59, tzinfo=tz)

        error: Optional[str] = None
        async with BmsFetcher() as fetcher:
            result = await fetcher.fetch_one(
                "device_profile", w_start, w_end, config,
                timeout=timeout_sec, session_hook=_make_session_hook(sns, concurrency, stats),
            )
            if not result.success:
                error = result.error or "session_hook 执行失败"
                stats["error"] = error

        # 2) 收尾：日志 + 缓存失效
        _log_sync(db, trigger_type, stats, error)
        _invalidate_dashboard_cache(stats["updated"])
        return stats
