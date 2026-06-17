import pytest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session
from app.core.database import engine

# Import domain models to satisfy SQLAlchemy mapper dependencies in tests
import app.domains.journal.models  # noqa: F401
import app.domains.streams.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.auth.models  # noqa: F401
import app.domains.users.models  # noqa: F401

from app.domains.users.models import User
from app.domains.accounts.models import TradingAccount, Trade, TradeDirection, TradeSession, TradingAccountType, TradingPlatform, TradingAccountConnectionState
from app.domains.journal.models import TagCategory, TagOption, TradeTagSelection
from app.domains.journal.repository import list_trade_setups

def test_list_trade_setups_sql_query() -> None:
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        # Create user
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        # Create trading account
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        # Create trade
        trade = Trade(
            id=uuid.uuid4(),
            account_id=account.id,
            broker_trade_id=f"trade-{uuid.uuid4()}",
            symbol="EURUSD",
            direction=TradeDirection.buy,
            open_price=Decimal("1.0"),
            close_price=Decimal("1.1"),
            volume=Decimal("0.1"),
            profit=Decimal("10.0"),
            commission=Decimal("0.0"),
            swap=Decimal("0.0"),
            net_profit=Decimal("10.0"),
            duration_seconds=60,
            session=TradeSession.london,
            opened_at=datetime.now(timezone.utc),
            closed_at=datetime.now(timezone.utc),
            is_missed=False,
            is_manual=False,
        )
        db.add(trade)
        
        # Create tag category
        category = TagCategory(
            id=uuid.uuid4(),
            user_id=user.id,
            title="Strategy",
            is_system=False,
        )
        db.add(category)
        
        # Create tag option
        option = TagOption(
            id=uuid.uuid4(),
            category_id=category.id,
            user_id=user.id,
            value="Breakout",
        )
        db.add(option)
        db.flush()
        
        # Create selection
        selection = TradeTagSelection(
            trade_id=trade.id,
            option_id=option.id,
        )
        db.add(selection)
        db.flush()
        
        # Call list_trade_setups
        results = list_trade_setups(
            db,
            account_id=account.id,
            closed_from_utc=None,
            closed_to_utc_exclusive=None,
            include_manual=True,
        )
        
        assert len(results) == 1
        assert results[0][0] == "breakout"
        assert results[0][1] == 1  # trade_count
        assert results[0][2] == 1  # win_count
        assert results[0][3] == Decimal("10.0")  # total_pnl
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_is_account_sync_locked() -> None:
    """The sync mutex is a transaction-scoped advisory lock (pg_try_advisory_xact_lock):
    visible across connections while the holding transaction is open, mutually
    exclusive, and auto-released when that transaction ends — no manual unlock."""
    from app.domains.accounts.repository import (
        try_acquire_account_sync_lock,
        is_account_sync_locked,
    )

    account_id = uuid.uuid4()

    # Connection A holds the lock; connection B is an independent observer that
    # stands in for another worker process / pooled server connection.
    conn_a = engine.connect()
    db_a = Session(bind=conn_a)
    conn_b = engine.connect()
    db_b = Session(bind=conn_b)
    try:
        # Initially nothing holds the lock.
        assert is_account_sync_locked(db_b, account_id) is False

        # A acquires the xact lock (attaches to A's open transaction).
        assert try_acquire_account_sync_lock(db_a, account_id) is True

        # Visible from a separate connection while A's transaction stays open.
        assert is_account_sync_locked(db_b, account_id) is True

        # A second acquirer on another connection cannot take the same lock.
        assert try_acquire_account_sync_lock(db_b, account_id) is False

        # Ending A's transaction auto-releases the lock — no explicit unlock call.
        db_a.rollback()
        assert is_account_sync_locked(db_b, account_id) is False
    finally:
        db_a.close()
        conn_a.close()
        db_b.close()
        conn_b.close()
        connection.close()


def test_refresh_token_grace_window() -> None:
    from app.domains.auth.service import refresh_access_token
    from app.domains.auth import repository as token_repo
    from app.domains.auth.models import TokenType
    from app.core.security import hash_token
    from fastapi import Response
    from datetime import timedelta
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        # Create user
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        # Create a refresh token
        raw_refresh = str(uuid.uuid4())
        hashed_refresh = hash_token(raw_refresh)
        token_repo.create(
            db,
            user_id=user.id,
            hashed_token=hashed_refresh,
            token_type=TokenType.REFRESH,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        db.flush()
        
        # Call refresh_access_token (first request - winner)
        res1 = Response()
        refresh_res1 = refresh_access_token(db, raw_refresh, res1)
        assert refresh_res1.access_token is not None
        
        # Verify first response has Set-Cookie for the new refresh token
        cookies1 = res1.headers.getlist("set-cookie")
        assert any("refresh_token=" in c for c in cookies1)
        
        # Call refresh_access_token again with same old token (second request - loser, inside grace window)
        res2 = Response()
        refresh_res2 = refresh_access_token(db, raw_refresh, res2)
        assert refresh_res2.access_token is not None
        
        # Verify second response does NOT have Set-Cookie (grace path does not rotate refresh token)
        cookies2 = res2.headers.getlist("set-cookie")
        assert not any("refresh_token=" in c for c in cookies2)
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_bulk_upsert_closed_trades_db() -> None:
    from app.domains.accounts.repository import bulk_upsert_closed_trades
    from app.domains.accounts.models import AccountSnapshot
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        # Define trade rows to upsert
        rows = [
            {
                "id": uuid.uuid4(),
                "account_id": account.id,
                "broker_trade_id": "T1",
                "symbol": "EURUSD",
                "direction": TradeDirection.buy,
                "open_price": Decimal("1.0"),
                "close_price": Decimal("1.1"),
                "volume": Decimal("0.1"),
                "profit": Decimal("10.0"),
                "commission": Decimal("0.0"),
                "swap": Decimal("0.0"),
                "net_profit": Decimal("10.0"),
                "duration_seconds": 60,
                "session": TradeSession.london,
                "opened_at": datetime.now(timezone.utc),
                "closed_at": datetime.now(timezone.utc),
                "is_manual": False,
                "is_missed": False,
                "created_at": datetime.now(timezone.utc),
            }
        ]
        
        # 1. First bulk upsert: Insert new trade
        ins, upd, dates = bulk_upsert_closed_trades(db, rows=rows)
        assert ins == 1
        assert upd == 0
        assert len(dates) == 1
        
        # 2. Second bulk upsert: Update trade, and insert a new one
        rows[0]["net_profit"] = Decimal("12.0")  # updated value
        rows.append({
            "id": uuid.uuid4(),
            "account_id": account.id,
            "broker_trade_id": "T2",
            "symbol": "GBPUSD",
            "direction": TradeDirection.sell,
            "open_price": Decimal("1.2"),
            "close_price": Decimal("1.1"),
            "volume": Decimal("0.2"),
            "profit": Decimal("20.0"),
            "commission": Decimal("-1.0"),
            "swap": Decimal("0.0"),
            "net_profit": Decimal("19.0"),
            "duration_seconds": 120,
            "session": TradeSession.new_york,
            "opened_at": datetime.now(timezone.utc),
            "closed_at": datetime.now(timezone.utc),
            "is_manual": False,
            "is_missed": False,
            "created_at": datetime.now(timezone.utc),
        })
        
        ins, upd, dates = bulk_upsert_closed_trades(db, rows=rows)
        assert ins == 1
        assert upd == 1
        assert len(dates) == 2
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_earliest_snapshot_db() -> None:
    from app.domains.accounts.repository import get_earliest_snapshot
    from app.domains.accounts.models import AccountSnapshot
    from datetime import date
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        # Add snapshots
        snap1 = AccountSnapshot(
            id=uuid.uuid4(),
            account_id=account.id,
            snapshot_date=date(2026, 5, 20),
            balance=Decimal("10000.00"),
            equity=Decimal("10050.00"),
            floating_pnl=Decimal("50.00"),
        )
        snap2 = AccountSnapshot(
            id=uuid.uuid4(),
            account_id=account.id,
            snapshot_date=date(2026, 5, 19),
            balance=Decimal("9900.00"),
            equity=Decimal("9950.00"),
            floating_pnl=Decimal("50.00"),
        )
        db.add_all([snap1, snap2])
        db.flush()
        
        earliest = get_earliest_snapshot(db, account.id)
        assert earliest is not None
        assert earliest.snapshot_date == date(2026, 5, 19)
        assert earliest.balance == Decimal("9900.00")
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()



def test_map_attachments_by_message_ids_db() -> None:
    from app.domains.journal.repository import map_attachments_by_message_ids, create_message, create_attachment
    from app.domains.journal.models import DailyJournal, JournalMessageType
    from datetime import date
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        dj = DailyJournal(
            id=uuid.uuid4(),
            account_id=account.id,
            trading_date=date(2026, 5, 20),
        )
        db.add(dj)
        db.flush()
        
        m1 = create_message(
            db,
            daily_journal_id=dj.id,
            trade_journal_id=None,
            author_id=user.id,
            message_type=JournalMessageType.text,
            content="Message 1",
            tags=[],
        )
        m2 = create_message(
            db,
            daily_journal_id=dj.id,
            trade_journal_id=None,
            author_id=user.id,
            message_type=JournalMessageType.image,
            content="Message 2",
            tags=[],
        )
        
        att1 = create_attachment(
            db,
            message_id=m2.id,
            storage_path="path/1.png",
            media_type="image",
            mime_type="image/png",
            original_filename="1.png",
            caption="Cap 1",
        )
        
        att_map = map_attachments_by_message_ids(db, [m1.id, m2.id])
        assert m1.id not in att_map
        assert m2.id in att_map
        assert len(att_map[m2.id]) == 1
        assert att_map[m2.id][0].id == att1.id
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_list_trading_dates_with_journal_activity_union_db() -> None:
    from app.domains.journal.repository import list_trading_dates_with_journal_activity, create_message
    from app.domains.journal.models import DailyJournal, JournalMessageType
    from datetime import date
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        dj1 = DailyJournal(
            id=uuid.uuid4(),
            account_id=account.id,
            trading_date=date(2026, 5, 20),
        )
        dj2 = DailyJournal(
            id=uuid.uuid4(),
            account_id=account.id,
            trading_date=date(2026, 5, 21),
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add_all([dj1, dj2])
        db.flush()
        
        create_message(
            db,
            daily_journal_id=dj1.id,
            trade_journal_id=None,
            author_id=user.id,
            message_type=JournalMessageType.text,
            content="Active message",
            tags=[],
        )
        
        dates = list_trading_dates_with_journal_activity(
            db,
            account_id=account.id,
            account_timezone="UTC",
            from_date=date(2026, 5, 19),
            to_date=date(2026, 5, 22),
        )
        
        assert dates == {date(2026, 5, 20), date(2026, 5, 21)}
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_delete_trades_outside_valid_broker_ids_in_window_returning_db() -> None:
    from app.domains.accounts.repository import delete_trades_outside_valid_broker_ids_in_window
    from datetime import date
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        # Create trades
        t1 = Trade(
            id=uuid.uuid4(),
            account_id=account.id,
            broker_trade_id="B1",
            symbol="EURUSD",
            direction=TradeDirection.buy,
            open_price=Decimal("1.0"),
            close_price=Decimal("1.1"),
            volume=Decimal("0.1"),
            profit=Decimal("10.0"),
            commission=Decimal("0.0"),
            swap=Decimal("0.0"),
            net_profit=Decimal("10.0"),
            duration_seconds=60,
            session=TradeSession.london,
            opened_at=datetime.now(timezone.utc),
            closed_at=datetime.now(timezone.utc),
            is_missed=False,
            is_manual=False,
        )
        t2 = Trade(
            id=uuid.uuid4(),
            account_id=account.id,
            broker_trade_id="B2",
            symbol="EURUSD",
            direction=TradeDirection.buy,
            open_price=Decimal("1.0"),
            close_price=Decimal("1.1"),
            volume=Decimal("0.1"),
            profit=Decimal("10.0"),
            commission=Decimal("0.0"),
            swap=Decimal("0.0"),
            net_profit=Decimal("10.0"),
            duration_seconds=60,
            session=TradeSession.london,
            opened_at=datetime.now(timezone.utc),
            closed_at=datetime.now(timezone.utc),
            is_missed=False,
            is_manual=False,
        )
        db.add_all([t1, t2])
        db.flush()
        
        # Delete trades outside valid set ("B1")
        deleted_count, affected_dates = delete_trades_outside_valid_broker_ids_in_window(
            db,
            account_id=account.id,
            closed_from_utc=None,
            closed_to_utc_exclusive=None,
            account_timezone="UTC",
            valid_broker_trade_ids={"B1"},
        )
        
        assert deleted_count == 1
        assert len(affected_dates) == 1
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_list_trade_rows_for_analytics_db() -> None:
    from app.domains.journal.repository import list_trade_rows_for_analytics
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        t1 = Trade(
            id=uuid.uuid4(),
            account_id=account.id,
            broker_trade_id="B1",
            symbol="EURUSD",
            direction=TradeDirection.buy,
            open_price=Decimal("1.0"),
            close_price=Decimal("1.1"),
            volume=Decimal("0.1"),
            profit=Decimal("10.0"),
            commission=Decimal("0.0"),
            swap=Decimal("0.0"),
            net_profit=Decimal("10.0"),
            duration_seconds=60,
            session=TradeSession.london,
            opened_at=datetime.now(timezone.utc),
            closed_at=datetime.now(timezone.utc),
            is_missed=False,
            is_manual=False,
        )
        db.add(t1)
        db.flush()
        
        rows = list_trade_rows_for_analytics(
            db,
            account_ids=[account.id],
            closed_from_utc=None,
            closed_to_utc_exclusive=None,
            include_manual=True,
        )
        
        assert len(rows) == 1
        row = rows[0]
        assert row.id == t1.id
        assert row.net_profit == Decimal("10.0")
        assert row.symbol == "EURUSD"
        assert row.account_id == account.id
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_list_recent_trades_for_dashboard_db() -> None:
    from app.domains.journal.repository import list_recent_trades_for_dashboard
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
        )
        db.add(account)
        db.flush()
        
        t1 = Trade(
            id=uuid.uuid4(),
            account_id=account.id,
            broker_trade_id="B1",
            symbol="EURUSD",
            direction=TradeDirection.buy,
            open_price=Decimal("1.0"),
            close_price=Decimal("1.1"),
            volume=Decimal("0.1"),
            profit=Decimal("10.0"),
            commission=Decimal("0.0"),
            swap=Decimal("0.0"),
            net_profit=Decimal("10.0"),
            duration_seconds=60,
            session=TradeSession.london,
            opened_at=datetime.now(timezone.utc),
            closed_at=datetime.now(timezone.utc),
            is_missed=False,
            is_manual=False,
        )
        db.add(t1)
        db.flush()
        
        recent = list_recent_trades_for_dashboard(
            db,
            account_ids=[account.id],
            closed_from_utc=None,
            closed_to_utc_exclusive=None,
            include_manual=True,
            limit=5,
        )
        
        assert len(recent) == 1
        assert isinstance(recent[0], Trade)
        assert recent[0].id == t1.id
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_multi_account_timezone_dashboard_analytics_db() -> None:
    from app.domains.journal.service._analytics import get_analytics_dashboard
    from datetime import date
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()
        
        account1 = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
            timezone="America/New_York",
            connection_state=TradingAccountConnectionState.ready,
            is_data_ready_for_stats=True,
        )
        account2 = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker 2",
            broker_login="654321",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
            timezone="UTC",
            connection_state=TradingAccountConnectionState.ready,
            is_data_ready_for_stats=True,
        )
        db.add_all([account1, account2])
        db.flush()
        
        close_time = datetime(2026, 6, 12, 1, 0, tzinfo=timezone.utc)
        
        t1 = Trade(
            id=uuid.uuid4(),
            account_id=account1.id,
            broker_trade_id="B1",
            symbol="EURUSD",
            direction=TradeDirection.buy,
            open_price=Decimal("1.0"),
            close_price=Decimal("1.1"),
            volume=Decimal("0.1"),
            profit=Decimal("10.0"),
            commission=Decimal("0.0"),
            swap=Decimal("0.0"),
            net_profit=Decimal("10.0"),
            duration_seconds=60,
            session=TradeSession.london,
            opened_at=close_time,
            closed_at=close_time,
            is_missed=False,
            is_manual=False,
        )
        db.add(t1)
        db.flush()
        
        dashboard = get_analytics_dashboard(
            db,
            account_id=None,
            user_id=user.id,
            from_date=None,
            to_date=None,
            recent_limit=8,
            time_basis="close",
            include_manual=True,
        )
        
        calendar_days = dashboard.calendar.days
        assert len(calendar_days) == 1
        assert calendar_days[0].date == date(2026, 6, 11)
        assert calendar_days[0].total_pnl == 10.0
        
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_analytics_curve_accepts_nonnumeric_broker_trade_ids_db() -> None:
    from app.domains.journal.service._analytics import get_analytics_curve
    from datetime import date

    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        user = User(
            id=uuid.uuid4(),
            username=f"user-{uuid.uuid4()}",
            email=f"test-{uuid.uuid4()}@example.com",
            hashed_password="hash",
            display_name="Test User",
            is_email_verified=True,
        )
        db.add(user)
        db.flush()

        account = TradingAccount(
            id=uuid.uuid4(),
            user_id=user.id,
            meta_account_id=f"acct-{uuid.uuid4()}",
            broker_name="Test Broker",
            broker_login="123456",
            broker_server="Server",
            account_type=TradingAccountType.live,
            platform=TradingPlatform.mt5,
            encrypted_investor_password="encrypted",
            timezone="UTC",
        )
        db.add(account)
        db.flush()

        close_time = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
        trades = [
            Trade(
                id=uuid.uuid4(),
                account_id=account.id,
                broker_trade_id="demo-0005",
                symbol="EURUSD",
                direction=TradeDirection.buy,
                open_price=Decimal("1.0"),
                close_price=Decimal("1.1"),
                volume=Decimal("0.1"),
                profit=Decimal("10.0"),
                commission=Decimal("0.0"),
                swap=Decimal("0.0"),
                net_profit=Decimal("10.0"),
                duration_seconds=60,
                session=TradeSession.london,
                opened_at=close_time,
                closed_at=close_time,
                is_missed=False,
                is_manual=False,
            ),
            Trade(
                id=uuid.uuid4(),
                account_id=account.id,
                broker_trade_id="demo-0000",
                symbol="EURUSD",
                direction=TradeDirection.sell,
                open_price=Decimal("1.1"),
                close_price=Decimal("1.0"),
                volume=Decimal("0.1"),
                profit=Decimal("-4.0"),
                commission=Decimal("0.0"),
                swap=Decimal("0.0"),
                net_profit=Decimal("-4.0"),
                duration_seconds=60,
                session=TradeSession.london,
                opened_at=close_time,
                closed_at=close_time,
                is_missed=False,
                is_manual=False,
            ),
        ]
        db.add_all(trades)
        db.flush()

        curve = get_analytics_curve(
            db,
            account_id=account.id,
            user_id=user.id,
            from_date=date(2026, 6, 12),
            to_date=date(2026, 6, 12),
            granularity="intraday",
            include_manual=True,
        )

        assert curve.intraday_curve is not None
        assert len(curve.intraday_curve.days) == 1
        points = curve.intraday_curve.days[0].points
        assert [point.symbol for point in points[1:]] == ["EURUSD", "EURUSD"]

    finally:
        db.close()
        transaction.rollback()
        connection.close()
