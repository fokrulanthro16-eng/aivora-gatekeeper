# ── Stage 1: Node build ───────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies (layer-cached separately from source)
COPY package.json package-lock.json ./
RUN npm ci --prefer-offline

# Copy source and build
COPY . .

# VITE_API_BASE_URL is injected at build time; override with docker build --build-arg
ARG  VITE_API_BASE_URL=http://localhost:8000
ENV  VITE_API_BASE_URL=$VITE_API_BASE_URL

# VITE_DEMO_MODE=false in production; override for demo deployments
ARG  VITE_DEMO_MODE=false
ENV  VITE_DEMO_MODE=$VITE_DEMO_MODE

RUN npm run build


# ── Stage 2: nginx serve ──────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist    /usr/share/nginx/html
COPY nginx.conf                  /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://localhost:80/ || exit 1
