# Gridless web: Next.js 16 built to a standalone server.
#
# The one thing to know before editing this file: NEXT_PUBLIC_* variables are
# inlined into the client bundle at BUILD time, so the API URL the browser uses
# is fixed here, not at run time. It must therefore be an address that resolves
# on the *user's* machine (the published port), never the `api` service name --
# which is why the server-side base is a separate, run-time variable. See
# lib/api.ts.
FROM node:22-slim AS deps
WORKDIR /app
RUN corepack enable
# pnpm's version comes from package.json's `packageManager` field.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:22-slim AS builder
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010
ARG NEXT_PUBLIC_ORG_ID=1
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_PUBLIC_ORG_ID=$NEXT_PUBLIC_ORG_ID \
    NEXT_TELEMETRY_DISABLED=1

RUN pnpm build

# Development: the Next dev server, for `docker compose watch`. Compose syncs
# edited files into /app and Next's HMR picks them up -- so this stage does NOT
# build anything, and NEXT_PUBLIC_* are read at run time here rather than being
# frozen into a bundle.
FROM node:22-slim AS dev
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000

# Same probe as the production stage, so `docker compose ps` reports health in
# either mode. The dev server compiles a route on first request, so the timeout
# is generous.
HEALTHCHECK --interval=10s --timeout=15s --start-period=30s --retries=6 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

# `next dev` already binds 0.0.0.0, which is what makes it reachable from the
# host through the published port.
CMD ["pnpm", "dev"]

# Production is the last stage on purpose: a plain `docker build ./frontend`
# gives the built server, not the dev one.
FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# The standalone server does not copy `public` or `.next/static` itself; it
# serves them once they are in place.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

USER node
EXPOSE 3000

HEALTHCHECK --interval=5s --timeout=5s --start-period=20s --retries=12 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]
