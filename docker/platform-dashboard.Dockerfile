# Production image for apps/platform-dashboard. Build context must be
# apps/platform-dashboard itself (see docker-compose.staging.yml). Mirrors
# docker/operator-dashboard.Dockerfile — see its comments for rationale.

FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_WS_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_PUBLIC_WS_BASE_URL=${NEXT_PUBLIC_WS_BASE_URL} \
    NODE_ENV=production
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
RUN groupadd --system rastichat && useradd --system --gid rastichat --home /app --shell /usr/sbin/nologin rastichat

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

RUN chown -R rastichat:rastichat /app
USER rastichat

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)).then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))"

EXPOSE 3000
CMD ["node", "server.js"]
