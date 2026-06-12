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
from app.domains.accounts.models import TradingAccount, Trade, TradeDirection, TradeSession, TradingAccountType, TradingPlatform
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
            username=f"user-{uuid.uuid4()}",
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
    from app.domains.accounts.repository import (
        try_acquire_account_sync_lock,
        release_account_sync_lock,
        is_account_sync_locked,
    )
    
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        account_id = uuid.uuid4()
        
        # Initially, the lock should not be held
        assert is_account_sync_locked(db, account_id) is False
        
        # Acquire lock
        acquired = try_acquire_account_sync_lock(db, account_id)
        assert acquired is True
        
        # Now it should be locked
        assert is_account_sync_locked(db, account_id) is True
        
        # Release lock
        release_account_sync_lock(db, account_id)
        
        # Should be unlocked again
        assert is_account_sync_locked(db, account_id) is False
        
    finally:
        db.close()
        transaction.rollback()
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
            username=f"user-{uuid.uuid4()}",
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
