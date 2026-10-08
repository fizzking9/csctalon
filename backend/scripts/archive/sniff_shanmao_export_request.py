"""
纯嗅探:捕获 shanmao_refund 真实导出的 HTTP 请求 (URL / Method / Headers / Body)
不做任何拦截修改,只 dump 出真实格式 → 之后就知道 monkey patch 要长啥样了!
"""
import sys, os, asyncio, logging, json, subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("ENV_FOR_DYNACONF", "production")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)

from app.services.bms_fetcher import (
    BmsFetcher, BMS_DATA_SOURCES, _storage_state_path
)
from app.services.sync_source_config_service import get_sync_source_config
from app.core.database import SessionLocal

SNAP_DIR = Path("/tmp/shanmao_sniff")
SNAP_DIR.mkdir(parents=True, exist_ok=True)


async def sniff_request(route):
    """捕获 exportRefundApply 请求,打印全部细节,然后放行,不修改!"""
    req = route.request
    url = req.url
    method = req.method
    is_export = "exportRefundApply" in url
    if is_export:
        hdrs = req.headers
        post_bytes = b""
        try:
            post_bytes = await req.body()
        except Exception:
            try:
                post = getattr(req, "post_data", None)
                if isinstance(post, (bytes, bytearray)):
                    post_bytes = bytes(post)
                elif isinstance(post, str):
                    post_bytes = post.encode("utf-8")
            except Exception:
                pass
        sep = "=" * 120
        print(f"\n{sep}", flush=True)
        print(f"🚨🚨🚨 SNIFF EXPORT REQUEST (不修改,仅嗅探) → method={method}", flush=True)
        print(f"  URL: {url}", flush=True)
        print(f"  Method: {method}", flush=True)
        print(f"  Resource Type: {req.resource_type}", flush=True)
        print(f"  All Headers:", flush=True)
        for k, v in hdrs.items():
            print(f"    {k}: {v}", flush=True)
        print(f"  Body length: {len(post_bytes)} bytes", flush=True)
        print(f"  Body raw hex[:512]: {post_bytes[:512].hex()}", flush=True)
        print(f"  Body repr[:1536]: {repr(post_bytes[:1536])}", flush=True)
        try:
            parsed = json.loads(post_bytes.decode("utf-8")) if post_bytes else {}
            print(f"  ✅ Body JSON.parse OK: {json.dumps(parsed, ensure_ascii=False, indent=4)}", flush=True)
        except Exception as e:
            print(f"  ⚠️  Body 不是 JSON: {e}", flush=True)
            try:
                txt = post_bytes.decode("utf-8", "replace")
                print(f"  Body text[:2048]:\n{txt[:2048]}", flush=True)
            except Exception:
                pass
        ct = (hdrs.get("content-type") or hdrs.get("Content-Type") or "").lower()
        if "form" in ct or "urlencoded" in ct:
            try:
                from urllib.parse import parse_qs
                pf = parse_qs(post_bytes.decode("utf-8", errors="replace")) if post_bytes else {}
                print(f"  ✅ Form/urlencoded parse: {json.dumps(pf, ensure_ascii=False, indent=4)}", flush=True)
            except Exception as e:
                print(f"  form parse fail: {e}", flush=True)
        print(f"{sep}\n", flush=True)

        snap = {
            "time": datetime.now().isoformat(),
            "url": url, "method": method, "resource_type": req.resource_type,
            "headers": dict(hdrs),
            "body_hex": post_bytes.hex(),
            "body_repr": repr(post_bytes[:4096]),
            "body_text": post_bytes.decode("utf-8", "replace")[:8192],
            "json_parsed": None,
        }
        try:
            snap["json_parsed"] = json.loads(post_bytes.decode("utf-8")) if post_bytes else {}
        except Exception:
            pass
        (SNAP_DIR / "export_request.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2))
    await route.continue_()


async def sniffer():
    start = datetime(2026, 8, 5, 0, 0, 0)
    end = datetime(2026, 8, 5, 23, 59, 59)
    data_type = "shanmao_refund"
    cfg = dict(BMS_DATA_SOURCES[data_type])
    db = SessionLocal()
    try:
        db_cfg = get_sync_source_config(db, data_type) or {}
    finally:
        db.close()
    cfg["username"] = db_cfg.get("username") or os.environ.get("SHANMAO_USER", "")
    cfg["password"] = db_cfg.get("password") or os.environ.get("SHANMAO_PASS", "")
    if not cfg["username"] or not cfg["password"]:
        print(
            "[FATAL] 缺少杉猫平台凭证: 请先在 sync_source_configs 配置或设置环境变量 SHANMAO_USER/SHANMAO_PASS",
            file=sys.stderr,
        )
        raise SystemExit(2)

    fetcher = BmsFetcher()
    ctx = None
    try:
        await fetcher._init_browser()
        browser = fetcher._browser
        storage_path = _storage_state_path(data_type)
        ctx_kwargs = {"accept_downloads": True, "viewport": {"width": 1920, "height": 1080}}
        if storage_path.exists():
            ctx_kwargs["storage_state"] = str(storage_path)
        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()

        # ==== ★ 核心嗅探:注册 route,不做任何修改,只抓包 dump ====
        await page.route("**/*", sniff_request)

        async def on_response(response):
            if "exportRefundApply" in response.url:
                print(f"\n{'~' * 100}", flush=True)
                print("📥 SNIFF EXPORT RESPONSE", flush=True)
                print(f"  URL: {response.url}", flush=True)
                print(f"  Status: {response.status} {response.status_text}", flush=True)
                print("  Headers:", flush=True)
                for k, v in (response.headers or {}).items():
                    print(f"    {k}: {v}", flush=True)
                try:
                    body_bytes = await response.body()
                except Exception as e:
                    body_bytes = b""
                    print(f"  [read body err] {type(e).__name__}: {e}", flush=True)
                print(f"  Body length: {len(body_bytes)} bytes", flush=True)
                if len(body_bytes) > 0:
                    print(f"  Body[:64] hex: {body_bytes[:64].hex()}", flush=True)
                    print(f"  Body[:64] repr: {repr(body_bytes[:64])}", flush=True)
                    if body_bytes[:1] == b"{":
                        print(f"  Body text[:512]: {body_bytes[:512].decode('utf-8','replace')}", flush=True)
                    (SNAP_DIR / "export_response_body.bin").write_bytes(body_bytes)
                    (SNAP_DIR / "export_response_info.json").write_text(
                        json.dumps({
                            "status": response.status,
                            "headers": dict(response.headers or {}),
                            "len": len(body_bytes),
                            "start_bytes_hex": body_bytes[:32].hex(),
                        }, ensure_ascii=False, indent=2)
                    )
                print(f"{'~' * 100}\n", flush=True)

        page.on("response", on_response)

        menupath = cfg.get("menu_path")
        target_url = cfg.get("url")
        logger = logging.getLogger("sniffer")

        # 1. goto
        logger.info(f"[step1] 打开 {target_url}")
        await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        await page.screenshot(path=str(SNAP_DIR / "1_after_goto.png"), full_page=True)

        # 2. menu
        if menupath:
            logger.info(f"[step2] 走菜单 {menupath}")
            await fetcher._navigate_through_menu(page, menupath)
            await asyncio.sleep(3)
        await page.screenshot(path=str(SNAP_DIR / "2_after_menu.png"), full_page=True)

        # 3. fill time
        start_str = start.strftime(cfg.get("time_format", "%Y-%m-%d %H:%M:%S"))
        end_str = end.strftime(cfg.get("time_format", "%Y-%m-%d %H:%M:%S"))
        logger.info(f"[step3] 填时间 {start_str} ~ {end_str}")
        await fetcher._set_time_range(page, data_type, start_str, end_str, extra=cfg)
        await asyncio.sleep(1)
        await page.screenshot(path=str(SNAP_DIR / "3_after_set_time.png"), full_page=True)

        # 4. 点查询
        logger.info("[step4] 点 查询/搜索")
        for text in ["查询", "搜索", "筛选"]:
            try:
                btn = page.locator(f'button:has-text("{text}")').first
                if await btn.is_enabled(timeout=800):
                    await btn.click()
                    logger.info(f"  clicked 「{text}」")
                    break
            except Exception:
                continue
        await asyncio.sleep(4)
        await page.screenshot(path=str(SNAP_DIR / "4_after_query.png"), full_page=True)

        # 5. 点导出 → 触发请求, sniff_request 会 dump
        logger.info("[step5] 点 导出")
        try:
            async with page.expect_download(timeout=90000) as dl_p:
                clicked = False
                for text in ["导出", "导出Excel", "导出数据"]:
                    try:
                        btn = page.locator(f'button:has-text("{text}")').first
                        if await btn.is_enabled(timeout=800):
                            await btn.click()
                            logger.info(f"  clicked 「{text}」")
                            clicked = True
                            break
                    except Exception:
                        continue
                if not clicked:
                    raise RuntimeError("找不到导出按钮")
            dl = await dl_p.value
            buf = await dl.read()
            logger.info(f"  下载完成 fn={dl.suggested_filename}  len={len(buf)} bytes")
            logger.info(f"  bytes[:32] hex={buf[:32].hex()}  repr={repr(buf[:32])}")
            (SNAP_DIR / f"down_{dl.suggested_filename}").write_bytes(buf)
            try:
                import pandas as pd, io
                if buf[:2] == b"PK":
                    df = pd.read_excel(io.BytesIO(buf), engine="openpyxl", dtype=str)
                    logger.info(f"  ✅ 下载 xlsx rows={len(df)}")
                    if "退费单号" in df.columns:
                        df["_p"] = df["退费单号"].str[:10]
                        logger.info(f"  前缀 TOP 15:\n{df['_p'].value_counts().head(15).to_string()}")
                elif buf[:1] == b"{":
                    logger.info(f"  ⚠️  下载的是 JSON: {buf[:800].decode('utf-8','replace')}")
                else:
                    logger.info(f"  ⚠️  未知格式 bytes[:128]={buf[:128].hex()}")
            except Exception as e:
                logger.info(f"  ⚠️  解析下载文件失败: {type(e).__name__}: {e}")
        except Exception as e:
            logger.info(f"  导出出错 {type(e).__name__}: {e}")

        await page.screenshot(path=str(SNAP_DIR / "5_final.png"), full_page=True)
        logger.info(f"✅ 嗅探完成! 全部 dump: {SNAP_DIR}")
        try:
            print(f"\n{'*' * 80}\n  ls -la:\n", flush=True)
            subprocess.run(["ls", "-la", str(SNAP_DIR)])
        except Exception:
            pass
    finally:
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        try:
            await fetcher._cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(sniffer())
