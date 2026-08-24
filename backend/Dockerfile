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
    && rm -rf /var/lib/apt/lists/*

# pip 使用清华源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.110.0" "uvicorn[standard]>=0.27.0" "gunicorn>=21.2.0" \
    "sqlalchemy>=2.0.25" "alembic>=1.13.0" "psycopg2-binary>=2.9.9" \
    "pydantic>=2.6.0" "pydantic-settings>=2.1.0" \
    "python-jose[cryptography]>=3.3.0" "bcrypt>=4.0.0" \
    "python-multipart>=0.0.9" "pandas>=2.2.0" "openpyxl>=3.1.2" "python-calamine>=0.2.0" \
    "redis>=5.0.0" "python-dotenv>=1.0.0" "orjson>=3.9.0" \
    "pypinyin>=0.50.0" "httpx>=0.27.0" \
    "playwright>=1.45.0" \
    # 验证码自动识别(ddddocr + ONNX Runtime,纯 Python,无外部系统依赖)
    "ddddocr>=1.4.11" "onnxruntime>=1.17.0" \
    # 发票二维码图本地解码(规划设计 §4.5 全电发票识别链路)
    "pyzbar>=0.1.9" "pillow>=10.0.0"

# 安装 Chromium 浏览器(浏览器二进制走 npmmirror 镜像,避免 cdn.playwright.dev 国内无法访问)
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/
RUN playwright install chromium --with-deps

COPY . /app

# 上传目录（bind mount 到宿主机 ./backend/uploads）
RUN mkdir -p /app/uploads

EXPOSE 8000

# gunicorn + uvicorn worker
# -w 1: 单 worker, 与 docker-compose 一致。
#   原因: 同步任务管理器/登录限速用进程内内存互斥锁(asyncio.Lock/字典), 多 worker 下各自计数
#   会导致重复同步与限速失效。如需多 worker 并发, 须先将这些状态迁至 Redis(M5)。
# --timeout 900s: sync-all 多源抓取含 65s 间隔, 全程 5-8 分钟, 默认 120s 会被 gunicorn
#   判超时 SIGABRT → 前端 502。
# 日志: gunicorn/uvicorn/app 日志统一走 stdout/stderr, 由 docker json-file 轮转(10m×3)捕获,
#       用 `docker compose logs backend` 查看; nginx 访问日志另以文件形式落在 ./logs/nginx。
CMD ["gunicorn", "app.main:app", "-w", "1", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--timeout", "900", "--log-level", "info"]
