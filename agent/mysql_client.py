"""MySQL 数据库工具，供 UI 自动化测试场景内部调用。

典型用途：
  - 查询验证码（绕过短信网关）
  - 校验业务侧落库结果
  - 测试前清理脏数据

示例：
    from agent import mysql_client as db

    # 查单值（拿验证码）
    code = db.get_value(
        "SELECT code FROM sms_log WHERE phone=%s ORDER BY id DESC LIMIT 1",
        ["13800138000"],
        database="sms",
    )

    # 查单行
    user = db.query_one(
        "SELECT id, phone, status FROM users WHERE phone=%s",
        ["13800138000"],
        database="user_center",
    )

    # 查多行
    result = db.query("SELECT * FROM orders WHERE user_id=%s LIMIT 10", [123])
    for row in result:
        print(row)

    # 写操作（DELETE / UPDATE / INSERT 都走 execute）
    affected = db.execute(
        "DELETE FROM tmp_test_data WHERE create_time<%s",
        ["2025-01-01"],
        database="test",
    )
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import pymysql
from pymysql.cursors import DictCursor

from agent.config import Config


# ── 连接管理 ────────────────────────────────────────────

@contextmanager
def get_connection(database: str | None = None) -> Iterator[pymysql.connections.Connection]:
    """获取 MySQL 短连接，with 块结束后自动关闭。

    Args:
        database: 切换到指定 schema；不传则使用 Config.MYSQL_DATABASE。
    """
    conn = pymysql.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=database or Config.MYSQL_DATABASE or None,
        charset=Config.MYSQL_CHARSET,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
        autocommit=False,
        cursorclass=DictCursor,
    )
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── 查询 ────────────────────────────────────────────────

def query(
    sql: str,
    params: Sequence[Any] | None = None,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """执行 SELECT，返回所有行（字典列表）。"""
    with get_connection(database) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params or ()))
            rows = cur.fetchall()
    return [_json_safe_row(r) for r in rows]


def query_one(
    sql: str,
    params: Sequence[Any] | None = None,
    database: str | None = None,
) -> dict[str, Any] | None:
    """执行 SELECT，仅返回第一行；无结果返回 None。"""
    with get_connection(database) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params or ()))
            row = cur.fetchone()
    return _json_safe_row(row) if row else None


def get_value(
    sql: str,
    params: Sequence[Any] | None = None,
    database: str | None = None,
    default: Any = None,
) -> Any:
    """执行 SELECT 并返回第一行第一列；常用于取验证码、ID 等单值。"""
    row = query_one(sql, params, database)
    if not row:
        return default
    return next(iter(row.values()))


# ── 写操作 ──────────────────────────────────────────────

def execute(
    sql: str,
    params: Sequence[Any] | None = None,
    database: str | None = None,
) -> int:
    """执行 INSERT / UPDATE / DELETE，返回受影响行数（自动 commit）。"""
    with get_connection(database) as conn:
        with conn.cursor() as cur:
            affected = cur.execute(sql, tuple(params or ()))
        conn.commit()
    return affected


def execute_many(
    sql: str,
    params_list: Sequence[Sequence[Any]],
    database: str | None = None,
) -> int:
    """批量执行同一 SQL（如批量插入），返回总受影响行数。"""
    with get_connection(database) as conn:
        with conn.cursor() as cur:
            affected = cur.executemany(sql, [tuple(p) for p in params_list])
        conn.commit()
    return affected


# ── 便捷方法 ────────────────────────────────────────────

def get_draw_identity_verify_code(
    phone: str,
    database: str | None = None,
) -> int | None:
    """取乐享借提现身份验证码 (event_code = e_draw_identity_verify_code)。

    数据表结构：
      cns.p_notice_record.params 是 JSON 字符串，嵌套结构为
        {"eventCode": ..., "params": {"code": "6010", ...}, "reqNo": ...}
      需要提取 outer.params.code 并转 int 作为短信输入。

    Args:
        phone: 手机号 (字符串)，在 SQL 里使用 MD5() 匹配 cis.u_user.mobile_no_md5。
        database: 可选，不传则使用默认 schema（SQL 里已带 cns./cis. 全限定名，一般不需要传）。

    Returns:
        int 验证码；查不到或解析失败返回 None。
    """
    sql = (
        "SELECT params FROM cns.p_notice_record "
        "WHERE user_no = ("
        "  SELECT user_no FROM cis.u_user WHERE mobile_no_md5 = MD5(%s)"
        ") "
        "AND event_code = 'e_draw_identity_verify_code' "
        "ORDER BY id DESC LIMIT 1"
    )
    raw = get_value(sql, [str(phone)], database=database)
    return _extract_code_from_params(raw)


def _extract_code_from_params(raw: Any) -> int | None:
    """从 p_notice_record.params 字段中提取 int 型验证码。

    兼容：
      - raw 为 JSON 字符串 -> 先 json.loads
      - raw 已是 dict
      - code 可能位于 outer.params.code 或 outer.code
    """
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        outer = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(outer, dict):
            return None
        inner = outer.get("params")
        code = None
        if isinstance(inner, dict):
            code = inner.get("code")
        if code is None:
            code = outer.get("code")
        if code is None or code == "":
            return None
        return int(str(code).strip())
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def clean_unsettled_loans(
    cust_no: str,
    settled_state: str = "DS",
    target_state: str = "DJ",
    database: str | None = None,
) -> int:
    """将指定客户的未结清借款状态置为 DJ（拒绝/作废）。

    场景：借款场景测试完成后，清洗测试帐号产生的借据，
    避免干扰下一轮测试。已结清 (loan_state='DS') 的记录不动。

    Args:
        cust_no: 客户号（lps.bm_iou.cust_no）。
        settled_state: 表示已结清的状态值，默认 'DS'；该状态记录不动。
        target_state: 要设置的目标状态，默认 'DJ'。
        database: 可选 schema；SQL 里已带 lps. 全限定名，一般不需要传。

    Returns:
        受影响的记录数。

    Raises:
        ValueError: cust_no 为空（防止误操作）。
    """
    if not cust_no or not str(cust_no).strip():
        raise ValueError("cust_no 不能为空，拒绝执行全表扫描更新")

    sql = (
        "UPDATE lps.bm_iou t SET t.loan_state = %s "
        "WHERE cust_no = %s AND loan_state != %s"
    )
    return execute(sql, [target_state, str(cust_no).strip(), settled_state], database=database)


def ping() -> dict[str, Any]:
    """连通性自检，返回服务器版本和时间。"""
    row = query_one("SELECT VERSION() AS version, NOW() AS server_time")
    return {
        "ok": True,
        "host": Config.MYSQL_HOST,
        "port": Config.MYSQL_PORT,
        **(row or {}),
    }


# ── 工具 ────────────────────────────────────────────────

def _json_safe_row(row: dict | None) -> dict | None:
    """把 datetime / Decimal / bytes 等转成可 JSON 序列化的形式。"""
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, bytes):
            try:
                out[k] = v.decode("utf-8")
            except Exception:
                out[k] = v.hex()
        else:
            out[k] = str(v)
    return out
