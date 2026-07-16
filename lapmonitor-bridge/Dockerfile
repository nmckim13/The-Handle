# Minimal image for the LapMonitor -> Supabase bridge worker.
FROM node:20-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY package.json ./
RUN npm install --omit=dev

# App code.
COPY . .

# Status/health endpoint (Fly health check + tablet-viewable status page).
EXPOSE 8080

CMD ["node", "bridge.js"]
