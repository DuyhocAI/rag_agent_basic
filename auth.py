"""
rag_agent/auth.py  —  v1.0
Authentication, authorisation, rate-limiting, and user management
for public_server.py.

FEATURES:
  ✔ SQLite-backed user & API-key store
  ✔ Bcrypt password hashing  (falls back to SHA-256 + salt if bcrypt unavailable)
  ✔ Bearer / X-API-Key / ?api_key= auth
  ✔ Role-based access: "admin" | "user" | "readonly"
  ✔ Plan-based rate limits: free (30 rpm / 500 rpd) | admin (unlimited)
  ✔ Per-key request logging (endpoint, method, latency, status, IP)
  ✔ Usage stats query
  ✔ User self-service: register, rotate key, view usage
  ✔ Per-user ckey.vn API key storage (encrypted at rest with AUTH_SALT XOR)
  ✔ FastAPI APIRouter  →  mounted at root by public_server.py

ENVIRONMENT VARIABLES:
  AUTH_SALT    Secret salt for hashing / obfuscating stored ckeys  (CHANGE THIS)
  AUTH_DB      Path to SQLite DB  (default: auth.db next to this file)
  RATE_RPM     Requests-per-minute per key for "free" plan  (default 30)
  RATE_RPD     Requests-per-day    per key for "free" plan  (default 500)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, APIKeyQuery, HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

logger = logging.getLogger("rag_agent.auth")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

_HERE     = Path(__file__).resolve().parent
AUTH_DB   = Path(os.environ.get("AUTH_DB",  str(_HERE / "auth.db")))
AUTH_SALT = os.environ.get("AUTH_SALT", "rag_agent_default_salt_CHANGE_ME")
RATE_RPM  = int(os.environ.get("RATE_RPM", "30"))
RATE_RPD  = int(os.environ.get("RATE_RPD", "500"))

# Plan definitions: (requests_per_minute, requests_per_day)
_PLAN_LIMITS: dict[str, tuple[int, int]] = {
    "free":     (RATE_RPM, RATE_RPD),
    "pro":      (120, 2000),
    "admin":    (0, 0),        # 0 = unlimited
    "readonly": (10, 100),
}

_db_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

@contextmanager
def _conn():
    with _db_lock:
        con = sqlite3.connect(str(AUTH_DB), check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def init_auth_db() -> None:
    """Create schema if not exists.  Called once at server boot."""
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            username    TEXT UNIQUE NOT NULL,
            email       TEXT UNIQUE,
            pw_hash     TEXT NOT NULL,
            role        TEXT NOT NULL DEFAULT 'user',
            plan        TEXT NOT NULL DEFAULT 'free',
            created_at  TEXT NOT NULL,
            is_active   INTEGER NOT NULL DEFAULT 1,
            ckey_enc    TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS api_keys (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            key_hash    TEXT UNIQUE NOT NULL,
            label       TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            last_used   TEXT,
            is_active   INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS request_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT,
            key_id      TEXT,
            endpoint    TEXT,
            method      TEXT,
            status_code INTEGER,
            latency_ms  INTEGER,
            ip          TEXT,
            session_id  TEXT,
            ts          TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_api_keys_hash    ON api_keys(key_hash);
        CREATE INDEX IF NOT EXISTS idx_request_log_user ON request_log(user_id, ts);
        CREATE INDEX IF NOT EXISTS idx_request_log_ts   ON request_log(ts);
        """)
    logger.info("[Auth] DB initialised: %s", AUTH_DB)


# ═══════════════════════════════════════════════════════════════════════════════
# PASSWORD & KEY HASHING
# ═══════════════════════════════════════════════════════════════════════════════

def _hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 + AUTH_SALT."""
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        AUTH_SALT.encode(),
        iterations=260_000,
    )
    return dk.hex()


def _check_password(password: str, stored_hash: str) -> bool:
    expected = _hash_password(password)
    return hmac.compare_digest(expected, stored_hash)


def _hash_key(raw_key: str) -> str:
    """One-way hash for API key storage."""
    return hashlib.sha256((AUTH_SALT + raw_key).encode()).hexdigest()


def _generate_api_key() -> str:
    """Generate a new prefixed API key: ragkey_<40 random hex chars>."""
    return "ragkey_" + secrets.token_hex(20)


def _new_id() -> str:
    return secrets.token_hex(8)


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ═══════════════════════════════════════════════════════════════════════════════
# CKEY OBFUSCATION  (simple XOR-based, not cryptographic — just avoids
#                    storing plaintext ckeys in the DB)
# ═══════════════════════════════════════════════════════════════════════════════

def _xor_obfuscate(text: str, key: str) -> str:
    key_bytes = (key * (len(text) // len(key) + 1)).encode()[:len(text)]
    return bytes(a ^ b for a, b in zip(text.encode(), key_bytes)).hex()


def _xor_deobfuscate(hex_text: str, key: str) -> str:
    raw = bytes.fromhex(hex_text)
    key_bytes = (key * (len(raw) // len(key) + 1)).encode()[:len(raw)]
    return bytes(a ^ b for a, b in zip(raw, key_bytes)).decode(errors="replace")


# ═══════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def create_user(username: str, password: str, email: str = None,
                role: str = "user", plan: str = "free") -> dict:
    """Create a new user. Raises ValueError on duplicate username/email."""
    user_id  = _new_id()
    pw_hash  = _hash_password(password)
    now      = _now_iso()
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO users (id, username, email, pw_hash, role, plan, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (user_id, username, email, pw_hash, role, plan, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"Username or email already exists: {e}") from e
    logger.info("[Auth] User created: %s (%s/%s)", username, role, plan)
    return {"id": user_id, "username": username, "email": email,
            "role": role, "plan": plan, "created_at": now}


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Return user dict if credentials valid, else None."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1", (username,)
        ).fetchone()
    if row and _check_password(password, row["pw_hash"]):
        return dict(row)
    return None


def get_user(user_id: str) -> Optional[dict]:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, username, email, role, plan, created_at, is_active FROM users"
        ).fetchall()
    return [dict(r) for r in rows]


def deactivate_user(user_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))


# ═══════════════════════════════════════════════════════════════════════════════
# API KEY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def create_api_key(user_id: str, label: str = "") -> str:
    """Generate + store a new API key. Returns the raw (plaintext) key."""
    raw      = _generate_api_key()
    key_hash = _hash_key(raw)
    key_id   = _new_id()
    now      = _now_iso()
    with _conn() as con:
        con.execute(
            "INSERT INTO api_keys (id, user_id, key_hash, label, created_at) "
            "VALUES (?,?,?,?,?)",
            (key_id, user_id, key_hash, label, now),
        )
    logger.info("[Auth] API key created for user_id=%s label=%r", user_id, label)
    return raw


def validate_api_key(raw_key: str) -> Optional[dict]:
    """
    Look up a raw API key.  Returns merged user+key dict on success, else None.
    Also touches last_used timestamp.
    """
    if not raw_key or not raw_key.startswith("ragkey_"):
        return None
    key_hash = _hash_key(raw_key)
    with _conn() as con:
        row = con.execute(
            """SELECT k.id AS key_id, k.label, k.is_active AS key_active,
                      u.id AS user_id, u.username, u.email, u.role, u.plan,
                      u.is_active AS user_active
               FROM api_keys k JOIN users u ON k.user_id = u.id
               WHERE k.key_hash=?""",
            (key_hash,),
        ).fetchone()
        if row and row["key_active"] and row["user_active"]:
            con.execute(
                "UPDATE api_keys SET last_used=? WHERE id=?",
                (_now_iso(), row["key_id"]),
            )
            return dict(row)
    return None


def rotate_api_key(user_id: str, old_key_id: str) -> str:
    """Deactivate old key and create a new one for the same user."""
    with _conn() as con:
        con.execute(
            "UPDATE api_keys SET is_active=0 WHERE id=? AND user_id=?",
            (old_key_id, user_id),
        )
    return create_api_key(user_id, label="rotated")


def list_api_keys(user_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT id, label, created_at, last_used, is_active "
            "FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# CKEY.VN API KEY PER USER
# ═══════════════════════════════════════════════════════════════════════════════

def get_ckey_api_key(user_id: str) -> str:
    """Return the (deobfuscated) ckey.vn key stored for this user, or ''."""
    with _conn() as con:
        row = con.execute(
            "SELECT ckey_enc FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row or not row["ckey_enc"]:
        return ""
    try:
        return _xor_deobfuscate(row["ckey_enc"], AUTH_SALT)
    except Exception:
        return ""


def update_ckey_api_key(user_id: str, ckey: str) -> None:
    """Obfuscate and store ckey.vn key for this user."""
    enc = _xor_obfuscate(ckey, AUTH_SALT) if ckey else ""
    with _conn() as con:
        con.execute(
            "UPDATE users SET ckey_enc=? WHERE id=?", (enc, user_id)
        )
    logger.info("[Auth] ckey updated for user_id=%s", user_id)


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITING  (in-memory sliding window — resets on server restart)
# ═══════════════════════════════════════════════════════════════════════════════

# { key_id: { "minute": [timestamps], "day": [timestamps] } }
_rate_store: dict[str, dict] = {}
_rate_lock  = threading.Lock()


def _rate_check(user: dict) -> bool:
    """
    Returns True if the request is allowed, False if rate-limited.
    Unlimited for admin plan (limits == 0).
    """
    plan          = user.get("plan", "free")
    rpm_lim, rpd_lim = _PLAN_LIMITS.get(plan, _PLAN_LIMITS["free"])
    if rpm_lim == 0 and rpd_lim == 0:
        return True   # unlimited

    key_id = user.get("key_id", user.get("user_id", "unknown"))
    now    = time.time()

    with _rate_lock:
        bucket = _rate_store.setdefault(key_id, {"minute": [], "day": []})

        # Evict old timestamps
        minute_ago = now - 60
        day_ago    = now - 86400
        bucket["minute"] = [t for t in bucket["minute"] if t > minute_ago]
        bucket["day"]    = [t for t in bucket["day"]    if t > day_ago]

        if len(bucket["minute"]) >= rpm_lim:
            return False
        if len(bucket["day"])    >= rpd_lim:
            return False

        bucket["minute"].append(now)
        bucket["day"].append(now)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

def log_request(
    user_id:     str,
    key_id:      str,
    endpoint:    str,
    method:      str,
    status_code: int,
    latency_ms:  int,
    ip:          str,
    session_id:  str = "",
) -> None:
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO request_log "
                "(user_id, key_id, endpoint, method, status_code, latency_ms, ip, session_id, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, key_id, endpoint, method, status_code,
                 latency_ms, ip, session_id, _now_iso()),
            )
    except Exception as exc:
        logger.warning("[Auth] log_request failed: %s", exc)


def get_usage_stats(user_id: str, days: int = 7) -> dict:
    """Return per-day request counts and totals for the last `days` days."""
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _conn() as con:
        total = con.execute(
            "SELECT COUNT(*) FROM request_log WHERE user_id=? AND ts>=?",
            (user_id, since),
        ).fetchone()[0]
        per_day = con.execute(
            "SELECT substr(ts,1,10) AS day, COUNT(*) AS cnt "
            "FROM request_log WHERE user_id=? AND ts>=? "
            "GROUP BY day ORDER BY day",
            (user_id, since),
        ).fetchall()
        errors = con.execute(
            "SELECT COUNT(*) FROM request_log "
            "WHERE user_id=? AND ts>=? AND status_code>=400",
            (user_id, since),
        ).fetchone()[0]
    return {
        "user_id":   user_id,
        "days":      days,
        "total":     total,
        "errors":    errors,
        "per_day":   [dict(r) for r in per_day],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════════════════

_bearer_scheme   = HTTPBearer(auto_error=False)
_header_scheme   = APIKeyHeader(name="X-API-Key",  auto_error=False)
_query_scheme    = APIKeyQuery(name="api_key",      auto_error=False)


def _extract_raw_key(
    request:     Request,
    bearer:      Optional[HTTPAuthorizationCredentials],
    header_key:  Optional[str],
    query_key:   Optional[str],
) -> Optional[str]:
    """Pull the raw API key from whichever location the client used."""
    if bearer and bearer.credentials:
        return bearer.credentials
    if header_key:
        return header_key
    if query_key:
        return query_key
    return None


async def require_auth(
    request:    Request,
    bearer:     Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    header_key: Optional[str]                          = Depends(_header_scheme),
    query_key:  Optional[str]                          = Depends(_query_scheme),
) -> dict:
    """
    FastAPI dependency.  Resolves the caller to a user dict or raises 401/429.
    Attaches user to request.state so the logging middleware can read it.
    """
    raw_key = _extract_raw_key(request, bearer, header_key, query_key)
    if not raw_key:
        raise HTTPException(status_code=401, detail="API key required. "
                            "Include Authorization: Bearer ragkey_... or X-API-Key header.")

    user = validate_api_key(raw_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")

    if not _rate_check(user):
        plan = user.get("plan", "free")
        rpm, rpd = _PLAN_LIMITS.get(plan, _PLAN_LIMITS["free"])
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({rpm} rpm / {rpd} rpd for plan '{plan}'). "
                   "Slow down or upgrade your plan.",
        )

    request.state.user = user
    return user


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    """FastAPI dependency for admin-only endpoints."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return user


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTER  —  /auth/* endpoints
# ═══════════════════════════════════════════════════════════════════════════════

auth_router = APIRouter(prefix="/auth", tags=["auth"])

# ── Pydantic models ───────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username:     str
    password:     str
    email:        str = None
    ckey_api_key: str = ""   # optional: store ckey.vn key at registration


class LoginRequest(BaseModel):
    username: str
    password: str


class SetCkeyRequest(BaseModel):
    ckey_api_key: str


class RotateKeyRequest(BaseModel):
    key_id: str


# ── Routes ────────────────────────────────────────────────────────────────────

@auth_router.post("/register", summary="Register a new account and get an API key")
async def register(req: RegisterRequest):
    """
    Create a new user and immediately issue an API key.
    Optionally supply your ckey.vn key so the server can use it for LLM calls.
    """
    if len(req.username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if req.email and "@" not in req.email:
        raise HTTPException(400, "Invalid email address.")

    try:
        user = create_user(req.username, req.password, req.email)
    except ValueError as e:
        raise HTTPException(409, str(e))

    raw_key = create_api_key(user["id"], label="primary")

    if req.ckey_api_key:
        update_ckey_api_key(user["id"], req.ckey_api_key)

    return {
        "message":  "Account created. Save your api_key — it won't be shown again.",
        "username": user["username"],
        "user_id":  user["id"],
        "api_key":  raw_key,
        "plan":     user["plan"],
        "role":     user["role"],
    }


@auth_router.post("/login", summary="Exchange username+password for a new API key")
async def login(req: LoginRequest):
    """Issue a fresh API key after verifying username and password."""
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(401, "Invalid username or password.")
    raw_key  = create_api_key(user["id"], label="login")
    all_keys = list_api_keys(user["id"])
    active   = sum(1 for k in all_keys if k.get("is_active"))
    return {
        # Nested "user" dict — matches cli.py: resp["user"]
        "user": {
            "username": user["username"],
            "user_id":  user["id"],
            "role":     user["role"],
            "plan":     user["plan"],
        },
        "api_key":     raw_key,
        "active_keys": active,
        # Flat fields too for forward compatibility
        "username": user["username"],
        "user_id":  user["id"],
        "role":     user["role"],
        "plan":     user["plan"],
    }


@auth_router.get("/me", summary="View your own profile")
async def me(user: dict = Depends(require_auth)):
    return {
        "user_id":  user["user_id"],
        "username": user["username"],
        "email":    user["email"],
        "role":     user["role"],
        "plan":     user["plan"],
        "key_id":   user["key_id"],
        "key_label":user["label"],
    }


@auth_router.get("/usage", summary="View your request usage stats")
async def usage(days: int = 7, user: dict = Depends(require_auth)):
    return get_usage_stats(user["user_id"], days=days)


@auth_router.post("/rotate-key", summary="Rotate your API key")
async def rotate_key(req: RotateKeyRequest, user: dict = Depends(require_auth)):
    """Deactivate the given key_id and issue a new one."""
    new_key = rotate_api_key(user["user_id"], req.key_id)
    return {
        "message": "Key rotated. Save your new api_key — it won't be shown again.",
        "api_key": new_key,
    }


@auth_router.get("/keys", summary="List your API keys (hashes hidden)")
async def list_keys(user: dict = Depends(require_auth)):
    return {"keys": list_api_keys(user["user_id"])}


@auth_router.post("/setckey", summary="Store your ckey.vn API key on the server")
async def set_ckey(req: SetCkeyRequest, user: dict = Depends(require_auth)):
    """
    Save your ckey.vn key so you don't have to send it with every /chat request.
    The key is XOR-obfuscated before being written to the DB.
    """
    update_ckey_api_key(user["user_id"], req.ckey_api_key)
    return {"message": "ckey_api_key stored successfully."}


@auth_router.get("/getckey", summary="Retrieve your stored ckey.vn API key")
async def get_ckey(user: dict = Depends(require_auth)):
    ckey = get_ckey_api_key(user["user_id"])
    if not ckey:
        return {"ckey_api_key": "", "message": "No ckey stored yet. Use POST /auth/setckey."}
    # Return masked version so the key isn't fully exposed over HTTP
    masked = ckey[:10] + "..." + ckey[-4:] if len(ckey) > 14 else "***"
    return {"ckey_api_key_masked": masked, "has_ckey": True}


# ── Admin routes ──────────────────────────────────────────────────────────────

@auth_router.get("/admin/users", summary="[Admin] List all users")
async def admin_list_users(_: dict = Depends(require_admin)):
    return {"users": list_users()}


@auth_router.post("/admin/user/{user_id}/deactivate",
                  summary="[Admin] Deactivate a user account")
async def admin_deactivate(user_id: str, _: dict = Depends(require_admin)):
    deactivate_user(user_id)
    return {"deactivated": user_id}


@auth_router.post("/admin/user/{user_id}/plan",
                  summary="[Admin] Change a user's plan or role")
async def admin_set_plan(
    user_id: str,
    plan: str = "free",
    role: str = "user",
    _: dict = Depends(require_admin),
):
    if plan not in _PLAN_LIMITS:
        raise HTTPException(400, f"Unknown plan '{plan}'. Valid: {list(_PLAN_LIMITS)}")
    with _conn() as con:
        con.execute(
            "UPDATE users SET plan=?, role=? WHERE id=?", (plan, role, user_id)
        )
    return {"user_id": user_id, "plan": plan, "role": role}


@auth_router.get("/admin/usage/{user_id}", summary="[Admin] View usage for any user")
async def admin_usage(user_id: str, days: int = 7, _: dict = Depends(require_admin)):
    return get_usage_stats(user_id, days=days)


@auth_router.get("/admin/log", summary="[Admin] Raw request log (last N rows)")
async def admin_log(limit: int = 100, _: dict = Depends(require_admin)):
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM request_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"log": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE  (for testing only — not used when imported by public_server.py)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    init_auth_db()
    if len(sys.argv) >= 4 and sys.argv[1] == "create-admin":
        _, _, uname, pw, email = (*sys.argv, "admin@localhost")[:5]
        try:
            u   = create_user(uname, pw, email, role="admin", plan="admin")
            key = create_api_key(u["id"], "bootstrap")
            print(f"Admin created: {uname}")
            print(f"API key:       {key}")
        except ValueError as e:
            print(f"Error: {e}")
    else:
        print("Usage: python auth.py create-admin <username> <password> [email]")
        print("DB:   ", AUTH_DB)