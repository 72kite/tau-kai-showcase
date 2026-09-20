# tau-admin-server (Phase 38): the standalone admin control panel backend, deliberately a
# separate image/process/port from tau-core - see the package README for why.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1 \
    TAU_ADMIN_BIND_HOST=0.0.0.0 \
    TAU_ADMIN_BIND_PORT=8100

EXPOSE 8100
CMD ["python", "-m", "tau_admin_server"]
