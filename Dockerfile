FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY scripts/serve_fastapi.py scripts/serve_fastapi.py

# Copy startup script
COPY start.sh start.sh
RUN chmod +x start.sh

# Wiki pages are copied into the image (small, 606KB)
COPY wiki/ /data/wiki/

# Database will be mounted as a volume at /data/db/
RUN mkdir -p /data/db

# Environment defaults
ENV DB_PATH=/data/db/laws.db
ENV WIKI_DIR=/data/wiki
ENV PORT=8080
ENV QMD_CMD=""

EXPOSE 8080

CMD ["./start.sh"]
