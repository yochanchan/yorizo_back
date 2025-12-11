from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import company_report
from app.models import Company, FinancialStatement
from database import Base


def test_company_report_preserves_japanese_fields(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        company = Company(
            id="c1",
            user_id="u1",
            company_name="テスト製造株式会社",
            industry="製造業",
            employees_range="1-10",
            annual_sales_range="1,000万〜3,000万円",
            annual_revenue_range="1,000万〜5,000万円",
            location_prefecture="東京都",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        stmt = FinancialStatement(
            company_id="c1",
            fiscal_year=2024,
            sales=12_000_000,
            operating_profit=1_200_000,
            ordinary_profit=1_000_000,
            net_income=800_000,
            depreciation=200_000,
            labor_cost=3_600_000,
            current_assets=3_000_000,
            current_liabilities=1_800_000,
            fixed_assets=2_000_000,
            equity=3_500_000,
            total_liabilities=1_500_000,
            employees=8,
        )
        db.add(company)
        db.add(stmt)
        db.commit()

        # Avoid calling LLM during tests.
        monkeypatch.setattr(
            company_report,
            "_generate_report_with_llm",
            lambda context: company_report._fallback_report_fields(),
        )

        report = company_report.build_company_report(db, "c1")

        assert report.company.name == "テスト製造株式会社"
        assert report.company.industry == "製造業"
        assert report.radar.axes == ["売上持続性", "収益性", "健全性", "効率性", "安全性"]
    finally:
        db.close()
