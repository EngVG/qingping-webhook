FROM python:3.12-slim

# Metadata
LABEL maintainer="Engineering von Gunten"
LABEL description="Qingping Webhook Validator & MQTT Gateway Service"
LABEL version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Multi-stage build for even smaller images (optional)
FROM python:3.12-slim as builder
WORKDIR /app
COPY app/requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH
COPY app/ ./

# Add a non-root user with specific UID and GID
ARG USERNAME=qingping
ARG USER_UID=1000
ARG USER_GID=1000
RUN addgroup --gid $USER_GID $USERNAME && \
    adduser --disabled-password --gecos "" --uid $USER_UID --gid $USER_GID $USERNAME

# Switch to the new user
USER $USERNAME

# Expose port
EXPOSE 8000

# Health check - uses the /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).getcode() == 200 else 1)"

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
