# Production image for packages/widget: builds the widget bundle, then
# serves it as static files from Nginx — no Node runtime needed once built.
# Build context must be packages/widget (see docker-compose.staging.yml) —
# nginx.conf lives alongside the package source for exactly that reason.

FROM node:20-slim AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# WIDGET_VERSION namespaces the immutable-cache URL (see widget-nginx.conf);
# defaults to "dev" for local/staging builds that don't pass a real value —
# CI passes the git short SHA (see .github/workflows/staging-deploy.yml).
ARG WIDGET_VERSION=dev
RUN mkdir -p /out/widget/${WIDGET_VERSION} \
    /out/examples/customer-test \
    /out/examples/guest \
    /out/examples/portal \
    && cp dist/widget.iife.js /out/widget.js \
    && cp dist/widget.iife.js /out/widget/${WIDGET_VERSION}/widget.js \
    && cp examples/customer-test/index.html /out/examples/customer-test/index.html \
    && cp examples/customer-test/style.css /out/examples/customer-test/style.css \
    && cp examples/customer-test/app.js /out/examples/customer-test/app.js \
    && cp examples/guest/index.html /out/examples/guest/index.html \
    && cp examples/guest/style.css /out/examples/guest/style.css \
    && cp examples/guest/app.js /out/examples/guest/app.js \
    && cp examples/portal/index.html /out/examples/portal/index.html \
    && cp examples/portal/style.css /out/examples/portal/style.css

FROM nginx:1.27-alpine AS runner
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /out /usr/share/nginx/html

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

EXPOSE 8080
