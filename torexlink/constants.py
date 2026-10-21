"""Application-wide constants.

Values that affect interoperability or security have an explicit version.
Never change them without a protocol/format migration.
"""

from __future__ import annotations

APP_NAME = "ToRexLink"
APP_VERSION = "1.0.11"
APP_BUNDLE_ID = "uz.torexlink.app"
PROTOCOL_VERSION = 1

# Network
DEFAULT_PORT = 48_620
PORT_SCAN_COUNT = 10
SERVICE_TYPE = "_torexlink._tcp.local."
SERVICE_NAME = "ToRexLink"
MAX_FRAME_BYTES = 8 * 1024 * 1024
HANDSHAKE_TIMEOUT = 12.0
CONNECT_TIMEOUT = 8.0
IO_TIMEOUT = 45.0
PING_INTERVAL = 10.0
PING_TIMEOUT = 32.0
RECONNECT_DELAYS = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)
RECONNECT_JITTER = 0.25

# Pairing
PAIRING_CODE_DIGITS = 6
PAIRING_TTL_SECONDS = 180.0
PAIRING_MAX_ATTEMPTS = 5
PAIRING_FAILURE_DELAY = 1.0
PAIRING_PAYLOAD_PREFIX = "torexlink://pair"

# Clipboard
CLIPBOARD_MAX_BYTES = 1024 * 1024
CLIPBOARD_POLL_SECONDS = 0.45
CLIPBOARD_MIN_INTERVAL = 0.12

# Input
HOTKEY_LABEL = "Ctrl+Alt+Space"
HOTKEY_KEYS = frozenset({"ctrl", "alt", "space"})
EDGES = ("left", "right", "top", "bottom")
OPPOSITE_EDGE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
INPUT_MOVE_SEND_INTERVAL = 0.004
INPUT_QUEUE_MAX = 2048
SCREEN_EDGE_MARGIN_PX = 2
EDGE_REARM_MARGIN_PX = 56
REMOTE_RETURN_REARM_MARGIN_PX = 60
REMOTE_RETURN_PUSH_PX = 72.0
REMOTE_RETURN_EDGE_DWELL_SECONDS = 0.3
REMOTE_RETURN_SETTLE_SECONDS = 0.6
EVENT_COOLDOWN_SECONDS = 0.35
LOCAL_MOTION_TAKEOVER = 0.004
LOCAL_MOTION_WINDOW_SECONDS = 0.5

# Focus arbitration
FOCUS_ACTIONS = frozenset({"toggle", "edge", "takeover", "return", "follow"})
FOCUS_REASONS = frozenset({"hotkey", "edge", "takeover", "remote-return", "initial"})

# UI
DEFAULT_LOCALE = "uz"
SUPPORTED_LOCALES = ("uz", "ru", "en")
LOCALE_NAMES = {"uz": "O‘zbekcha", "ru": "Русский", "en": "English"}
LOCALES = SUPPORTED_LOCALES

# Security / certificates
TLS_MIN_VERSION = "TLSv1.3"
CERT_VALIDITY_DAYS = 3650
CERT_RENEW_BEFORE_DAYS = 180
MAX_CERT_PEM_BYTES = 24 * 1024
TOKEN_BYTES = 32
NONCE_BYTES = 32
CPACE_DOMAIN = b"ToRexLink-CPace-P256-v1"
PAIR_PROOF_LABEL = b"ToRexLink-Pair-Proof-v1"
AUTH_LABEL_CLIENT = b"ToRexLink-Auth-Client-v1"
AUTH_LABEL_SERVER = b"ToRexLink-Auth-Server-v1"
PAIR_TOKEN_INFO = b"ToRexLink-Pair-Token-v1"
CPACE_KEY_INFO = b"ToRexLink-CPace-Key-v1"

# Files and runtime
SETTINGS_VERSION = 1
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_SHARE_MAX_BYTES = 256 * 1024
LOG_SHARE_TIMEOUT_SECONDS = 10.0
LOG_SHARE_COOLDOWN_SECONDS = 2.0
REMOTE_LOGS_MIN_VERSION = "1.0.5"
KEYBOARD_FOLLOW_MIN_VERSION = "1.0.8"
APP_EXIT_TIMEOUT = 6.0

# Limit untrusted network requests
PAIR_CHALLENGE_MAX_BYTES = 64 * 1024
MAX_PEERS = 32
MAX_DEVICE_NAME = 64
