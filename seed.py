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
    Seed minimal demo data for local development using ASCII/English text.
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
                    company_name="Demo Company",
                    name="Demo Company",
                    industry="Services",
                    employees=10,
                    employees_range="1-10",
                    annual_sales_range="30-50M JPY",
                    annual_revenue_range="1,000-5,000 ten-thousand JPY",
                    location_prefecture="Tokyo",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(company)
                db.commit()
                db.refresh(company)
            else:
                company.name = company.name or company.company_name or "Demo Company"
                company.company_name = company.company_name or company.name or "Demo Company"
                company.industry = company.industry or "Services"
                company.employees = company.employees or 10
                company.employees_range = company.employees_range or "1-10"
                company.annual_sales_range = company.annual_sales_range or "30-50M JPY"
                company.annual_revenue_range = company.annual_revenue_range or "1,000-5,000 ten-thousand JPY"
                company.location_prefecture = company.location_prefecture or "Tokyo"
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
                alias_company.name = alias_company.name or company.name
                alias_company.company_name = alias_company.company_name or company.company_name
                alias_company.industry = alias_company.industry or company.industry
                alias_company.employees = alias_company.employees or company.employees
                alias_company.employees_range = alias_company.employees_range or company.employees_range
                alias_company.annual_sales_range = alias_company.annual_sales_range or company.annual_sales_range
                alias_company.annual_revenue_range = alias_company.annual_revenue_range or company.annual_revenue_range
                alias_company.location_prefecture = alias_company.location_prefecture or company.location_prefecture
                alias_company.updated_at = datetime.utcnow()
                db.commit()

            has_financials = (
                db.query(FinancialStatement)
                .filter(FinancialStatement.company_id == company.id)
                .count()
                > 0
            )
            if not has_financials:
                statements = [
                    FinancialStatement(
                        company_id=company.id,
                        fiscal_year=2022,
                        sales=12000000,
                        operating_profit=1200000,
                        ordinary_profit=1100000,
                        net_income=800000,
                        depreciation=250000,
                        labor_cost=3600000,
                        current_assets=3000000,
                        current_liabilities=1800000,
                        fixed_assets=4200000,
                        equity=3500000,
                        total_liabilities=2000000,
                        employees=8,
                    ),
                    FinancialStatement(
                        company_id=company.id,
                        fiscal_year=2023,
                        sales=13600000,
                        operating_profit=1500000,
                        ordinary_profit=1400000,
                        net_income=950000,
                        depreciation=280000,
                        labor_cost=3900000,
                        current_assets=3400000,
                        current_liabilities=2000000,
                        fixed_assets=4300000,
                        equity=3800000,
                        total_liabilities=2100000,
                        employees=9,
                    ),
                    FinancialStatement(
                        company_id=company.id,
                        fiscal_year=2024,
                        sales=15000000,
                        operating_profit=1650000,
                        ordinary_profit=1520000,
                        net_income=1050000,
                        depreciation=300000,
                        labor_cost=4200000,
                        current_assets=3800000,
                        current_liabilities=2100000,
                        fixed_assets=4400000,
                        equity=4200000,
                        total_liabilities=2050000,
                        employees=10,
                    ),
                ]
                db.add_all(statements)
                db.commit()

            alias_financials = (
                db.query(FinancialStatement)
                .filter(FinancialStatement.company_id == demo_company_id)
                .count()
            )
            if alias_financials == 0:
                base_statements = (
                    db.query(FinancialStatement)
                    .filter(FinancialStatement.company_id == company.id)
                    .all()
                )
                duplicates = [
                    FinancialStatement(
                        company_id=demo_company_id,
                        fiscal_year=s.fiscal_year,
                        sales=s.sales,
                        operating_profit=s.operating_profit,
                        ordinary_profit=s.ordinary_profit,
                        net_income=s.net_income,
                        depreciation=s.depreciation,
                        labor_cost=s.labor_cost,
                        current_assets=s.current_assets,
                        current_liabilities=s.current_liabilities,
                        fixed_assets=s.fixed_assets,
                        equity=s.equity,
                        total_liabilities=s.total_liabilities,
                        employees=s.employees,
                    )
                    for s in base_statements
                ]
                if duplicates:
                    db.add_all(duplicates)
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
