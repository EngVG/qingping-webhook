FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Cleanup
RUN apt-get update && \
    apt-get autoremove --purge -y && \
    apt-get autoclean && \
    rm -rf /var/lib/apt/lists/*

# Copy application
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

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).getcode() == 200 else 1)"

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
