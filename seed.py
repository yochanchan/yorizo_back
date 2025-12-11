import logging
import os
from datetime import datetime, timedelta

from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from app.models import Company, Conversation, FinancialStatement, Memory, Message, User

logger = logging.getLogger(__name__)

DEMO_USER_ID = os.getenv("DEMO_USER_ID", "demo-user")


def get_or_create_demo_user(session: Session) -> User:
    """
    Fetch the demo user; create tables and the row if missing.
    """
    try:
        user = session.get(User, DEMO_USER_ID)
    except ProgrammingError:
        Base.metadata.create_all(bind=engine)
        session.rollback()
        user = session.get(User, DEMO_USER_ID)

    if user is None:
        user = User(id=DEMO_USER_ID, external_id="demo", nickname="demo")
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def seed_demo_data() -> None:
    """
    Seed minimal demo data for local development.
    """
    if os.getenv("DISABLE_DEMO_SEED"):
        logger.info("DISABLE_DEMO_SEED is set; skipping demo seed")
        return

    try:
        Base.metadata.create_all(bind=engine)
    except SQLAlchemyError as exc:
        logger.warning("Skipping demo seed; failed to create tables: %s", exc)
        return

    try:
        with SessionLocal() as db:
            user = get_or_create_demo_user(db)

            company = (
                db.query(Company)
                .filter((Company.id == user.id) | (Company.user_id == user.id))
                .first()
            )
            if not company:
                company = Company(
                    id=user.id,
                    user_id=user.id,
                    company_name="テスト製造株式会社",
                    name="テスト製造株式会社",
                    industry="製造業",
                    employees=10,
                    employees_range="1-10",
                    annual_sales_range="3,000万～5,000万円",
                    annual_revenue_range="1,000万～5,000万円",
                    location_prefecture="東京都",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(company)
                db.commit()
                db.refresh(company)
            else:
                # 既存データが文字化けしていても正常な日本語に上書きする
                company.name = "テスト製造株式会社"
                company.company_name = "テスト製造株式会社"
                company.industry = "製造業"
                company.employees = company.employees or 10
                company.employees_range = "1-10"
                company.annual_sales_range = "3,000万～5,000万円"
                company.annual_revenue_range = "1,000万～5,000万円"
                company.location_prefecture = "東京都"
                company.updated_at = datetime.utcnow()
                db.commit()

            demo_company_id = "1"
            alias_company = db.query(Company).filter(Company.id == demo_company_id).first()
            if not alias_company:
                alias_company = Company(
                    id=demo_company_id,
                    user_id=user.id,
                    name=company.name,
                    company_name=company.company_name,
                    industry=company.industry,
                    employees=company.employees,
                    employees_range=company.employees_range,
                    annual_sales_range=company.annual_sales_range,
                    annual_revenue_range=company.annual_revenue_range,
                    location_prefecture=company.location_prefecture,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(alias_company)
                db.commit()
                db.refresh(alias_company)
            else:
                alias_company.name = company.name
                alias_company.company_name = company.company_name
                alias_company.industry = company.industry
                alias_company.employees = alias_company.employees or company.employees
                alias_company.employees_range = company.employees_range
                alias_company.annual_sales_range = company.annual_sales_range
                alias_company.annual_revenue_range = company.annual_revenue_range
                alias_company.location_prefecture = company.location_prefecture
                alias_company.updated_at = datetime.utcnow()
                db.commit()

            has_conversation = db.query(Conversation).filter(Conversation.user_id == user.id).count() > 0
            if not has_conversation:
                conv1 = Conversation(
                    user_id=user.id,
                    title="Sales growth consultation",
                    main_concern="Regular customers are declining and monthly revenue is flat.",
                    channel="chat",
                    started_at=datetime.utcnow() - timedelta(days=2),
                )
                conv2 = Conversation(
                    user_id=user.id,
                    title="Hiring and staffing",
                    main_concern="Short on hall staff and hiring is not progressing.",
                    channel="chat",
                    started_at=datetime.utcnow() - timedelta(days=5),
                )
                db.add_all([conv1, conv2])
                db.commit()
                db.refresh(conv1)
                db.refresh(conv2)

                messages_conv1 = [
                    Message(conversation_id=conv1.id, role="user", content="Sales are sluggish and regulars are decreasing."),
                    Message(
                        conversation_id=conv1.id,
                        role="assistant",
                        content="Where do you feel the pain is bigger: number of visitors or average spend?",
                    ),
                    Message(
                        conversation_id=conv1.id,
                        role="user",
                        content="Visitor count is dropping the most. New customer acquisition is also weak.",
                    ),
                ]
                messages_conv2 = [
                    Message(conversation_id=conv2.id, role="user", content="Hiring for hall staff is not going well."),
                    Message(conversation_id=conv2.id, role="assistant", content="What channels have you tried so far?"),
                    Message(conversation_id=conv2.id, role="user", content="Job boards and referrals, but little traction."),
                ]
                db.add_all(messages_conv1 + messages_conv2)
                db.commit()

            if db.query(Memory).filter(Memory.user_id == user.id).count() == 0:
                memory = Memory(
                    user_id=user.id,
                    current_concerns="Sales and hiring remain challenging.",
                    important_points="Staffing is tight and revenue has been flat.",
                    remembered_facts="Regular customers are declining; new acquisition is weak.",
                    last_updated_at=datetime.utcnow(),
                )
                db.add(memory)
                db.commit()
    except SQLAlchemyError as exc:
        logger.warning("Skipping demo seed due to database error: %s", exc)
