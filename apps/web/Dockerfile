# Production Web image. Standalone Next.js output using standard Node.js.
# Build context must be the repository root so the shared contracts package is visible.
FROM node:20-slim AS builder

WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/. .
COPY packages/contracts/src/generated.ts ./node_modules/@bridges/contracts/generated.ts

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-slim

WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
