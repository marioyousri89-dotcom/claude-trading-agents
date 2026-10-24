ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 10001 agent && mkdir /events && chown agent /events
COPY auth_gate ./auth_gate
COPY trading ./trading
USER agent

ENV EVENTS_DIR=/events
CMD ["python", "-m", "trading.engine"]
