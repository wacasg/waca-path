"""[DATA-FRESHNESS] 空表の理由を区別する注記のテスト (2026-09-03)。

背景:
  上流の日次バッチが破壊的リビルドの途中で止まり、micro_user_table が数か月前の
  月ぶんだけになった。UI は「対象期間にユーザーが見つかりません」としか出さず、
  絞り込みの問題との区別が付かないまま半日以上放置された (2026-09-02 実観測)。

本 test が守る不変条件:
  1. 鮮度取得 SQL が micro_user_table の MAX(event_date) を読むこと。
  2. identifier 検証を通っていること (injection 防止)。
  3. 鮮度取得が失敗しても例外を投げず None を返すこと (一覧表示を壊さない)。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from services.user_explorer import build_max_event_date_sql, max_event_date  # noqa: E402


def test_sql_reads_max_event_date_from_micro_user_table():
    sql = build_max_event_date_sql("proj", "ds")
    assert "MAX(event_date)" in sql
    assert "`proj.ds.micro_user_table`" in sql


def test_sql_rejects_invalid_identifiers():
    """dataset 名は検証済みのものしか埋め込まない。"""
    with pytest.raises(Exception):
        build_max_event_date_sql("proj", "ds`; DROP TABLE x --")


def test_lookup_failure_returns_none_instead_of_raising(monkeypatch):
    """鮮度注記は補助情報。取得に失敗しても一覧表示まで巻き添えにしない。"""

    def _boom(project):
        raise RuntimeError("bigquery unavailable")

    monkeypatch.setattr("services.user_explorer._client", _boom)

    assert asyncio.run(max_event_date(project="proj", dataset="ds")) is None
