"""Small, centralised product identifiers for the desktop application."""

from __future__ import annotations


APP_NAME = "科研助手"
APP_VERSION = "13.1.3"
MIN_SUPPORTED_DATA_VERSION = "10.0"

# v0.7.7/v0.7.8 portable builds used the original channel.  A new channel lets
# the installer launch once beside that legacy instance, so the user can import
# data and close the old copy instead of silently activating it again.
INSTANCE_CHANNEL = "v2"
