FROM node:24-alpine AS build

WORKDIR /app

COPY package*.json ./

RUN npm ci

COPY . .

# Vite inlines these at build time (the browser calls each Student service directly).
ARG VITE_AUTH_API_URL
ARG VITE_PROFILE_API_URL
ARG VITE_ENROLLMENT_API_URL
ARG VITE_TEST_API_URL
ARG VITE_ATTEMPT_API_URL
ARG VITE_RESULT_API_URL
ARG VITE_RANKING_API_URL
ARG VITE_NOTIFICATION_API_URL
ENV VITE_AUTH_API_URL=$VITE_AUTH_API_URL VITE_PROFILE_API_URL=$VITE_PROFILE_API_URL VITE_ENROLLMENT_API_URL=$VITE_ENROLLMENT_API_URL \
    VITE_TEST_API_URL=$VITE_TEST_API_URL VITE_ATTEMPT_API_URL=$VITE_ATTEMPT_API_URL VITE_RESULT_API_URL=$VITE_RESULT_API_URL \
    VITE_RANKING_API_URL=$VITE_RANKING_API_URL VITE_NOTIFICATION_API_URL=$VITE_NOTIFICATION_API_URL

RUN npm run build

# The CSP header lists the API origins this bundle was built with (scripts/render-nginx-conf.mjs).
RUN node scripts/render-nginx-conf.mjs nginx.conf /app/nginx.rendered.conf

FROM nginx:alpine

COPY --from=build /app/nginx.rendered.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
