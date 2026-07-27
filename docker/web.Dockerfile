FROM node:22-bookworm-slim

WORKDIR /app

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web ./
RUN npm run build

ENV NODE_ENV=production
EXPOSE 5173

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "5173"]
