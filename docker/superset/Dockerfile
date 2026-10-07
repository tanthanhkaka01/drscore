# Apache Superset (Apache-2.0), open-source image, plus the PostgreSQL driver it needs to read
# the drs_bi schema. Pinned version.
FROM apache/superset:6.1.0
USER root
# Recent Superset images keep Python packages in /app/.venv and ship without database drivers.
RUN if [ -x /app/.venv/bin/python ]; then \
        /app/.venv/bin/python -m pip install --no-cache-dir psycopg2-binary || \
        (cd /app && uv pip install --python /app/.venv/bin/python psycopg2-binary); \
    else pip install --no-cache-dir psycopg2-binary; fi
USER superset
