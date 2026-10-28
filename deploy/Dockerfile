# syntax=docker/dockerfile:1
# BASE_IMAGE must be supplied as a reviewed digest at release packaging time.
ARG BASE_IMAGE=python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-runtime.lock /app/requirements-runtime.lock
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r requirements-runtime.lock; else pip install --no-cache-dir -r requirements-runtime.lock; fi
RUN groupadd --gid 10001 timemap && useradd --uid 10001 --gid 10001 --no-create-home timemap
COPY --chown=10001:10001 backend /app/backend
COPY --chown=10001:10001 time_summary.py /app/time_summary.py
COPY --chown=10001:10001 config /app/config
USER 10001:10001
CMD ["python","-m","uvicorn","backend.prod:build_app","--factory","--host","0.0.0.0","--port","8100","--workers","1"]
