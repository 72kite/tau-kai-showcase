# Production build of the admin control panel frontend (Phase 38), served by nginx - same
# single-origin pattern as docker/frontend.Dockerfile, proxying to tau-admin-server instead of
# tau-core.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_ADMIN_API_BASE=""
ENV VITE_ADMIN_API_BASE=${VITE_ADMIN_API_BASE}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
