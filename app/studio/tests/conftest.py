"""Session-wide test env: one temp data dir, executor off.

Must run before any test module imports `studio.core.db`, because DB_PATH is
computed at import time. Individual modules still set their own env for other
flags, but the data dir is fixed here.
"""

from __future__ import annotations

import os
import tempfile

os.environ["STUDIO_DATA"] = tempfile.mkdtemp(prefix="studio-tests-")
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
os.environ["STUDIO_ALLOW_RENDER"] = "0"
# 测试直接调接口，不走登录守卫（登录本身有独立用例）
os.environ["STUDIO_AUTH"] = "0"
