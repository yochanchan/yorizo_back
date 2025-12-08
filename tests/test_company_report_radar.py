from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import company_report
from app.models import Company, FinancialStatement
from database import Base


def test_company_report_radar_uses_financials(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        company = Company(
            id="c1",
            user_id="u1",
            company_name="レーダーチャートテスト株式会社",
            industry="IT",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        stmt_latest = FinancialStatement(
            company_id="c1",
            fiscal_year=2024,
            sales=10_000_000,
            operating_profit=1_000_000,
            equity=4_000_000,
            total_liabilities=3_000_000,
            employees=5,
        )
        stmt_prev = FinancialStatement(
            company_id="c1",
            fiscal_year=2023,
            sales=8_000_000,
            operating_profit=500_000,
            equity=3_500_000,
            total_liabilities=3_200_000,
            employees=5,
        )
        stmt_prev2 = FinancialStatement(
            company_id="c1",
            fiscal_year=2022,
            sales=7_000_000,
            operating_profit=300_000,
            equity=3_200_000,
            total_liabilities=3_300_000,
            employees=4,
        )
        db.add_all([company, stmt_latest, stmt_prev, stmt_prev2])
        db.commit()

        monkeypatch.setattr(
            company_report,
            "_generate_report_with_llm",
            lambda context: company_report._fallback_report_fields(),
        )

        report = company_report.build_company_report(db, "c1")

        assert report.radar.axes == ["売上持続性", "収益性", "生産性", "健全性", "効率性", "安全性"]
        assert len(report.radar.periods) == 3
        for period in report.radar.periods:
            assert len(period.raw_values) == 6
            for v in period.raw_values:
                assert v is None or isinstance(v, float)
    finally:
        db.close()
