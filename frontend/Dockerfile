FROM node:22-alpine AS build
ARG APP_VERSION=0.9.0
ARG VITE_API_ORIGIN=
LABEL org.opencontainers.image.title="RAG Builder operator console" \
      org.opencontainers.image.version="$APP_VERSION" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
ENV VITE_API_ORIGIN=$VITE_API_ORIGIN
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.30-alpine AS production
ARG APP_VERSION=0.9.0
LABEL org.opencontainers.image.title="RAG Builder operator console" \
      org.opencontainers.image.version="$APP_VERSION" \
      org.opencontainers.image.licenses="MIT"
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html/operator
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/operator/ || exit 1
