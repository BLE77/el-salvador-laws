#!/bin/bash
# Wait for database to be uploaded, then start server
if [ ! -f /data/db/laws.db ]; then
    echo "============================================"
    echo "  DATABASE NOT FOUND at /data/db/laws.db"
    echo "  Upload it via: fly sftp shell"
    echo "  Then put laws.db into /data/db/"
    echo "  Waiting..."
    echo "============================================"
    # Keep the container alive so we can SSH in and upload the DB
    while [ ! -f /data/db/laws.db ]; do
        sleep 5
    done
    echo "Database found! Starting server..."
fi

exec python scripts/serve_fastapi.py
