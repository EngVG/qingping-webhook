#!  /usr/bin/env python
# -*- coding: utf-8 -*-
#
# file: main.py
# brief: Qingping Webhook - FastAPI Validator Service
#        Qingping → Webhook Service → MQTT → Telegraf → InfluxDB
#
# author: martin von gunten
# $Id: main.py 4616 2026-02-17 08:33:58Z m.vgunten $
#
# (c) 2026 by engineering von gunten / all rights reserved


# ---------------------------------------------------------
# --> https://developer.qingping.co/cloud-to-cloud/product-access-guidelines
# Apply the App Key and App Secret of the Open API
#   On developer platform, you can apply the App Key and App Secret on the developer information management page. The App Key and App Secret are used for getting the Access Token which is necessary when calling the Open API. The detail about the Open API, please refer to Open API Specification
# Register Webhooks（Optional）
#   By using webhooks, developer platform can push the information of your devices to your platform. The data and events the devices report are supported now, so you can get these in near-real-time and do something more.
#   Please register the webhooks on the developer information management page:
#   Webhook	Description
#   Device Data	Used to push the near-real-time data of the devices after the devices reported them
#   Device Event	Used to push the events of the devices, includes online, offline, low power, alerts and so on. Different products support different events, please refer to Specification - 2. Products List and Support Note
#
# Register MQTT Information（Optional）
#   Notice: The purposes of MQTT and Webhooks are the same, you should choose one of them.
#   Please register MQTT information on the developer information management page:
#   Item	Description
#   MQTT User Name	User Name for MQTT Connection
#   MQTT User Secret	User Secret for MQTT Connection
#   MQTT Broker List	MQTT Broker address (IP:Port)
#   MQTT Client Prefix	The prefix of the client ID in MQTT Connection (Qingping Developer Platform will add a microsecond Unix timestamp after the prefix to avoid repetition, so the length of the prefix should not be longer than 8 bytes)
#   Topic Data	Used to push the near-real-time data of the devices after the devices reported them
#   Topic Event	Used to push the events of the devices, includes online, offline, low power, alerts and so on. Different products support different events, please refer to Specification - 2. Products List and Support Note
#
# --> https://developer.qingping.co/cloud-to-cloud/data-push
# Webhook
#   Data will be put in the body part of every HTTP/HTTPS request. They are in Json format with "Content-Type: application/json" in the request header.
#   After getting the Webhook requests, you should make a response in 3 seconds. Otherwise Qingping Developer Platform will determine this request is timeout, this request will be in the delay-push status.
#   When the response code from you is 200, Qingping Developer Platform will determine this request is done, otherwise this request will be in the delay-push status.
#   In the delay-push status, Qingping Developer Platform will retry the request during 30 minutes at the following intervals before stop trying: 5 minutes, 15 minutes, 30 minutes. (If all requests are failed, the data will be aborted, but you can also get them by calling the Open API)
#   If the 10-seconds-timeout ratio of your HTTP/HTTPS server is bigger than 50%, Qingping Developer Platform will fuse this Webhook address for 10 minutes, during this time no requests will be make. You can also get data by calling the Open API.
#
# Signature Rules
#   To ensure the authenticity of every push request, Qingping Developer Platform will sign them and posts the signature along other parameters in the Json object. The signature part includes these three parameters:
#   Parameter   Type    Description
#   timestamp   int     Unix timestamp
#   token       string  Randomly generated string with length 36
#   signature   string  String with hexadecimal digits generate by HMAC algorithm
#
# To verify the request is originating from Qingping Developer Platform you need to:
#   Concatenate timestamp and token values.
#   Encode the resulting string with the HMAC algorithm (using your App Secret on Qingping Developer Platform and SHA256 digest mode).
#   Compare the resulting hexdigest to the signature.
#   Optionally, you can cache the token value locally and ignore other subsequent request with the same token. This will prevent replay attacks.
#   Optionally, you can check if the timestamp is not too far from the current time.


# ---------------------------------------------------------
import os
import hmac
import hashlib
import time
import asyncio
from datetime import datetime, timezone
import random
import json
from collections import defaultdict
import copy
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional
import re
import logging

# ---------------------------------------------------------
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from paho.mqtt import client as mqtt_client
from paho.mqtt.client import CallbackAPIVersion


# ---------------------------------------------------------
# Configuration from environment
APP_SECRET = os.getenv("QINGPING_APP_SECRET", "")
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "qingping-webhook")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "qingPing/device")
MAX_TIMESTAMP_DRIFT = int(os.getenv("MAX_TIMESTAMP_DRIFT_SECONDS", "300"))  # 5 minutes
ENABLE_REPLAY_PROTECTION = os.getenv("ENABLE_REPLAY_PROTECTION", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()  # CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET

# Internal constants
MQTT_CONNECTION_WAIT_INTERVAL = 0.05  # seconds to wait between connection status checks
MAX_TOKEN_CACHE_SIZE = 1000  # Maximum number of tokens to cache for replay protection


# ---------------------------------------------------------
# Logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
logger.info(f"Log level set to: {LOG_LEVEL}")


# ---------------------------------------------------------
class MQTTPublisher:
    """MQTT Publisher with connection management and publishing capabilities"""

    def __init__(self, broker: str, port: int, username: str = "", password: str = ""):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.logger = logging.getLogger(f"{__name__}.MQTTPublisher")
        # Initialize MQTT client
        self.client = mqtt_client.Client(CallbackAPIVersion.VERSION2, client_id=f"{MQTT_CLIENT_ID}-{time.time():.3f}")
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        # Set credentials if provided
        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)

    def _on_connect(self, client, userdata, flags, rc, properties):
        """MQTT connection callback (paho-mqtt 2.x VERSION2 API)"""
        if rc == 0:
            self.logger.info(f"Connected to MQTT broker at {self.broker}:{self.port}")
        else:
            self.logger.error(f"Failed to connect to MQTT broker, return code {rc}")

    def _on_disconnect(self, client, userdata, disconnect_flags, rc, properties):
        """MQTT disconnection callback (paho-mqtt 2.x VERSION2 API)"""
        if rc == 0:
            self.logger.info(f"Disconnected from MQTT broker at {self.broker}:{self.port}")
        else:
            self.logger.warning(f"Unexpected disconnection from MQTT broker: {rc}")

    def connect(self) -> bool:
        """Connect to MQTT broker and start background loop."""
        if self.is_connected():
            self.logger.debug("MQTT client already connected")
            return True
        self.logger.info("Connecting to MQTT broker...")
        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            return False

    def disconnect(self):
        """Disconnect from MQTT broker and stop background loop"""
        self.logger.info("Disconnecting from MQTT broker...")
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, topic: str, payload: Dict[str, Any], retries: int = 3) -> bool:
        """
        Publish validated payload to MQTT.

        Args:
            topic: The MQTT topic to publish to
            payload: The payload to publish (will be JSON-encoded)
            retries: Number of retry attempts in case of failure

        Returns:
            True if publishing was successful, False otherwise
        """
        for attempt in range(retries):
            try:
                if not self.is_connected() and not self.connect():
                    continue
                result = self.client.publish(topic, json.dumps(payload), qos=1, retain=False)
                if result.rc == mqtt_client.MQTT_ERR_SUCCESS:
                    self.logger.info(f"Published to MQTT: {topic}")
                    return True
                else:
                    self.logger.error(f"MQTT publish failed with code: {result.rc}")
                    return False
            except Exception as e:
                self.logger.warning(f"Publish attempt {attempt+1} failed: {e}")
        self.logger.error(f"Publish to MQTT: {topic} failed")
        return False

    def is_connected(self) -> bool:
        """Check if MQTT client is connected"""
        return self.client.is_connected()


# ---------------------------------------------------------
# startup checks
if not APP_SECRET:
    logger.error("FATAL: QINGPING_APP_SECRET environment variable is required!")
    raise ValueError("QINGPING_APP_SECRET must be set")
logger.info("APP_SECRET configured successfully")


# ---------------------------------------------------------
# Initialize MQTT Publisher
logger.info("Initialize MQTT Publisher")
mqtt_publisher = MQTTPublisher(
    broker=MQTT_BROKER,
    port=MQTT_PORT,
    username=MQTT_USERNAME,
    password=MQTT_PASSWORD
)


# ---------------------------------------------------------
# Use FastAPI lifespan context for startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic (if any) can go here
    logger.info("Starting up FastAPI...")
    if not await ensure_mqtt_connection():
        logger.warning("Unable to connect to MQTT broker during startup, will retry on demand")
    yield
    # Shutdown logic
    logger.info("Shutting down FastAPI...")
    try:
        if mqtt_publisher.is_connected():
            mqtt_publisher.disconnect()
    except Exception:
        logger.exception("Error while disconnecting from MQTT broker during shutdown")


# ---------------------------------------------------------
# Application
logger.info("Initialize FastAPI Application")
app = FastAPI(lifespan=lifespan)
webhook_stats = defaultdict(int)


# ---------------------------------------------------------
# Token cache for replay attack prevention
token_cache: Dict[str, float] = dict()
token_cache_lock: Optional[asyncio.Lock] = None
mqtt_reconnect_lock: Optional[asyncio.Lock] = None


# ---------------------------------------------------------
async def ensure_mqtt_connection(timeout_seconds: float = 2.0) -> bool:
    """Ensure the MQTT client is connected without blocking the event loop."""
    global mqtt_reconnect_lock

    loop = asyncio.get_running_loop()
    if mqtt_reconnect_lock is None:
        mqtt_reconnect_lock = asyncio.Lock()
    async with mqtt_reconnect_lock:
        # Check again inside lock to avoid race condition
        if mqtt_publisher.is_connected():
            return True

        connect_result = await loop.run_in_executor(None, mqtt_publisher.connect)
        if not connect_result:
            return False

        deadline = time.monotonic() + timeout_seconds
        while not mqtt_publisher.is_connected() and time.monotonic() < deadline:
            await asyncio.sleep(MQTT_CONNECTION_WAIT_INTERVAL)

        return mqtt_publisher.is_connected()


# ---------------------------------------------------------
def sanitize_signature_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep-copied payload with sensitive signature fields blanked."""
    sanitized = copy.deepcopy(payload)
    signature_section = sanitized.get('signature')

    if isinstance(signature_section, dict):
        if 'token' in signature_section and isinstance(signature_section['token'], str):
            token = signature_section['token']
            signature_section['token'] = token if len(token) <= 8 else token[:4] + "..." + token[-4:]
        if 'signature' in signature_section and isinstance(signature_section['signature'], str):
            signature = signature_section['signature']
            signature_section['signature'] = signature if len(signature) <= 8 else signature[:4] + "..." + signature[-4:]

    return sanitized


# ---------------------------------------------------------
def verify_signature(timestamp: str, token: str, signature: str) -> bool:
    """
    Verify HMAC-SHA256 signature according to Qingping documentation

    Args:
        timestamp: Unix timestamp from signature object as provided (string form)
        token: Random token from signature object
        signature: HMAC signature to verify

    Returns:
        True if signature is valid, False otherwise
    """
    try:
        # Concatenate timestamp and token
        message = f"{timestamp}{token}"

        # Compute HMAC-SHA256
        computed_signature = hmac.new(
            APP_SECRET.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Compare signatures (constant-time comparison)
        is_valid = hmac.compare_digest(computed_signature, signature)

        if not is_valid:
            logger.warning("Signature mismatch")

        return is_valid

    except Exception as e:
        logger.error(f"Error verifying signature: {e}")
        return False


# ---------------------------------------------------------
async def check_replay_attack(token: str) -> bool:
    """
    Check if token has been seen before (replay attack detection)

    Args:
        token: Token to check

    Returns:
        True if token is new (valid), False if seen before (replay attack)
    """
    global token_cache_lock

    if not ENABLE_REPLAY_PROTECTION:
        return True  # Check disabled

    if token_cache_lock is None:
        token_cache_lock = asyncio.Lock()

    async with token_cache_lock:
        current_time = time.time()

        # Check if token exists in cache
        if token in token_cache:
            logger.warning(f"Replay attack detected: token {token} already used")
            return False

        # Add token to cache
        token_cache[token] = current_time

        # Cleanup old tokens if cache is too large
        if len(token_cache) > MAX_TOKEN_CACHE_SIZE:
            cleanup_old_tokens()
        # periodic cleanup / 1% chance
        elif len(token_cache) > (MAX_TOKEN_CACHE_SIZE / 100) and random.random() < 0.01:
            cleanup_old_tokens()

    return True


# ---------------------------------------------------------
def cleanup_old_tokens() -> None:
    """Remove old tokens from cache (must be called while holding token_cache_lock)"""
    current_time = time.time()
    cutoff_time = current_time - abs((2 * MAX_TIMESTAMP_DRIFT) + 300)

    # Remove tokens older than cutoff (safe in-place modification)
    tokens_to_remove = [
        token for token, timestamp in token_cache.items()
        if timestamp <= cutoff_time
    ]

    for token in tokens_to_remove:
        del token_cache[token]

    logger.info(f"Token cache cleaned up: {len(token_cache)} tokens remaining")


# ---------------------------------------------------------
def validate_timestamp(timestamp: int) -> bool:
    """
    Check if timestamp is within acceptable range

    Args:
        timestamp: Unix timestamp to validate

    Returns:
        True if timestamp is fresh, False otherwise
    """
    if MAX_TIMESTAMP_DRIFT <= 0:
        return True  # Check disabled

    current_time = int(time.time())
    time_diff = current_time - timestamp  # Preserve sign

    if time_diff < -MAX_TIMESTAMP_DRIFT:
        logger.warning(f"Timestamp {timestamp} in future: {abs(time_diff)}s ahead")
        return False
    elif time_diff > MAX_TIMESTAMP_DRIFT:
        logger.warning(f"Timestamp {timestamp} too old: {time_diff}s behind")
        return False

    return True


# ---------------------------------------------------------
@app.get("/")
async def root(request: Request):
    """Root endpoint"""
    origin = request.client.host if request.client else "unknown"
    logger.debug(f"Received root endpoint from {origin}")
    return {
        "service": "Qingping Webhook - Validator & MQTT Gateway Service",
        "version": "1.0.1",
        "revision": "$Rev: 4616 $",
        "endpoints": {
            "health": "/health",
            "metrics": "/metrics",
            "qingping data": "/qingPing/device/data",
            "qingping event": "/qingPing/device/event"
        }
    }


# ---------------------------------------------------------
@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint"""
    origin = request.client.host if request.client else "unknown"
    logger.debug(f"Received health check endpoint from {origin}")
    mqtt_connected = await ensure_mqtt_connection()
    return {
        "status": "healthy" if mqtt_connected else "degraded",
        "mqtt_connected": mqtt_connected,
        "app_secret_configured": bool(APP_SECRET),
        "replay_protection_enabled": ENABLE_REPLAY_PROTECTION,
        "timestamp_check_enabled": MAX_TIMESTAMP_DRIFT > 0,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ---------------------------------------------------------
@app.get("/metrics")
async def metrics(request: Request):
    origin = request.client.host if request.client else "unknown"
    logger.debug(f"Received metrics endpoint from {origin}")
    return {
        "webhook_stats": dict(webhook_stats),
        "token_cache_size": len(token_cache),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ---------------------------------------------------------
@app.post("/qingPing/device/data")      # Device Data Webhook
@app.post("/qingPing/device/event")     # Device Event Webhook
async def qingping_webhook(request: Request):
    """Handle incoming Qingping webhook for both data and event types"""
    origin = request.client.host if request.client else "unknown"
    logger.debug(f"Received device endpoint from {origin}")
    try:
        # Qingping expects a response in 3 seconds. Add a timeout to the entire request
        return await asyncio.wait_for(
            _process_webhook(request),
            timeout=2.5  # Leave 0.5s buffer
        )
    except asyncio.TimeoutError:
        logger.error("Processing webhook timed out")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Processing timed out"
        )


# ---------------------------------------------------------
async def _process_webhook(request: Request):
    """
    Handle incoming Qingping webhook
    Validates signature and publishes to MQTT if valid

    SECURITY NOTE: Per Qingping documentation:
    - Must respond within 3 seconds (not 10 as mentioned in old docs)
    - Response code 200 = success, any other code triggers retry
    - Retry schedule: 5min, 15min, 30min intervals over 30 minutes
    - If 10-second timeout ratio > 50%, webhook will be fused for 10 minutes
    """
    start_time = time.perf_counter()
    webhook_stats['total_received'] += 1

    try:
        # Ensure MQTT connection is available before processing (fail fast if broker is down)
        if not await ensure_mqtt_connection():
            logger.error("MQTT broker unavailable before processing webhook")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MQTT unavailable"
            )

        # Parse JSON payload
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            logger.error("Invalid JSON payload")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload"
            )

        # Log the received payload with sensitive fields sanitized
        logger.debug(f"Received qingping webhook: {json.dumps(sanitize_signature_fields(data), indent=2)}")

        # Extract signature components
        signature_obj = data.get('signature')
        if not isinstance(signature_obj, dict):
            logger.error("Missing signature object")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing signature fields"
            )
        timestamp = signature_obj.get('timestamp')
        token = signature_obj.get('token')
        signature = signature_obj.get('signature')
        # Validate presence of required fields
        if timestamp is None or not token or not signature:
            logger.error("Missing required signature fields")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing signature field(s)"
            )
        # validate timestamp format (must be integer)
        try:
            timestamp_int = int(timestamp)
        except (TypeError, ValueError):
            logger.error(f"Timestamp format not valid: {timestamp} not an integer number")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Timestamp format not valid - not an integer number"
            )
        # validate token format (must be 36-character string)
        if not isinstance(token, str) or len(token) != 36:
            logger.error(f"Token format not valid: {token} (must be 36-character string)")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token format not valid - must be 36-character string"
            )

        # 1. Verify timestamp freshness
        if not validate_timestamp(timestamp_int):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Timestamp too old or invalid"
            )

        # 2. Verify signature (before caching token)
        timestamp_for_hmac = str(timestamp_int)
        if not verify_signature(timestamp_for_hmac, token, signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature"
            )
        webhook_stats['signature_valid'] += 1

        # 3. Check for replay attack
        if not await check_replay_attack(token):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Replay attack detected"
            )

        # Validate payload structure before publishing
        payload = data.get('payload', {})
        if not payload:
            logger.error("Missing or empty payload section")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing payload section"
            )
        # Validate expected payload fields based on webhook type
        webhook_type = request.url.path.split("/")[-1]  # 'data' or 'event'
        required_obj_instance = {'info': dict, 'data': list}
        for required_obj in required_obj_instance.keys():
            if required_obj not in payload or not isinstance(payload[required_obj], required_obj_instance[required_obj]):
                logger.error(f"Missing or invalid '{required_obj}' section in payload for {webhook_type} webhook")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid payload structure: missing '{required_obj}'"
                )
            if not payload[required_obj]:
                logger.error(f"'{required_obj}' section is empty in payload for {webhook_type} webhook")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid payload structure: '{required_obj}' cannot be empty"
                )

        # validate mac address format (basic check for presence and length, more complex validation can be added if needed)
        device_mac = payload.get('info', {}).get('mac', 'unknown')
        if not device_mac or not isinstance(device_mac, str) or not re.match(r'^[0-9A-F]{12}$', device_mac, re.IGNORECASE):
            logger.error(f"Invalid device MAC address format: {device_mac}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid device MAC address format"
            )

        # All validations passed - publish to MQTT / Include device MAC in topic for better routing
        topic = MQTT_TOPIC_PREFIX + "/" + device_mac + "/" + webhook_type
        # Ensure MQTT connection before publish (in case it was lost during processing)
        if not mqtt_publisher.is_connected():
            await ensure_mqtt_connection()
        # publish the full payload (including signature) for maximum context in downstream processing
        if mqtt_publisher.publish(topic, data):
            webhook_stats['published_success'] += 1
            processing_time_ms = 1000 * (time.perf_counter() - start_time)
            logger.info(f"Successfully validated and published message for device {device_mac} with {len(payload['data'])} data entries in {processing_time_ms:.3f} ms")
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "success", "message": "Message validated and published"}
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to publish to MQTT"
            )

    except asyncio.TimeoutError:
        logger.error("Processing webhook timed out")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Processing timed out"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


# ---------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=LOG_LEVEL.lower()
    )
