"""SQLite persistence for the bench — scans, device enrichment and GATT.

Why this exists: the three scan backends keep everything in memory and the CLI
throws it away at exit (bar a one-off CSV). The web/MCP front-end needs the recon
data to *survive* a session and be queryable by address across scans, so this
module is the single durable store behind all of it.

Design choices:
  - **stdlib `sqlite3` only** — the "lightweight DB" the maintainer asked for, with
    zero new native dependency (unlike anything under blatann). The file lives at
    ``db/intotheblue.db`` (git-ignored); override with ``$INTOTHEBLUE_DB`` or a
    ``Store(path)`` argument (tests use ``":memory:"``).
  - **No blatann import.** Kept deliberately free of the dongle driver so the read
    side (Devices page, MCP queries) and headless tests load without hardware.
  - **"Latest enrichment wins", non-destructively.** A device is upserted by
    address: a newer scan fills blanks and refreshes values but never overwrites a
    known field with an empty one (different backends see different subsets — the
    dongle decodes Fast Pair model ids, BlueZ knows host bond state, etc.).
  - Records from any backend (`scan_live.DeviceRecord`, `scan_le.LeRecord`,
    `scan_classic.ClassicRecord`) are read with ``getattr`` defaults, so one schema
    covers all three. Addresses are normalised (drop blatann's ",r"/",p" suffix,
    upper-case) so the same device merges across backends.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from types import SimpleNamespace

import oui

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "intotheblue.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_session (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    note       TEXT
);

CREATE TABLE IF NOT EXISTS device (
    address      TEXT PRIMARY KEY,
    transport    TEXT,
    addr_type    TEXT,
    name         TEXT,
    manufacturer TEXT,
    protocol     TEXT,
    fast_pair    TEXT,
    connectable  INTEGER,          -- 1 yes / 0 no / NULL unknown
    tx_power     INTEGER,
    directed     INTEGER DEFAULT 0,
    paired       INTEGER DEFAULT 0,
    fp_payload   TEXT,             -- raw 0xFE2C payload (hex) for bond decoding
    cod          INTEGER,
    cod_label    TEXT,
    icon         TEXT,
    vendor_oui   TEXT,
    rssi         INTEGER,          -- most recent
    first_seen   REAL,
    last_seen    REAL
);

CREATE TABLE IF NOT EXISTS advertised_service (
    address TEXT NOT NULL,
    uuid    TEXT NOT NULL,
    name    TEXT,
    UNIQUE(address, uuid)
);

CREATE TABLE IF NOT EXISTS sighting (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    address    TEXT NOT NULL,
    rssi       INTEGER,
    count      INTEGER,
    seen_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gatt_service (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    address       TEXT NOT NULL,
    session_id    INTEGER,
    uuid          TEXT NOT NULL,
    name          TEXT,
    discovered_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS gatt_characteristic (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id    INTEGER NOT NULL REFERENCES gatt_service(id) ON DELETE CASCADE,
    uuid          TEXT NOT NULL,
    name          TEXT,
    props         TEXT,
    value_hex     TEXT,
    value_interp  TEXT
);

-- Followed BLE connections (Sniffle conn-follow, Phase 3b). A *new shape*, not a
-- sighting: one row per followed connection keyed by its access address (the central
-- picks a fresh random AA per connection, so AA identifies the connection instance).
-- Holding central (InitA, the phone) and peripheral (AdvA, the device) on one row is
-- the persisted phone<->device relationship. The peripheral address joins back to the
-- device table so the Devices view can show what a device was seen connected to.
CREATE TABLE IF NOT EXISTS connection (
    aa              INTEGER PRIMARY KEY,
    session_id      INTEGER,
    central         TEXT NOT NULL,
    central_type    TEXT,
    peripheral      TEXT NOT NULL,
    peripheral_type TEXT,
    peripheral_name TEXT,
    interval_ms     REAL,
    latency         INTEGER,
    timeout_ms      REAL,
    channels        INTEGER,
    encrypted       INTEGER DEFAULT 0,   -- 1 once LL_ENC_REQ/START_ENC seen (sticky)
    terminated      INTEGER DEFAULT 0,   -- 1 once LL_TERMINATE_IND seen (sticky)
    pkt_central     INTEGER,
    pkt_peripheral  INTEGER,
    rssi            INTEGER,
    first_seen      REAL,
    last_seen       REAL
);

CREATE INDEX IF NOT EXISTS idx_sighting_addr ON sighting(address);
CREATE INDEX IF NOT EXISTS idx_advsvc_addr   ON advertised_service(address);
CREATE INDEX IF NOT EXISTS idx_gattsvc_addr  ON gatt_service(address);
CREATE INDEX IF NOT EXISTS idx_conn_periph   ON connection(peripheral);
CREATE INDEX IF NOT EXISTS idx_conn_central  ON connection(central);
"""

# Device columns in the order the merge/insert uses (address handled separately).
_DEVICE_COLS = [
    "transport", "addr_type", "name", "manufacturer", "protocol", "fast_pair",
    "connectable", "tx_power", "directed", "paired", "fp_payload", "cod",
    "cod_label", "icon", "vendor_oui", "rssi", "first_seen", "last_seen",
]


def normalize_address(address: str) -> str:
    """Canonical device key: drop blatann's ",r"/",p" suffix and upper-case."""
    return (address or "").split(",")[0].strip().upper()


def _as_bool_int(value) -> int | None:
    """Map a tri-state connectable (True/False/None) to 1/0/NULL."""
    if value is None:
        return None
    return 1 if value else 0


def record_fields(rec, transport: str) -> dict:
    """Flatten any backend record into the ``device`` column set.

    Uses ``getattr`` with defaults so a single schema covers the BLE dongle, BlueZ
    LE and Classic record classes, which each carry a different subset of fields.
    """
    address = normalize_address(getattr(rec, "address", ""))
    fp_payload = getattr(rec, "fp_payload", b"") or b""
    addr_type = getattr(rec, "addr_type", "") or ""
    # Vendor from the IEEE OUI is only meaningful for a public address (a random
    # one carries no OUI) — mirrors exposure.assess, never guesses from bytes.
    vendor = oui.lookup(address) if addr_type == "pub" else ""
    now = time.time()
    return {
        "address": address,
        "transport": transport,
        "addr_type": addr_type,
        "name": getattr(rec, "name", "") or "",
        "manufacturer": getattr(rec, "manufacturer", "") or "",
        "protocol": getattr(rec, "protocol", "") or "",
        "fast_pair": getattr(rec, "fast_pair", "") or "",
        "connectable": _as_bool_int(getattr(rec, "connectable", None)),
        "tx_power": getattr(rec, "tx_power", None),
        "directed": 1 if getattr(rec, "directed", False) else 0,
        "paired": 1 if getattr(rec, "paired", False) else 0,
        "fp_payload": fp_payload.hex() if fp_payload else "",
        "cod": getattr(rec, "cod", None),
        "cod_label": getattr(rec, "cod_label", "") or "",
        "icon": getattr(rec, "icon", "") or "",
        "vendor_oui": vendor or "",
        "rssi": getattr(rec, "rssi", 0) or 0,
        "first_seen": getattr(rec, "first_seen", now),
        "last_seen": getattr(rec, "last_seen", now),
    }


def row_to_record(row: sqlite3.Row | dict) -> SimpleNamespace:
    """Rebuild a record-like object from a ``device`` row.

    Gives the passive analysers (`exposure.assess`, `bond.signals`,
    `scan_fused.target_note`) the attribute surface they expect — ``services`` as a
    set and ``fp_payload`` back as bytes — so the DB read side reuses them unchanged.
    """
    d = dict(row)
    conn = d.get("connectable")
    try:
        payload = bytes.fromhex(d.get("fp_payload") or "")
    except ValueError:
        payload = b""
    return SimpleNamespace(
        address=d.get("address", ""),
        transport=d.get("transport", ""),
        addr_type=d.get("addr_type", "") or "",
        name=d.get("name", "") or "",
        manufacturer=d.get("manufacturer", "") or "",
        protocol=d.get("protocol", "") or "",
        fast_pair=d.get("fast_pair", "") or "",
        connectable=None if conn is None else bool(conn),
        tx_power=d.get("tx_power"),
        directed=bool(d.get("directed")),
        paired=bool(d.get("paired")),
        fp_payload=payload,
        cod=d.get("cod"),
        cod_label=d.get("cod_label", "") or "",
        icon=d.get("icon", "") or "",
        vendor_oui=d.get("vendor_oui", "") or "",
        rssi=d.get("rssi", 0) or 0,
        services=set(),  # filled by get_device from advertised_service
        first_seen=d.get("first_seen"),
        last_seen=d.get("last_seen"),
    )


# Connection columns in insert order (aa handled separately as the key).
_CONN_COLS = [
    "session_id", "central", "central_type", "peripheral", "peripheral_type",
    "peripheral_name", "interval_ms", "latency", "timeout_ms", "channels",
    "encrypted", "terminated", "pkt_central", "pkt_peripheral", "rssi",
    "first_seen", "last_seen",
]


def connection_fields(rec, session_id: int | None = None) -> dict:
    """Flatten a `sniff.ConnectionRecord` into the ``connection`` column set.

    Reads attributes with ``getattr`` defaults (no blatann, no sniff import) and
    normalises both addresses so the peripheral joins back to the ``device`` row.
    """
    return {
        "aa": int(getattr(rec, "aa", 0)),
        "session_id": session_id,
        "central": normalize_address(getattr(rec, "central", "")),
        "central_type": getattr(rec, "central_type", "") or "",
        "peripheral": normalize_address(getattr(rec, "peripheral", "")),
        "peripheral_type": getattr(rec, "peripheral_type", "") or "",
        "peripheral_name": getattr(rec, "peripheral_name", "") or "",
        "interval_ms": getattr(rec, "interval_ms", 0.0),
        "latency": getattr(rec, "latency", 0),
        "timeout_ms": getattr(rec, "timeout_ms", 0.0),
        "channels": getattr(rec, "channels", 0),
        "encrypted": 1 if getattr(rec, "encrypted", False) else 0,
        "terminated": 1 if getattr(rec, "terminated", False) else 0,
        "pkt_central": getattr(rec, "pkt_central", 0),
        "pkt_peripheral": getattr(rec, "pkt_peripheral", 0),
        "rssi": getattr(rec, "rssi", 0),
        "first_seen": getattr(rec, "first_seen", None),
        "last_seen": getattr(rec, "last_seen", None),
    }


def row_to_connection(row: sqlite3.Row | dict) -> SimpleNamespace:
    """Rebuild a `sniff.ConnectionRecord`-shaped object from a DB row.

    Attribute names match `sniff.ConnectionRecord` (and thus `sniff.CONN_COLUMNS` cell
    functions), so the stored read side renders exactly like the live table — with
    ``encrypted``/``terminated`` back as bools and no in-memory ``packets`` buffer.
    """
    d = dict(row)
    return SimpleNamespace(
        aa=d.get("aa", 0),
        central=d.get("central", ""),
        central_type=d.get("central_type", ""),
        peripheral=d.get("peripheral", ""),
        peripheral_type=d.get("peripheral_type", ""),
        peripheral_name=d.get("peripheral_name", "") or "",
        interval_ms=d.get("interval_ms") or 0.0,
        latency=d.get("latency") or 0,
        timeout_ms=d.get("timeout_ms") or 0.0,
        channels=d.get("channels") or 0,
        encrypted=bool(d.get("encrypted")),
        terminated=bool(d.get("terminated")),
        pkt_central=d.get("pkt_central") or 0,
        pkt_peripheral=d.get("pkt_peripheral") or 0,
        rssi=d.get("rssi") or 0,
        first_seen=d.get("first_seen"),
        last_seen=d.get("last_seen"),
    )


class Store:
    """Thread-safe SQLite wrapper (one shared connection, guarded by a lock).

    A single ``check_same_thread=False`` connection is shared between the engine's
    background flush thread and the request handlers; every statement runs under one
    lock, which is ample for the bench's write volume and avoids per-thread
    connection bookkeeping.
    """

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("INTOTHEBLUE_DB") or _DEFAULT_PATH
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- sessions ----------------------------------------------------------
    def open_session(self, kind: str, note: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO scan_session (kind, started_at, note) VALUES (?, ?, ?)",
                (kind, time.time(), note),
            )
            self._conn.commit()
            return cur.lastrowid

    def close_session(self, session_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scan_session SET ended_at = ? WHERE id = ?",
                (time.time(), session_id),
            )
            self._conn.commit()

    def list_sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, "
                "  (SELECT COUNT(DISTINCT address) FROM sighting WHERE session_id = s.id) AS devices "
                "FROM scan_session s ORDER BY s.started_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- device upsert -----------------------------------------------------
    def upsert_device(self, rec, transport: str, session_id: int | None = None) -> str:
        """Merge a scan record into ``device`` (latest non-empty wins) and record a
        sighting. Also stores advertised service UUIDs. Returns the normalised address."""
        f = record_fields(rec, transport)
        address = f["address"]
        if not address:
            return ""
        services = {str(s) for s in getattr(rec, "services", set()) or set()}
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM device WHERE address = ?", (address,)
            ).fetchone()
            merged = self._merge(dict(existing) if existing else None, f)
            cols = ["address"] + _DEVICE_COLS
            placeholders = ", ".join("?" for _ in cols)
            updates = ", ".join(f"{c} = excluded.{c}" for c in _DEVICE_COLS)
            self._conn.execute(
                f"INSERT INTO device ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON CONFLICT(address) DO UPDATE SET {updates}",
                [merged[c] for c in cols],
            )
            for uuid in services:
                self._conn.execute(
                    "INSERT OR IGNORE INTO advertised_service (address, uuid, name) "
                    "VALUES (?, ?, ?)",
                    (address, uuid, ""),
                )
            self._conn.execute(
                "INSERT INTO sighting (session_id, address, rssi, count, seen_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, address, f["rssi"], getattr(rec, "count", 0), time.time()),
            )
            self._conn.commit()
        return address

    @staticmethod
    def _merge(existing: dict | None, new: dict) -> dict:
        """Combine an existing device row with a fresh scan's fields.

        A new value wins only when it carries information: an empty string or a
        ``None`` connectable never clobbers a known value. ``first_seen`` keeps the
        earliest, ``last_seen`` the latest.
        """
        if existing is None:
            return dict(new)
        merged = dict(existing)
        for col in _DEVICE_COLS:
            nv = new.get(col)
            if col == "first_seen":
                merged[col] = min(x for x in (merged.get(col), nv) if x is not None)
            elif col == "last_seen":
                merged[col] = max(x for x in (merged.get(col), nv) if x is not None)
            elif col in ("connectable", "tx_power", "cod"):
                if nv is not None:
                    merged[col] = nv
            elif col in ("directed", "paired"):
                merged[col] = 1 if (nv or merged.get(col)) else 0   # sticky
            elif col == "fast_pair":
                # A decoded Model ID (dongle) must win over a bare "Yes" (BlueZ),
                # same precedence as scan_fused._merge_fast_pair — otherwise a later
                # BlueZ sighting would mask the very thing the target is named by.
                cur = merged.get(col) or ""
                if nv and nv != "Yes":
                    merged[col] = nv
                elif nv and not cur:
                    merged[col] = nv
            elif nv not in (None, "", 0):
                merged[col] = nv
            elif col == "rssi" and nv:
                merged[col] = nv
        merged["address"] = new["address"]
        return merged

    # --- connections (Sniffle conn-follow) ---------------------------------
    def upsert_connection(self, rec, session_id: int | None = None) -> int:
        """Persist a followed connection, keyed by access address. Returns the AA.

        Re-upserting the same live connection (the flush thread does this repeatedly)
        refreshes the counters/last_seen and keeps ``encrypted``/``terminated`` sticky
        and ``first_seen`` earliest — so one followed link stays one row."""
        f = connection_fields(rec, session_id)
        if not f["aa"] or not f["central"] or not f["peripheral"]:
            return 0
        cols = ["aa"] + _CONN_COLS
        placeholders = ", ".join("?" for _ in cols)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO connection ({', '.join(cols)}) VALUES ({placeholders}) "
                "ON CONFLICT(aa) DO UPDATE SET "
                "  session_id = excluded.session_id, "
                "  central = excluded.central, central_type = excluded.central_type, "
                "  peripheral = excluded.peripheral, "
                "  peripheral_type = excluded.peripheral_type, "
                # keep a name once learned (a later snapshot may have it blank)
                "  peripheral_name = CASE WHEN excluded.peripheral_name != '' "
                "    THEN excluded.peripheral_name ELSE connection.peripheral_name END, "
                "  interval_ms = excluded.interval_ms, latency = excluded.latency, "
                "  timeout_ms = excluded.timeout_ms, channels = excluded.channels, "
                "  encrypted = MAX(connection.encrypted, excluded.encrypted), "
                "  terminated = MAX(connection.terminated, excluded.terminated), "
                "  pkt_central = excluded.pkt_central, "
                "  pkt_peripheral = excluded.pkt_peripheral, rssi = excluded.rssi, "
                "  first_seen = MIN(connection.first_seen, excluded.first_seen), "
                "  last_seen = MAX(connection.last_seen, excluded.last_seen)",
                [f[c] for c in cols],
            )
            self._conn.commit()
        return f["aa"]

    def list_connections(self, peripheral: str | None = None,
                         session_id: int | None = None,
                         since: float | None = None) -> list[dict]:
        """Persisted connections, most recent first. Optional non-destructive filters:
        a specific peripheral address, a scan session, or a ``last_seen`` floor."""
        clauses, params = [], []
        if peripheral:
            clauses.append("peripheral = ?")
            params.append(normalize_address(peripheral))
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("last_seen >= ?")
            params.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM connection{where} ORDER BY last_seen DESC", params
            ).fetchall()
        return [dict(r) for r in rows]

    # --- GATT --------------------------------------------------------------
    def save_gatt(self, address: str, tree: list[dict], session_id: int | None = None) -> None:
        """Replace the stored GATT tree for a device (latest discovery wins).

        ``tree`` is a list of ``{uuid, name, characteristics: [{uuid, name, props,
        value_hex, value_interp}]}`` dicts. The old tree is deleted (characteristics
        cascade) before inserting the new one.
        """
        address = normalize_address(address)
        now = time.time()
        with self._lock:
            old = self._conn.execute(
                "SELECT id FROM gatt_service WHERE address = ?", (address,)
            ).fetchall()
            for r in old:
                self._conn.execute("DELETE FROM gatt_service WHERE id = ?", (r["id"],))
            for svc in tree:
                cur = self._conn.execute(
                    "INSERT INTO gatt_service (address, session_id, uuid, name, discovered_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (address, session_id, str(svc.get("uuid", "")), svc.get("name", ""), now),
                )
                svc_id = cur.lastrowid
                for ch in svc.get("characteristics", []):
                    self._conn.execute(
                        "INSERT INTO gatt_characteristic "
                        "(service_id, uuid, name, props, value_hex, value_interp) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (svc_id, str(ch.get("uuid", "")), ch.get("name", ""),
                         ch.get("props", ""), ch.get("value_hex", ""), ch.get("value_interp", "")),
                    )
            self._conn.commit()

    def set_name(self, address: str, name: str, only_if_empty: bool = True) -> None:
        """Set a device's name (e.g. the GATT Device Name read on a browse).

        By default only fills a blank name, so a GATT name backfills a device that
        advertised none without ever overwriting a real advertised local name.
        """
        address = normalize_address(address)
        if not name:
            return
        sql = "UPDATE device SET name = ? WHERE address = ?"
        if only_if_empty:
            sql += " AND (name IS NULL OR name = '')"
        with self._lock:
            self._conn.execute(sql, (name, address))
            self._conn.commit()

    def update_char_value(self, address: str, char_uuid: str,
                          value_hex: str, value_interp: str) -> None:
        """Update one characteristic's cached value in the stored GATT tree.

        Used by an on-demand read in an interaction session (`engine.gatt_read`): the
        tree structure is already persisted, so only the value cells change. Matches
        on the characteristic UUID across the device's services (UUIDs are unique
        enough per device for this cache); no-op if the tree has not been stored yet.
        """
        address = normalize_address(address)
        target = str(char_uuid).lower()
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.uuid FROM gatt_characteristic c "
                "JOIN gatt_service s ON c.service_id = s.id WHERE s.address = ?",
                (address,),
            ).fetchall()
            for r in rows:
                if str(r["uuid"]).lower() == target:
                    self._conn.execute(
                        "UPDATE gatt_characteristic SET value_hex = ?, value_interp = ? "
                        "WHERE id = ?",
                        (value_hex, value_interp, r["id"]),
                    )
            self._conn.commit()

    def get_gatt(self, address: str) -> list[dict]:
        address = normalize_address(address)
        with self._lock:
            svcs = self._conn.execute(
                "SELECT * FROM gatt_service WHERE address = ? ORDER BY id", (address,)
            ).fetchall()
            tree = []
            for s in svcs:
                chars = self._conn.execute(
                    "SELECT * FROM gatt_characteristic WHERE service_id = ? ORDER BY id",
                    (s["id"],),
                ).fetchall()
                node = dict(s)
                node["characteristics"] = [dict(c) for c in chars]
                tree.append(node)
        return tree

    # --- read side ---------------------------------------------------------
    def list_devices(self, search: str | None = None, transport: str | None = None,
                     addr_type: str | None = None, fast_pair_only: bool = False,
                     since: float | None = None, session_id: int | None = None) -> list[dict]:
        """All persisted devices, newest sighting first, with optional filters.

        ``since`` keeps only devices whose ``last_seen`` is at or after that epoch (the
        "seen recently" window — the store keeps every device forever, this just scopes
        the view). ``session_id`` keeps only devices with a sighting in that scan session
        (the "current/last scan" lens). Both are non-destructive; use ``prune_devices`` to
        actually drop old history.
        """
        clauses, params = [], []
        if search:
            clauses.append("(name LIKE ? OR address LIKE ? OR manufacturer LIKE ? OR vendor_oui LIKE ?)")
            like = f"%{search}%"
            params += [like, like, like, like]
        if transport:
            clauses.append("transport = ?")
            params.append(transport)
        if addr_type:
            clauses.append("addr_type = ?")
            params.append(addr_type)
        if fast_pair_only:
            clauses.append("fast_pair != ''")
        if since is not None:
            clauses.append("last_seen >= ?")
            params.append(since)
        if session_id is not None:
            clauses.append("address IN (SELECT DISTINCT address FROM sighting WHERE session_id = ?)")
            params.append(session_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM device{where} ORDER BY last_seen DESC", params
            ).fetchall()
        return [dict(r) for r in rows]

    def prune_devices(self, before: float) -> int:
        """Delete devices whose ``last_seen`` is older than ``before`` (epoch), with all
        their dependent rows (sightings, advertised services, GATT tree). Destructive —
        frees history the recency/session filters only hide. Returns the count removed.

        FK cascade isn't relied on (it needs ``PRAGMA foreign_keys`` on), so every
        dependent table is cleared explicitly for the pruned addresses.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT address FROM device WHERE last_seen IS NOT NULL AND last_seen < ?",
                (before,),
            ).fetchall()
            addresses = [r["address"] for r in rows]
            if not addresses:
                return 0
            marks = ", ".join("?" for _ in addresses)
            self._conn.execute(
                f"DELETE FROM gatt_characteristic WHERE service_id IN "
                f"(SELECT id FROM gatt_service WHERE address IN ({marks}))",
                addresses,
            )
            for table in ("gatt_service", "advertised_service", "sighting", "device"):
                self._conn.execute(
                    f"DELETE FROM {table} WHERE address IN ({marks})", addresses
                )
            self._conn.commit()
        return len(addresses)

    def get_device(self, address: str) -> dict | None:
        """Full device view: enrichment + advertised services + GATT + sighting stats."""
        address = normalize_address(address)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM device WHERE address = ?", (address,)
            ).fetchone()
            if row is None:
                return None
            svcs = self._conn.execute(
                "SELECT uuid, name FROM advertised_service WHERE address = ? ORDER BY uuid",
                (address,),
            ).fetchall()
        device = dict(row)
        device["advertised_services"] = [dict(s) for s in svcs]
        device["gatt"] = self.get_gatt(address)
        device["history"] = self.device_history(address)
        return device

    def device_history(self, address: str, limit: int = 200) -> list[dict]:
        """Sighting history (RSSI trend) for a device, oldest first."""
        address = normalize_address(address)
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, rssi, count, seen_at FROM sighting "
                "WHERE address = ? ORDER BY seen_at DESC LIMIT ?",
                (address, limit),
            ).fetchall()
        return list(reversed([dict(r) for r in rows]))


# Module-level default instance, created lazily so importing the module never
# touches the filesystem until something actually persists or reads.
_default: Store | None = None
_default_lock = threading.Lock()


def get_store() -> Store:
    """The shared default `Store` (path from ``$INTOTHEBLUE_DB`` or ``db/``)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = Store()
        return _default
