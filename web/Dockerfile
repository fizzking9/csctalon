# 前端构建 + nginx 托管(多阶段)
FROM node:20-alpine AS builder
WORKDIR /app
# 复制 lockfile, 用 npm ci 严格按 lockfile 安装(可复现, 不被浮动版本污染)
COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmmirror.com && npm ci --silent
COPY . .
RUN npm run build

# 锁定 nginx 1.27 minor, 避免镜像 tag 浮动导致构建不可复现
FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
