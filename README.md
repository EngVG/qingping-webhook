# Qingping Webhook Validator & MQTT Gateway

A microservice that validates Qingping cloud webhooks using HMAC-SHA256 signature verification and publishes validated messages to MQTT for downstream processing (e.g., Telegraf → InfluxDB).

## Architecture

```
Qingping Cloud → HTTP Webhook → Validator Service → MQTT → Telegraf → InfluxDB
                                      ↓
                                 ✓ Signature Check (HMAC-SHA256)
                                 ✓ Timestamp Validation
                                 ✓ Replay Attack Protection
```

## Features

- HMAC-SHA256 signature verification (Qingping spec)
- Timestamp freshness check (configurable drift)
- Replay protection via token cache with automatic cleanup
- MAC address format validation
- Payload structure validation
- Two webhook endpoints:
  - `/qingPing/device/data`
  - `/qingPing/device/event`
- MQTT publishing with device-specific topics (QoS 1)
- Health and lightweight metrics endpoints (`/health`, `/metrics`)
- 2.5-second processing timeout (Qingping requires <3s response)

## Quick Start

### 1. Configuration

Copy the environment template:
```bash
cp .env.template .env
```

Edit `.env`:
```env
QINGPING_APP_SECRET=your_actual_app_secret_here
MQTT_BROKER=mosquitto
MQTT_CLIENT_ID=qingping-webhook
MQTT_TOPIC_PREFIX=qingPing/device
```

### 2. Run (Docker)

````````
docker-compose up -d
````````

Check logs:
```bash
docker-compose logs -f qingping-webhook
```


### 3. Qingping Developer Platform Webhook URLs

Configure:
- Device Data:  `http://your-server:8000/qingPing/device/data`
- Device Event: `http://your-server:8000/qingPing/device/event`

## API Endpoints

- `GET /` — Service info with version and available endpoints.
- `GET /health` — Health check with MQTT connection status and statistics.
- `GET /metrics` — Metrics and webhook statistics.
- `POST /qingPing/device/data` — Webhook endpoint for device data.
- `POST /qingPing/device/event` — Webhook endpoint for device events.

## MQTT Topic Structure

Published messages use device-specific topics:
- `{MQTT_TOPIC_PREFIX}/{DEVICE_MAC}/{WEBHOOK_TYPE}`

**Examples (default prefix):**
- `qingPing/device/A1B2C3D4E5F6/data`
- `qingPing/device/A1B2C3D4E5F6/event`

- MAC address: 12 uppercase hexadecimal characters (e.g., `A1B2C3D4E5F6`)
- The complete original webhook JSON is published, including the `signature` object.

**MQTT Settings:**
- QoS: 1 (at least once delivery)
- Retain: false

## Validation Process

1. JSON parsing
2. Signature object presence and required fields
3. Timestamp format (integer)
4. Token format (36-character string)
5. Timestamp freshness (within drift)
6. HMAC signature verification
7. Replay protection (token uniqueness)
8. Payload structure (`info`, `data`)
9. MAC address format
10. MQTT publishing

## Configuration Options

| Variable                      | Default             | Description                                                                 |
|-------------------------------|---------------------|-----------------------------------------------------------------------------|
| `QINGPING_APP_SECRET`         | *(required)*        | Qingping App Secret for HMAC validation                                     |
| `MQTT_BROKER`                 | `localhost`         | MQTT broker hostname                                                        |
| `MQTT_PORT`                   | `1883`              | MQTT broker port                                                            |
| `MQTT_CLIENT_ID`              | `qingping-webhook`  | MQTT client ID (timestamp appended for uniqueness)                          |
| `MQTT_USERNAME`               | *(empty)*           | MQTT username                                                               |
| `MQTT_PASSWORD`               | *(empty)*           | MQTT password                                                               |
| `MQTT_TOPIC_PREFIX`           | `qingPing/device`   | MQTT topic prefix                                                           |
| `MAX_TIMESTAMP_DRIFT_SECONDS` | `300`               | Allowed timestamp drift in seconds (`0` disables check)                     |
| `ENABLE_REPLAY_PROTECTION`    | `true`              | Reject duplicate `token` values                                             |
| `LOG_LEVEL`                   | `INFO`              | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)                       |
| `USER_UID`                    | `1001`              | **(Docker only)** Linux user ID for the application process                 |
| `USER_GID`                    | `1001`              | **(Docker only)** Linux group ID for the application process                |

> **Note:**
> `USER_UID` and `USER_GID` are used when running the container as a non-root user.
> Set these to match your host user's UID/GID if you need to manage file permissions or integrate with host-mounted volumes.

## Security Features

- **Replay Protection:** Caches up to 1000 tokens, automatic cleanup, thread-safe async operations.
- **Signature Verification:** HMAC-SHA256 with constant-time comparison, timestamp freshness, rejects malformed signatures.
- **Timeout Protection:** 2.5-second processing timeout, fail-fast MQTT checks, graceful degradation if MQTT unavailable.

## Troubleshooting

**Signature validation fails**
- Verify `QINGPING_APP_SECRET` matches Qingping Developer Platform
- Ensure system time is correct (use NTP)
- Enable `LOG_LEVEL=DEBUG` for detailed logs

**MQTT connection issues**
- Verify `MQTT_BROKER` hostname
- Check network connectivity
- Verify credentials if authentication is enabled
- Check `/health` endpoint

**Timestamp validation fails**
- Synchronize system time (NTP)
- Adjust `MAX_TIMESTAMP_DRIFT_SECONDS` if needed

**Replay attacks detected**
- Normal if webhook is retried
- Check for duplicate requests in logs
- Disable with `ENABLE_REPLAY_PROTECTION=false` for testing only

## Performance Notes

- Token cache limited to 1000 entries (configurable via `MAX_TOKEN_CACHE_SIZE`)
- Automatic cleanup prevents memory leaks
- Async/await for non-blocking operations
- Connection pooling for MQTT
- Request timeout: 2.5s (Qingping spec: 3s)

## Support / Spec

- [Qingping Data Push](https://developer.qingping.co/cloud-to-cloud/data-push)
- [Qingping Product Access Guidelines](https://developer.qingping.co/cloud-to-cloud/product-access-guidelines)
