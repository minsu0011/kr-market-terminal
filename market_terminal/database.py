from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, symbol_name TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'virtual', status TEXT NOT NULL DEFAULT 'OPEN',
                    entry_time TEXT NOT NULL, entry_price REAL NOT NULL,
                    entry_quantity INTEGER NOT NULL, entry_amount REAL NOT NULL,
                    entry_fee REAL NOT NULL DEFAULT 0, entry_features TEXT NOT NULL DEFAULT '{}',
                    exit_time TEXT, exit_price REAL, exit_quantity INTEGER, exit_amount REAL,
                    exit_fee REAL DEFAULT 0, tax REAL DEFAULT 0, gross_pnl REAL, net_pnl REAL,
                    return_pct REAL, exit_features TEXT, reason_summary TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feature_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, snapshot_time TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades(symbol, status);
                CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_time ON feature_snapshots(symbol, snapshot_time);

                CREATE TABLE IF NOT EXISTS ai_analysis (
                    id TEXT PRIMARY KEY,
                    request_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT,
                    expires_at TEXT,
                    market_regime TEXT,
                    overall_action TEXT,
                    confidence REAL,
                    data_quality_score INTEGER,
                    input_snapshot_json TEXT NOT NULL,
                    response_json TEXT,
                    human_summary TEXT,
                    applied_feature_order TEXT NOT NULL DEFAULT '[]',
                    applied_alerts TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    is_saved INTEGER NOT NULL DEFAULT 0,
                    price_at_analysis REAL,
                    price_after_5m REAL,
                    price_after_15m REAL,
                    price_after_30m REAL,
                    closing_price REAL,
                    direction_match INTEGER
                );
                CREATE TABLE IF NOT EXISTS ai_feature_rank (
                    analysis_id TEXT NOT NULL,
                    feature_id TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    importance INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    positive_threshold TEXT,
                    caution_threshold TEXT,
                    danger_threshold TEXT,
                    threshold_basis TEXT,
                    explanation TEXT,
                    PRIMARY KEY(analysis_id, feature_id),
                    FOREIGN KEY(analysis_id) REFERENCES ai_analysis(id)
                );
                CREATE TABLE IF NOT EXISTS ai_feature_layout (
                    symbol TEXT PRIMARY KEY,
                    pinned_json TEXT NOT NULL DEFAULT '[]',
                    excluded_json TEXT NOT NULL DEFAULT '[]',
                    ai_order_json TEXT NOT NULL DEFAULT '[]',
                    mode TEXT NOT NULL DEFAULT 'default',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ai_alerts (
                    alert_id TEXT PRIMARY KEY,
                    analysis_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    feature_id TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    threshold REAL,
                    second_threshold REAL,
                    severity TEXT NOT NULL,
                    basis TEXT NOT NULL,
                    explanation TEXT,
                    expires_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    last_triggered_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(analysis_id, feature_id, operator, severity)
                );
                CREATE INDEX IF NOT EXISTS idx_ai_analysis_hash ON ai_analysis(request_hash, expires_at);
                CREATE INDEX IF NOT EXISTS idx_ai_analysis_symbol ON ai_analysis(symbol, requested_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ai_alerts_symbol_active ON ai_alerts(symbol, active, expires_at);

                CREATE TABLE IF NOT EXISTS news_article (
                    article_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL,
                    publisher TEXT, publisher_domain TEXT, title TEXT NOT NULL,
                    url TEXT, published_at TEXT NOT NULL, received_at TEXT NOT NULL,
                    body_status TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_event_cluster (
                    cluster_id TEXT PRIMARY KEY, canonical_title TEXT NOT NULL,
                    event_type TEXT, first_published_at TEXT NOT NULL,
                    last_updated_at TEXT NOT NULL, independent_source_count INTEGER NOT NULL,
                    primary_source_count INTEGER NOT NULL, syndicated_copy_count INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_event_article (
                    cluster_id TEXT NOT NULL, article_id TEXT NOT NULL,
                    PRIMARY KEY(cluster_id,article_id)
                );
                CREATE TABLE IF NOT EXISTS news_fact (
                    cluster_id TEXT NOT NULL, fact_index INTEGER NOT NULL,
                    fact_type TEXT, amount REAL, currency TEXT, confidence REAL,
                    payload_json TEXT NOT NULL, PRIMARY KEY(cluster_id,fact_index)
                );
                CREATE TABLE IF NOT EXISTS news_entity_link (
                    cluster_id TEXT NOT NULL, symbol TEXT NOT NULL,
                    relationship TEXT NOT NULL, confidence REAL NOT NULL,
                    payload_json TEXT NOT NULL, PRIMARY KEY(cluster_id,symbol,relationship)
                );
                CREATE TABLE IF NOT EXISTS news_verification (
                    cluster_id TEXT PRIMARY KEY, verification_status TEXT NOT NULL,
                    decided_at TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_ai_analysis (
                    analysis_id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL,
                    input_json TEXT NOT NULL, response_json TEXT,
                    statistics_available INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS news_feature_link (
                    cluster_id TEXT NOT NULL, symbol TEXT NOT NULL,
                    feature_id TEXT NOT NULL, priority INTEGER NOT NULL,
                    reason TEXT, PRIMARY KEY(cluster_id,symbol,feature_id)
                );
                CREATE TABLE IF NOT EXISTS news_candidate (
                    symbol TEXT NOT NULL, cluster_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT,
                    action TEXT NOT NULL, candidate_score REAL NOT NULL,
                    confidence REAL, verification_status TEXT, materiality_score REAL,
                    sentiment TEXT, price_confirmation INTEGER, volume_confirmation INTEGER,
                    institution_confirmation INTEGER, foreign_confirmation INTEGER,
                    member_confirmation INTEGER, sector_confirmation INTEGER,
                    risk_penalty REAL, active INTEGER NOT NULL DEFAULT 1,
                    removal_reason TEXT, input_snapshot_json TEXT NOT NULL,
                    PRIMARY KEY(symbol,cluster_id)
                );
                CREATE TABLE IF NOT EXISTS news_candidate_confirmation (
                    symbol TEXT NOT NULL, cluster_id TEXT NOT NULL,
                    confirmation_group TEXT NOT NULL, confirmed INTEGER,
                    observed_at TEXT, payload_json TEXT NOT NULL,
                    PRIMARY KEY(symbol,cluster_id,confirmation_group)
                );
                CREATE TABLE IF NOT EXISTS news_provider_status (
                    provider_id TEXT PRIMARY KEY, available INTEGER NOT NULL,
                    state TEXT NOT NULL, message TEXT, checked_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_news_cluster_time ON news_event_cluster(first_published_at DESC);
                CREATE INDEX IF NOT EXISTS idx_news_candidate_active ON news_candidate(active,action,candidate_score DESC);
            """)
            trade_columns = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
            if "side" not in trade_columns:
                conn.execute("ALTER TABLE trades ADD COLUMN side TEXT NOT NULL DEFAULT 'LONG'")
            conn.commit()

    @staticmethod
    def utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("entry_features", "exit_features"):
            if result.get(key):
                try:
                    result[key] = json.loads(result[key])
                except (TypeError, ValueError):
                    result[key] = {}
        return result

    def create_trade(self, p: dict[str, Any]) -> dict[str, Any]:
        now = self.utcnow()
        with self._lock, self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO trades(symbol,symbol_name,mode,side,status,entry_time,entry_price,entry_quantity,
                entry_amount,entry_fee,entry_features,created_at,updated_at)
                VALUES(?,?,?,?,'OPEN',?,?,?,?,?,?,?,?)
            """, (p["symbol"], p["symbol_name"], p.get("mode", "virtual"), p.get("side", "LONG"), p["entry_time"],
                   float(p["entry_price"]), int(p["entry_quantity"]), float(p["entry_amount"]),
                  float(p.get("entry_fee", 0)), json.dumps(p.get("entry_features", {}), ensure_ascii=False), now, now))
            conn.commit()
            return self.get_trade(int(cur.lastrowid)) or {}

    def create_historical_trade(self, p: dict[str, Any]) -> dict[str, Any]:
        now = self.utcnow()
        with self._lock, self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO trades(symbol,symbol_name,mode,side,status,entry_time,entry_price,entry_quantity,
                entry_amount,entry_fee,entry_features,exit_time,exit_price,exit_quantity,exit_amount,
                exit_fee,tax,gross_pnl,net_pnl,return_pct,exit_features,reason_summary,created_at,updated_at)
                VALUES(?,?,'historical',?,'CLOSED',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (p["symbol"], p["symbol_name"], p.get("side", "LONG"), p["entry_time"], float(p["entry_price"]),
                  int(p["entry_quantity"]), float(p["entry_amount"]), float(p.get("entry_fee", 0)),
                  json.dumps(p.get("entry_features", {}), ensure_ascii=False), p["exit_time"],
                  float(p["exit_price"]), int(p["exit_quantity"]), float(p["exit_amount"]),
                  float(p.get("exit_fee", 0)), float(p.get("tax", 0)), float(p["gross_pnl"]),
                  float(p["net_pnl"]), float(p["return_pct"]),
                  json.dumps(p.get("exit_features", {}), ensure_ascii=False), p.get("reason_summary", ""), now, now))
            conn.commit()
            return self.get_trade(int(cur.lastrowid)) or {}

    def close_trade(self, trade_id: int, p: dict[str, Any]) -> dict[str, Any] | None:
        now = self.utcnow()
        with self._lock, self._connect() as conn:
            conn.execute("""
                UPDATE trades SET status='CLOSED',exit_time=?,exit_price=?,exit_quantity=?,exit_amount=?,
                exit_fee=?,tax=?,gross_pnl=?,net_pnl=?,return_pct=?,exit_features=?,reason_summary=?,updated_at=?
                WHERE id=? AND status='OPEN'
            """, (p["exit_time"], float(p["exit_price"]), int(p["exit_quantity"]), float(p["exit_amount"]),
                  float(p.get("exit_fee", 0)), float(p.get("tax", 0)), float(p["gross_pnl"]),
                  float(p["net_pnl"]), float(p["return_pct"]),
                  json.dumps(p.get("exit_features", {}), ensure_ascii=False), p.get("reason_summary", ""), now, trade_id))
            conn.commit()
        return self.get_trade(trade_id)

    def get_trade(self, trade_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._row_to_dict(conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone())

    def list_trades(self, symbol: str | None = None, status: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        clauses, params = [], []
        if symbol:
            clauses.append("symbol=?"); params.append(symbol)
        if status:
            clauses.append("status=?"); params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM trades{where} ORDER BY id DESC LIMIT ?", params).fetchall()
        return [self._row_to_dict(row) or {} for row in rows]

    def delete_trade(self, trade_id: int) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM trades WHERE id=?", (trade_id,)); conn.commit(); return cur.rowcount > 0

    def store_snapshot(self, symbol: str, snapshot_time: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO feature_snapshots(symbol,snapshot_time,payload) VALUES(?,?,?)",
                         (symbol, snapshot_time, json.dumps(payload, ensure_ascii=False)))
            conn.execute("DELETE FROM feature_snapshots WHERE id IN (SELECT id FROM feature_snapshots WHERE symbol=? ORDER BY id DESC LIMIT -1 OFFSET 1000)", (symbol,))
            conn.commit()

    def portfolio_summary(self, seed_money: float) -> dict[str, Any]:
        trades = self.list_trades(limit=10_000)
        closed = [t for t in trades if t.get("status") == "CLOSED"]
        open_trades = [t for t in trades if t.get("status") == "OPEN"]
        realized = sum(float(t.get("net_pnl") or 0) for t in closed)
        wins = sum(float(t.get("net_pnl") or 0) > 0 for t in closed)
        used = sum(float(t.get("entry_amount") or 0) + float(t.get("entry_fee") or 0) for t in open_trades)
        return {"seed_money": seed_money, "cash": seed_money + realized - used, "used_capital": used,
                "realized_pnl": realized, "equity_without_open_pnl": seed_money + realized,
                "closed_count": len(closed), "open_count": len(open_trades), "wins": wins,
                "losses": len(closed) - wins, "win_rate": wins / len(closed) * 100 if closed else 0.0}

    @staticmethod
    def _decode_json_fields(row: sqlite3.Row | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in fields:
            raw = result.get(field)
            if raw:
                try:
                    result[field] = json.loads(raw)
                except (TypeError, ValueError):
                    result[field] = None
        result["is_saved"] = bool(result.get("is_saved"))
        return result

    def save_ai_analysis(
        self,
        input_payload: dict[str, Any],
        response_payload: dict[str, Any],
        *,
        request_hash: str,
        model: str,
        prompt_version: str,
    ) -> dict[str, Any]:
        analysis_id = str(response_payload["analysis_id"])
        now = self.utcnow()
        top_features = response_payload.get("top_features", [])
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO ai_analysis(
                    id,request_type,symbol,model,prompt_version,request_hash,requested_at,
                    completed_at,expires_at,market_regime,overall_action,confidence,
                    data_quality_score,input_snapshot_json,response_json,human_summary,
                    applied_feature_order,applied_alerts,error,is_saved,price_at_analysis
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,
                    COALESCE((SELECT is_saved FROM ai_analysis WHERE id=?),0),?)
            """, (
                analysis_id, input_payload["analysis_type"], input_payload["symbol"]["code"],
                model, prompt_version, request_hash, input_payload["requested_at"], now,
                response_payload["expires_at"], response_payload["market_regime"]["label"],
                response_payload["overall_action"]["code"],
                response_payload["overall_action"]["confidence"],
                response_payload["data_quality"]["score"],
                json.dumps(input_payload, ensure_ascii=False),
                json.dumps(response_payload, ensure_ascii=False),
                response_payload["human_readable_summary"],
                json.dumps(response_payload.get("feature_order", []), ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                analysis_id,
                input_payload.get("price", {}).get("current"),
            ))
            conn.execute("DELETE FROM ai_feature_rank WHERE analysis_id=?", (analysis_id,))
            for feature in top_features:
                thresholds = feature.get("thresholds", {})
                basis = next(
                    (item.get("basis") for item in thresholds.values() if isinstance(item, dict) and item.get("basis")),
                    "UNAVAILABLE",
                )
                conn.execute("""
                    INSERT INTO ai_feature_rank(
                        analysis_id,feature_id,rank,importance,direction,
                        positive_threshold,caution_threshold,danger_threshold,
                        threshold_basis,explanation
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    analysis_id, feature["feature_id"], feature["rank"], feature["importance"],
                    feature["direction_now"],
                    json.dumps(thresholds.get("positive"), ensure_ascii=False),
                    json.dumps(thresholds.get("caution"), ensure_ascii=False),
                    json.dumps(thresholds.get("danger"), ensure_ascii=False),
                    basis, feature.get("reason", ""),
                ))
            conn.commit()
        return self.get_ai_analysis(analysis_id) or {}

    def get_ai_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ai_analysis WHERE id=?", (analysis_id,)).fetchone()
        return self._decode_json_fields(
            row,
            ("input_snapshot_json", "response_json", "applied_feature_order", "applied_alerts"),
        )

    def find_cached_ai_analysis(self, request_hash: str, now_iso: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ai_analysis WHERE request_hash=? AND error IS NULL "
                "AND expires_at>? ORDER BY completed_at DESC LIMIT 1",
                (request_hash, now_iso),
            ).fetchone()
        return self._decode_json_fields(
            row,
            ("input_snapshot_json", "response_json", "applied_feature_order", "applied_alerts"),
        )

    def list_ai_analyses(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM ai_analysis WHERE symbol=? ORDER BY requested_at DESC LIMIT ?",
                    (symbol, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ai_analysis ORDER BY requested_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [
            self._decode_json_fields(
                row, ("input_snapshot_json", "response_json", "applied_feature_order", "applied_alerts")
            ) or {}
            for row in rows
        ]

    def mark_ai_analysis_saved(self, analysis_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE ai_analysis SET is_saved=1 WHERE id=?", (analysis_id,))
            conn.commit()
            return cur.rowcount > 0

    def save_feature_layout(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.utcnow()
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT INTO ai_feature_layout(symbol,pinned_json,excluded_json,ai_order_json,mode,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                  pinned_json=excluded.pinned_json,
                  excluded_json=excluded.excluded_json,
                  ai_order_json=excluded.ai_order_json,
                  mode=excluded.mode,
                  updated_at=excluded.updated_at
            """, (
                payload["symbol"],
                json.dumps(payload.get("pinned", []), ensure_ascii=False),
                json.dumps(payload.get("excluded", []), ensure_ascii=False),
                json.dumps(payload.get("ai_order", []), ensure_ascii=False),
                payload.get("mode", "default"),
                now,
            ))
            conn.commit()
        return self.get_feature_layout(payload["symbol"])

    def get_feature_layout(self, symbol: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ai_feature_layout WHERE symbol=?", (symbol,)).fetchone()
        if row is None:
            return {"symbol": symbol, "pinned": [], "excluded": [], "ai_order": [], "mode": "default"}
        return {
            "symbol": symbol,
            "pinned": json.loads(row["pinned_json"]),
            "excluded": json.loads(row["excluded_json"]),
            "ai_order": json.loads(row["ai_order_json"]),
            "mode": row["mode"],
            "updated_at": row["updated_at"],
        }

    def create_ai_alerts(self, alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = self.utcnow()
        with self._lock, self._connect() as conn:
            for item in alerts:
                conn.execute("""
                    INSERT OR IGNORE INTO ai_alerts(
                        alert_id,analysis_id,symbol,feature_id,operator,threshold,
                        second_threshold,severity,basis,explanation,expires_at,active,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)
                """, (
                    item["alert_id"], item["analysis_id"], item["symbol"], item["feature_id"],
                    item["operator"], item.get("threshold"), item.get("second_threshold"),
                    item["severity"], item["basis"], item.get("explanation", ""),
                    item["expires_at"], now,
                ))
            conn.commit()
        return self.list_ai_alerts(alerts[0]["symbol"] if alerts else None)

    def list_ai_alerts(self, symbol: str | None = None, active_only: bool = False) -> list[dict[str, Any]]:
        clauses, params = [], []
        if symbol:
            clauses.append("symbol=?")
            params.append(symbol)
        if active_only:
            clauses.append("active=1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM ai_alerts{where} ORDER BY created_at DESC", params).fetchall()
        return [{**dict(row), "active": bool(row["active"])} for row in rows]

    def clear_ai_alerts(self, symbol: str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE ai_alerts SET active=0 WHERE symbol=? AND active=1", (symbol,))
            conn.commit()
            return cur.rowcount

    def expire_ai_alerts(self, now_iso: str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("UPDATE ai_alerts SET active=0 WHERE active=1 AND expires_at<=?", (now_iso,))
            conn.commit()
            return cur.rowcount

    def mark_ai_alert_triggered(self, alert_id: str, trigger_time: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT last_triggered_at FROM ai_alerts WHERE alert_id=?", (alert_id,)).fetchone()
            if row and row["last_triggered_at"]:
                try:
                    last = datetime.fromisoformat(row["last_triggered_at"])
                    current = datetime.fromisoformat(trigger_time)
                    if (current - last).total_seconds() < 60:
                        return False
                except ValueError:
                    pass
            cur = conn.execute(
                "UPDATE ai_alerts SET last_triggered_at=? WHERE alert_id=? AND active=1",
                (trigger_time, alert_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def save_news_pipeline_state(
        self,
        articles: list[Any],
        clusters: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        provider_statuses: list[dict[str, Any]],
    ) -> None:
        """Persist only observed news facts and deterministic candidate state."""
        now = self.utcnow()

        def payload(value: Any) -> dict[str, Any]:
            return asdict(value) if is_dataclass(value) else dict(value)

        with self._lock, self._connect() as conn:
            for raw in articles:
                item = payload(raw)
                conn.execute("""
                    INSERT OR REPLACE INTO news_article(
                        article_id,provider_id,publisher,publisher_domain,title,url,
                        published_at,received_at,body_status,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    item["article_id"], item["provider_id"], item.get("publisher"),
                    item.get("publisher_domain"), item["title"], item.get("url"),
                    item["published_at"], item["received_at"],
                    "COLLECTED" if item.get("body") else "UNAVAILABLE",
                    json.dumps(item, ensure_ascii=False),
                ))
            for cluster in clusters:
                cluster_payload = dict(cluster)
                conn.execute("""
                    INSERT OR REPLACE INTO news_event_cluster(
                        cluster_id,canonical_title,event_type,first_published_at,last_updated_at,
                        independent_source_count,primary_source_count,syndicated_copy_count,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                """, (
                    cluster["cluster_id"], cluster["canonical_title"], cluster.get("event_type"),
                    cluster["first_published_at"], cluster["last_updated_at"],
                    int(cluster.get("independent_source_count", 0)),
                    int(cluster.get("primary_source_count", 0)),
                    int(cluster.get("syndicated_copy_count", 0)),
                    json.dumps(cluster_payload, ensure_ascii=False),
                ))
                conn.execute("DELETE FROM news_event_article WHERE cluster_id=?", (cluster["cluster_id"],))
                for article_id in cluster.get("article_ids", []):
                    conn.execute(
                        "INSERT OR IGNORE INTO news_event_article(cluster_id,article_id) VALUES(?,?)",
                        (cluster["cluster_id"], article_id),
                    )
                conn.execute("DELETE FROM news_fact WHERE cluster_id=?", (cluster["cluster_id"],))
                for index, fact in enumerate(cluster.get("facts", [])):
                    conn.execute("""
                        INSERT INTO news_fact(cluster_id,fact_index,fact_type,amount,currency,confidence,payload_json)
                        VALUES(?,?,?,?,?,?,?)
                    """, (
                        cluster["cluster_id"], index, fact.get("fact_type"), fact.get("amount"),
                        fact.get("currency"), fact.get("confidence"),
                        json.dumps(fact, ensure_ascii=False),
                    ))
                conn.execute("DELETE FROM news_entity_link WHERE cluster_id=?", (cluster["cluster_id"],))
                for entity in cluster.get("entities", []):
                    conn.execute("""
                        INSERT OR REPLACE INTO news_entity_link(
                            cluster_id,symbol,relationship,confidence,payload_json
                        ) VALUES(?,?,?,?,?)
                    """, (
                        cluster["cluster_id"], entity["symbol"], entity.get("relationship", "UNKNOWN"),
                        float(entity.get("confidence", 0)),
                        json.dumps(entity, ensure_ascii=False),
                    ))
                verification = cluster.get("verification")
                if verification:
                    conn.execute("""
                        INSERT OR REPLACE INTO news_verification(
                            cluster_id,verification_status,decided_at,payload_json
                        ) VALUES(?,?,?,?)
                    """, (
                        cluster["cluster_id"], verification["status"], now,
                        json.dumps(verification, ensure_ascii=False),
                    ))
            for candidate in candidates:
                verification = candidate.get("verification", {})
                materiality = candidate.get("materiality", {})
                sentiment = candidate.get("sentiment", {})
                reaction = candidate.get("price_volume_reaction", {})
                flow = candidate.get("flow_confirmation", {})
                breakdown = candidate.get("score_breakdown", {})
                created = now
                expires = (
                    datetime.fromisoformat(candidate["news_time"]).astimezone(timezone.utc) + timedelta(hours=6)
                ).isoformat()
                conn.execute("""
                    INSERT OR REPLACE INTO news_candidate(
                        symbol,cluster_id,created_at,expires_at,action,candidate_score,confidence,
                        verification_status,materiality_score,sentiment,price_confirmation,
                        volume_confirmation,institution_confirmation,foreign_confirmation,
                        member_confirmation,sector_confirmation,risk_penalty,active,
                        removal_reason,input_snapshot_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    candidate["symbol"], candidate["cluster_id"], created, expires,
                    candidate["action"], float(candidate["candidate_score"]), None,
                    verification.get("status"), materiality.get("score"), sentiment.get("sentiment"),
                    reaction.get("price_confirmation"), reaction.get("volume_confirmation"),
                    flow.get("institution_confirmation"), flow.get("foreign_confirmation"),
                    flow.get("member_confirmation"), None, breakdown.get("risk_penalty", 0),
                    1 if candidate["action"] != "NO_TRADE" else 0,
                    "; ".join(candidate.get("blocking_reasons", [])) or None,
                    json.dumps(candidate, ensure_ascii=False),
                ))
                conn.execute(
                    "DELETE FROM news_candidate_confirmation WHERE symbol=? AND cluster_id=?",
                    (candidate["symbol"], candidate["cluster_id"]),
                )
                group_values = {
                    "PRICE": reaction.get("price_confirmation"),
                    "VOLUME": reaction.get("volume_confirmation"),
                    "INSTITUTION": flow.get("institution_confirmation"),
                    "FOREIGN": flow.get("foreign_confirmation"),
                    "MEMBER_DESK": flow.get("member_confirmation"),
                }
                for group, confirmed in group_values.items():
                    conn.execute("""
                        INSERT INTO news_candidate_confirmation(
                            symbol,cluster_id,confirmation_group,confirmed,observed_at,payload_json
                        ) VALUES(?,?,?,?,?,?)
                    """, (
                        candidate["symbol"], candidate["cluster_id"], group, confirmed, now,
                        json.dumps({"group": group, "confirmed": confirmed}, ensure_ascii=False),
                    ))
            for status in provider_statuses:
                conn.execute("""
                    INSERT OR REPLACE INTO news_provider_status(
                        provider_id,available,state,message,checked_at,payload_json
                    ) VALUES(?,?,?,?,?,?)
                """, (
                    status["provider_id"], int(bool(status["available"])), status["state"],
                    status.get("message"), status.get("checked_at", now),
                    json.dumps(status, ensure_ascii=False),
                ))
            conn.commit()
