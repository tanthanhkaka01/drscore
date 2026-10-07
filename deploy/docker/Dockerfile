# DRS in a container (docs/deployment.md, "Run in containers"). Python, DRS, and the ODBC driver
# that report sources on SQL Server need. Configuration, designs and logs stay outside the image:
# they are the folders of DRS_HOME, mounted by deploy/docker/docker-compose.yml.
#
# Build from the root of the source:  docker build -f deploy/docker/Dockerfile -t drs .
FROM python:3.12-slim-bookworm

# Microsoft ODBC Driver 18 for SQL Server (free of charge, proprietary: THIRD_PARTY_LICENSES.md).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates tzdata \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && apt-get purge -y --auto-remove gnupg \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /src/
COPY drs /src/drs
RUN pip install --no-cache-dir /src && rm -rf /src

# config/, designs/ and runtime/ of this folder are the installation (mounted).
ENV DRS_HOME=/drs PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /drs
EXPOSE 8090
CMD ["python", "-m", "drs", "serve"]
