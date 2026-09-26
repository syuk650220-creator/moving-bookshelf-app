# =====================================================================
#  セルフホスト: アプリの画面（Next.js）を Pi の Docker で動かすためのイメージ
#
#  作るのは setup.sh（docker compose build web）。初回とアプリを更新したとき（bash setup.sh rebuild-web）。
#  ★ビルドにはインターネットが要る（npm の部品と、画面の文字（Google Fonts）を取りに行くため）★
#  できあがったイメージは、動かすときにはインターネットを使わない。
#
#    NEXT_PUBLIC_SUPABASE_URL=same-origin … 開いたページと同じ場所の /rest/v1 を使う（lib/supabaseClient.ts）
#    NEXT_OUTPUT_STANDALONE=1             … node_modules なしで動く最小のサーバー一式を作る（next.config.ts）
# =====================================================================

FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ENV NEXT_PUBLIC_SUPABASE_URL=same-origin \
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=selfhost \
    NEXT_OUTPUT_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1
RUN npx next build

FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
