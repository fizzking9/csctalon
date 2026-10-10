# 客服中心管理系统 后端镜像
FROM python:3.11-slim

WORKDIR /app

# 替换 apt 为清华源(加速国内下载)
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null \
    || true

# 系统依赖(libpq 编译所需 + libzbar 用于发票二维码解码)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    # 发票二维码本地解码(规划设计 §4.5, pyzbar 依赖)
    libzbar0 \
    # Playwright/Chromium 依赖
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpangocairo-1.0-0 libpango-1.0-0 libcairo2 \
    # 虚拟显示服务(工单 #45): 共享文档页面内扫码登录需在有头浏览器中渲染,
    # 容器无物理显示器 → 用 Xvfb 提供虚拟 DISPLAY。xauth 供 xvfb-run 使用。
    xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

# pip 使用清华源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

COPY pyproject.toml ./
COPY app ./app
# 依赖单一来源 = pyproject.toml (#183): 安装本项目即解析全部依赖,
# 杜绝 Dockerfile 硬编码清单与 pyproject 双定义漂移。
RUN pip install --no-cache-dir .

# 安装 Chromium 浏览器(浏览器二进制走 npmmirror 镜像,避免 cdn.playwright.dev 国内无法访问)
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
RUN playwright install chromium --with-deps

COPY . /app

# 上传目录（bind mount 到宿主机 ./backend/uploads）
RUN mkdir -p /app/uploads

EXPOSE 8000

# gunicorn + uvicorn worker
# -w 1: 单 worker, 与 docker-compose.yml、Dockerfile.patch 三处保持一致（#212 已消除 -w 4 矛盾）。
#   原因（下列状态均为进程内状态，多 worker 各持一份即失效）:
#     · llm_scheduler 的进程级 threading.Condition 门闸(_gates): 保证任一时刻仅 1 个 LLM
#       调用在飞，多 worker 会把单个 ollama 端点并发打爆；
#     · 同步任务管理器(SyncTaskManager) / 登录限速(core.rate_limit) 的内存锁与计数:
#       多 worker 各自计数会导致重复同步(数据重复)与限速失效。
#   多 worker 水平扩容是 v0.7「立此存照、不做」项；如需，须先将这些状态迁至 Redis。
# --timeout 900s: sync-all 多源抓取含 65s 间隔, 全程 5-8 分钟, 默认 120s 会被 gunicorn
#   判超时 SIGABRT → 前端 502。
# 日志: gunicorn/uvicorn/app 日志统一走 stdout/stderr, 由 docker json-file 轮转(10m×3)捕获,
#       用 `docker compose logs backend` 查看; nginx 访问日志另以文件形式落在 ./logs/nginx。
CMD ["gunicorn", "app.main:app", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--timeout", "900", "--log-level", "info"]
