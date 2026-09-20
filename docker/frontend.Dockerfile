# Production build of the frontend (Vite + React + Three.js), served by nginx.
#
# VITE_TAU_API_BASE is baked in at build time (Vite env vars aren't runtime-configurable).
# Default is "" (empty) => the app makes SAME-ORIGIN, relative "/api/..." calls, which nginx below
# proxies to tau-core (frontend.nginx.conf). One origin means it works over plain http on the LAN
# AND over https behind a Tailscale serve / reverse proxy, with no CORS and no mixed content.
# (useMCPResource.js still falls back to http://<host>:8000 when the var is UNSET, e.g. `npm run
# dev`, so local two-origin dev keeps working.)
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_TAU_API_BASE=""
ENV VITE_TAU_API_BASE=${VITE_TAU_API_BASE}
# Which commit this bundle was built from. Passed in rather than read from git: `.git` is not in
# the build context, so `git rev-parse` here would fail and stamp every image the same. Defaults
# to "dev" for a plain `docker build` with no stamp. See vite.config.js.
ARG TAU_BUILD_SHA="dev"
ENV TAU_BUILD_SHA=${TAU_BUILD_SHA}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Single-origin serving: proxy /api/ to tau-core so the whole app is one origin (see the conf).
# Path is relative to the build context (packages/frontend), same as the `COPY . .` above.
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
