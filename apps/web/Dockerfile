ARG NODE_IMAGE=node:20-alpine
# 生产默认同源：由 nginx 反代 /captcha.cgi、/pages 等到 api 服务
ARG VITE_API_BASE=
FROM ${NODE_IMAGE} AS build
WORKDIR /app
ARG VITE_API_BASE
ENV VITE_API_BASE=${VITE_API_BASE}
COPY package.json package-lock.json* ./
RUN npm config set registry https://registry.npmmirror.com && npm install
COPY . .
RUN npm run build

FROM ${NODE_IMAGE}
RUN apk add --no-cache nginx wget && mkdir -p /run/nginx
COPY nginx.conf /etc/nginx/http.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
ENTRYPOINT ["/docker-entrypoint.sh"]
