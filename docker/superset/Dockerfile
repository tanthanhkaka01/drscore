# Apache Superset (Apache-2.0), open-source image, plus the PostgreSQL driver it needs to read
# the drs_bi schema. Pinned version.
FROM apache/superset:6.1.0
USER root
# Recent Superset images keep Python packages in /app/.venv and ship without database drivers.
RUN if [ -x /app/.venv/bin/python ]; then \
        /app/.venv/bin/python -m pip install --no-cache-dir psycopg2-binary || \
        (cd /app && uv pip install --python /app/.venv/bin/python psycopg2-binary); \
    else pip install --no-cache-dir psycopg2-binary; fi
# The DRS look (the site kit's colours and type, superset_config.py THEME_DEFAULT / THEME_DARK): the logo, the
# favicon and the Manrope faces under /static/assets/drs/, and the <link> that loads the faces in every page.
COPY drs-theme/ /tmp/drs-theme/
RUN PY=$( [ -x /app/.venv/bin/python ] && echo /app/.venv/bin/python || echo python ); \
    SUPERSET_DIR=$($PY -c "import os, superset; print(os.path.dirname(superset.__file__))"); \
    mkdir -p "$SUPERSET_DIR/static/assets/drs" && \
    cp -r /tmp/drs-theme/fonts /tmp/drs-theme/fonts.css /tmp/drs-theme/logo.svg /tmp/drs-theme/favicon.svg \
          "$SUPERSET_DIR/static/assets/drs/" && \
    cp /tmp/drs-theme/head_custom_extra.html "$SUPERSET_DIR/templates/head_custom_extra.html" && \
    chmod -R a+rX "$SUPERSET_DIR/static/assets/drs" "$SUPERSET_DIR/templates/head_custom_extra.html" && \
    rm -rf /tmp/drs-theme
USER superset
