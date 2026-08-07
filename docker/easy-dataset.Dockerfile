FROM node:20-alpine AS pnpm-base

RUN npm install -g pnpm@9

FROM pnpm-base AS builder

WORKDIR /app

RUN apk add --no-cache --virtual .build-deps \
    python3 \
    make \
    g++ \
    cairo-dev \
    pango-dev \
    jpeg-dev \
    giflib-dev \
    librsvg-dev \
    build-base \
    pixman-dev \
    pkgconfig

# Easy Dataset 的 Web sidecar 不运行 Electron；跳过约 100 MB 的桌面运行时下载，
# 避免本机 Docker 首次构建被无关的 Electron 网络请求中断。
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1

COPY upstream/easy-dataset/package.json \
     upstream/easy-dataset/pnpm-lock.yaml \
     upstream/easy-dataset/.npmrc ./
RUN pnpm install

COPY upstream/easy-dataset ./

RUN if [ "$(uname -m)" = "aarch64" ] || [ "$(uname -m)" = "arm64" ]; then \
        sed -i 's/binaryTargets = \[.*\]/binaryTargets = ["linux-musl-arm64-openssl-3.0.x"]/' prisma/schema.prisma; \
        PRISMA_CLI_BINARY_TARGETS="linux-musl-arm64-openssl-3.0.x" pnpm build; \
    else \
        sed -i 's/binaryTargets = \[.*\]/binaryTargets = ["linux-musl-openssl-3.0.x"]/' prisma/schema.prisma; \
        PRISMA_CLI_BINARY_TARGETS="linux-musl-openssl-3.0.x" pnpm build; \
    fi \
    && pnpm prune --prod

FROM pnpm-base AS runner

WORKDIR /app

RUN apk add --no-cache cairo pango jpeg giflib librsvg pixman

COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/electron ./electron
COPY --from=builder /app/prisma /app/prisma-template
COPY --from=builder /app/docker-entrypoint.sh /usr/local/bin/easy-dataset-entrypoint.sh

RUN sed -i 's/\r$//' /usr/local/bin/easy-dataset-entrypoint.sh \
    && chmod +x /usr/local/bin/easy-dataset-entrypoint.sh

ENV NODE_ENV=production \
    DATABASE_URL=file:/app/prisma/db.sqlite \
    LOCAL_DB_PATH=/app/local-db

EXPOSE 1717

ENTRYPOINT ["/usr/local/bin/easy-dataset-entrypoint.sh"]
CMD ["pnpm", "start"]
