FROM node:24-alpine AS build

WORKDIR /app

COPY package*.json ./

RUN npm ci

COPY . .

ARG VITE_INSTITUTE_API_URL
ENV VITE_INSTITUTE_API_URL=$VITE_INSTITUTE_API_URL

RUN npm run build

# The CSP header lists the API origins this bundle was built with (scripts/render-nginx-conf.mjs).
RUN node scripts/render-nginx-conf.mjs nginx.conf /app/nginx.rendered.conf

FROM nginx:alpine

COPY --from=build /app/nginx.rendered.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
