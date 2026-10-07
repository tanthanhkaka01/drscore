# drscore in a container (docs/deployment.md, "Run in containers"): Python and drscore, nothing
# else. Configuration, database file, designs and logs stay outside the image: they are the folders
# of DRS_HOME, mounted by deploy/docker/docker-compose.yml.
#
# Build from the root of the source:  docker build -f deploy/docker/Dockerfile -t drscore .
#
# Report sources: PostgreSQL, Oracle (thin mode) and SQLite work as they are; SQL Server works with
# the open-source driver pymssql (datasource option `driver = pymssql`). The Microsoft ODBC driver
# is NOT in this image: it is Microsoft's software under Microsoft's licence, and whoever wants it
# downloads it themselves - deploy/docker/Dockerfile.msodbc adds it on top of this image.
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml LICENSE THIRD_PARTY_LICENSES.md /src/
COPY drscore /src/drscore
RUN pip install --no-cache-dir /src && rm -rf /src

# config/, designs/ and runtime/ of this folder are the installation (mounted).
ENV DRS_HOME=/drs PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /drs
EXPOSE 8080
CMD ["python", "-m", "drscore", "serve"]
