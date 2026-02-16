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
```powershell
Copy-Item .env.template .env
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
```powershell
docker-compose logs -f qingping-webhook
```

### 3. Qingping Developer Platform Webhook URLs

Configure:
- Device Data:  `http://your-server:8000/qingPing/device/data`
- Device Event: `http://your-server:8000/qingPing/device/event`

## API Endpoints

### `GET /`
Service info with version and available endpoints.

### `GET /health`
Health check with MQTT connection status and statistics:
- `status`: `"healthy"` (MQTT connected) or `"degraded"` (MQTT unavailable)
- `mqtt_connected`: boolean - MQTT broker connection status
- `app_secret_configured`: boolean - Whether `QINGPING_APP_SECRET` is set
- `replay_protection_enabled`: boolean - Replay attack protection status
- `timestamp_check_enabled`: boolean - Timestamp validation enabled
- `timestamp`: Current server time in ISO 8601 format (UTC)

### `GET /metrics`
Returns:
- `webhook_stats`: Object with counters:
  - `total_received`: Total webhooks received
  - `signature_valid`: Webhooks with valid signatures
  - `published_success`: Successfully published to MQTT
- `token_cache_size`: Current replay protection token cache size
- `timestamp`: Current server time in ISO 8601 format (UTC)

### `POST /qingPing/device/data`
Webhook endpoint for device data. Validates signature, timestamp, and payload structure.

### `POST /qingPing/device/event`
Webhook endpoint for device events. Validates signature, timestamp, and payload structure.

## MQTT Topic Structure

Published messages use device-specific topics with the following structure:
**Components:**
- `{MQTT_TOPIC_PREFIX}`: Configurable base path (default: `qingPing/device`)
- `{DEVICE_MAC}`: Device MAC address (12 uppercase hexadecimal characters, e.g., `A1B2C3D4E5F6`)
- `{WEBHOOK_TYPE}`: Webhook category - either `data` or `event`

**Examples** (with default prefix):
- `qingPing/device/A1B2C3D4E5F6/data`
- `qingPing/device/A1B2C3D4E5F6/event`

**Note:** The MAC address is always included in the topic for device-specific routing.

**Message Payload:**
The complete original webhook JSON is published, including the `signature` object for full traceability and downstream validation if needed.

**MQTT Settings:**
- QoS: 1 (at least once delivery)
- Retain: false (no message retention)

## Validation Process

1. **JSON Parsing** - Validates incoming payload is valid JSON
2. **Signature Object** - Checks presence of signature object with required fields
3. **Timestamp Format** - Validates timestamp is integer
4. **Token Format** - Validates token is 36-character string
5. **Timestamp Freshness** - Checks timestamp is within acceptable drift
6. **HMAC Signature** - Verifies signature using APP_SECRET
7. **Replay Protection** - Checks token hasn't been used before
8. **Payload Structure** - Validates presence of required fields (`info`, `data`)
9. **MAC Address** - Validates MAC address format (12 hex characters)
10. **MQTT Publishing** - Publishes to device-specific topic

## Configuration Options

| Variable                | Default         | Description                                                                 |
|-------------------------|----------------|-----------------------------------------------------------------------------|
| `QINGPING_APP_SECRET`   | *(required)*   | Qingping App Secret used for HMAC validation                                |
| `MQTT_BROKER`           | `localhost`    | MQTT broker hostname                                                        |
| `MQTT_PORT`             | `1883`         | MQTT broker port                                                            |
| `MQTT_CLIENT_ID`        | `qingping-webhook` | MQTT client ID. A timestamp is appended automatically to ensure uniqueness. |
| `MQTT_USERNAME`         | *(empty)*      | MQTT username                                                               |
| `MQTT_PASSWORD`         | *(empty)*      | MQTT password                                                               |
| `MQTT_TOPIC_PREFIX`     | `qingPing/device` | MQTT topic prefix                                                          |
| `MAX_TIMESTAMP_DRIFT_SECONDS` | `300`   | Allowed timestamp drift in seconds (`0` disables check)                     |
| `ENABLE_REPLAY_PROTECTION` | `true`     | Reject duplicate `token` values                                             |
| `LOG_LEVEL`             | `INFO`         | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)                       |
| `USER_UID`              | `1001`         | **(Docker only)** Linux user ID for the application process                 |
| `USER_GID`              | `1001`         | **(Docker only)** Linux group ID for the application process                |

> **Note:**  
> `USER_UID` and `USER_GID` are used when running the container as a non-root user.  
> Set these to match your host user's UID/GID if you need to manage file permissions or integrate with host-mounted volumes.

## Security Features

### Replay Protection
- Caches up to 1000 tokens
- Automatic cleanup of old tokens (>10 min + 2×drift)
- Periodic cleanup (1% chance per request)
- Thread-safe async operations

### Signature Verification
- HMAC-SHA256 with constant-time comparison
- Validates timestamp freshness (default ±5 minutes)
- Rejects malformed signatures

### Timeout Protection
- 2.5-second processing timeout (Qingping spec: <3s)
- Fail-fast MQTT connection checks
- Graceful degradation if MQTT unavailable

## Troubleshooting

### Signature validation fails
- Verify `QINGPING_APP_SECRET` matches Qingping Developer Platform
- Ensure system time is correct (use NTP)
- Enable `LOG_LEVEL=DEBUG` for detailed logs
- Check signature fields in debug output (sanitized)

### MQTT connection issues
- Verify `MQTT_BROKER` hostname is correct
- Check network connectivity to broker
- Verify credentials if authentication is enabled
- Check `/health` endpoint for connection status

### Timestamp validation fails
- Synchronize system time (NTP)
- Adjust `MAX_TIMESTAMP_DRIFT_SECONDS` if needed
- Check Qingping server time vs. local time

### Replay attacks detected
- Normal behavior if webhook is retried
- Check for duplicate requests in logs
- Disable with `ENABLE_REPLAY_PROTECTION=false` for testing only

## Performance Notes

- Token cache limited to 1000 entries (configurable via `MAX_TOKEN_CACHE_SIZE` constant)
- Automatic cleanup prevents memory leaks
- Async/await for non-blocking operations
- Connection pooling for MQTT
- Request timeout: 2.5s (Qingping spec: 3s)

## Support / Spec
- https://developer.qingping.co/cloud-to-cloud/data-push
- https://developer.qingping.co/cloud-to-cloud/product-access-guidelines
