from datetime import date, datetime, timedelta
from random import Random

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine
from .domains import DEMO_USERS, DOMAINS
from .models import AppUser, Claim, Customer, Glossary, HitlReview, LiveEvent, Order, OrderItem, Product
from .security import hash_password

PRODUCTS = {
    "retail": [
        ("Analytics Starter", "Software", 49),
        ("Analytics Pro", "Software", 199),
        ("Warehouse Add-on", "Infrastructure", 399),
        ("Support Pack", "Services", 89),
        ("Training Workshop", "Services", 129),
        ("Dashboard Pack", "Software", 79),
    ],
    "banking": [
        ("Checking Account", "Deposits", 12),
        ("Savings Account", "Deposits", 8),
        ("Credit Card", "Cards", 45),
        ("Home Loan", "Lending", 220),
        ("Personal Loan", "Lending", 95),
        ("Wealth Mandate", "Wealth", 150),
    ],
    "finance": [
        ("Equity Fund", "Funds", 5000),
        ("Bond Fund", "Funds", 2500),
        ("ETF Core", "ETFs", 1800),
        ("Advisory Desk", "Advisory", 900),
        ("Options Overlay", "Derivatives", 1200),
        ("Private Credit", "Alts", 4000),
    ],
    "insurance": [
        ("Auto Cover", "Motor", 320),
        ("Home Cover", "Property", 540),
        ("Life Term", "Life", 280),
        ("Health Plus", "Health", 410),
        ("Cyber Shield", "Commercial", 690),
        ("Travel Protect", "Specialty", 90),
    ],
}

GLOSSARY = {
    "retail": [
        ("revenue", "orders", "total_amount", "Retail sales amount"),
        ("sales", "orders", "total_amount", "Completed order value"),
        ("churn", "customers", "churned", "1 if the shopper churned"),
        ("product", "products", "name", "Catalog item"),
        ("region", "customers", "region", "Customer geography"),
    ],
    "banking": [
        ("deposits", "orders", "total_amount", "Deposit / account flow amount"),
        ("balance", "orders", "total_amount", "Balance-impacting amount"),
        ("transaction", "orders", "id", "Banking transaction"),
        ("churn", "customers", "churned", "1 if the account holder churned"),
        ("npl", "orders", "status", "Non-performing if status is delinquent"),
    ],
    "finance": [
        ("aum", "orders", "total_amount", "Assets under management flow"),
        ("trade", "orders", "id", "Executed trade"),
        ("notional", "orders", "total_amount", "Trade notional"),
        ("instrument", "products", "name", "Traded instrument"),
        ("churn", "customers", "churned", "1 if the client churned"),
    ],
    "insurance": [
        ("premium", "orders", "total_amount", "Gross written premium"),
        ("gwp", "orders", "total_amount", "Gross written premium"),
        ("claim", "claims", "amount", "Claimed amount"),
        ("loss ratio", "claims", "amount", "Claims vs premium"),
        ("policy", "products", "name", "Insurance product"),
        ("churn", "customers", "churned", "1 if the policyholder churned"),
    ],
}


def ensure_schema() -> None:
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    needs_reset = False
    if "customers" in tables:
        cols = {c["name"] for c in inspector.get_columns("customers")}
        if "domain" not in cols:
            needs_reset = True
    if "app_users" not in tables and tables:
        needs_reset = True
    if needs_reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _add_column_if_missing("query_history", "audit_id", "VARCHAR(40)")
    _add_column_if_missing("query_history", "tables_used", "VARCHAR(240)")
    _add_column_if_missing("query_history", "row_count", "INTEGER DEFAULT 0")
    _add_column_if_missing("query_history", "guardrail_ok", "INTEGER DEFAULT 1")
    _add_column_if_missing("query_history", "result_json", "TEXT")
    _add_column_if_missing("customers", "account_number", "VARCHAR(40)")
    _add_column_if_missing("claims", "policy_number", "VARCHAR(40)")


def _add_column_if_missing(table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns(table)}
    if column in cols:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def seed_if_empty(db: Session) -> None:
    if db.query(AppUser).count() > 0 and db.query(Customer).count() > 0:
        if settings.demo_mode:
            _seed_demo_sensitive_identifiers(db)
        return
    rng = Random(42)
    for user in DEMO_USERS:
        db.add(
            AppUser(
                username=user["username"],
                password_hash=hash_password(user["password"]),
                domain=user["domain"],
                display_name=user["display_name"],
            )
        )

    n_customers = settings.seed_customers_per_domain
    regions = ["North", "South", "East", "West"]
    segments = {
        "retail": ["Retail", "SMB", "Enterprise"],
        "banking": ["Mass", "Affluent", "Private"],
        "finance": ["Retail", "Institutional", "Family office"],
        "insurance": ["Personal", "SME", "Commercial"],
    }
    product_rows: dict[str, list[Product]] = {}
    for domain, catalog in PRODUCTS.items():
        rows = [Product(domain=domain, name=n, category=c, unit_price=p) for n, c, p in catalog]
        db.add_all(rows)
        product_rows[domain] = rows
    db.flush()

    order_id = 1
    item_id = 1
    claim_id = 1
    for domain in DOMAINS:
        customers = []
        for i in range(1, n_customers + 1):
            churned = 1 if rng.random() > 0.82 else 0
            customers.append(
                Customer(
                    domain=domain,
                    name=f"{domain.title()} customer {i:03d}",
                    segment=rng.choice(segments[domain]),
                    region=rng.choice(regions),
                    account_number=f"DEMO-ACCT-{domain.upper()}-{i:05d}",
                    signup_date=date(2023, 1, 1) + timedelta(days=rng.randint(0, 900)),
                    churned=churned,
                )
            )
        db.add_all(customers)
        db.flush()
        for customer in customers:
            n_orders = 1 if customer.churned else rng.randint(3, 10)
            for _ in range(n_orders):
                days_ago = rng.randint(8, 420) if customer.churned else rng.randint(0, 180)
                product = rng.choice(product_rows[domain])
                qty = rng.randint(1, 4)
                amount = round(product.unit_price * qty * (0.8 + rng.random()), 2)
                status = "completed"
                if domain == "banking" and rng.random() < 0.06:
                    status = "delinquent"
                elif rng.random() < 0.04:
                    status = "cancelled"
                order = Order(
                    id=order_id,
                    domain=domain,
                    customer_id=customer.id,
                    order_date=date.today() - timedelta(days=days_ago),
                    status=status,
                    total_amount=amount,
                )
                db.add(order)
                db.add(
                    OrderItem(
                        id=item_id,
                        domain=domain,
                        order_id=order_id,
                        product_id=product.id,
                        quantity=qty,
                        amount=amount,
                    )
                )
                order_id += 1
                item_id += 1
            if domain == "insurance" and rng.random() < 0.35:
                db.add(
                    Claim(
                        id=claim_id,
                        domain=domain,
                        customer_id=customer.id,
                        claim_date=date.today() - timedelta(days=rng.randint(1, 200)),
                        amount=round(rng.uniform(80, 2400), 2),
                        status=rng.choice(["open", "paid", "rejected"]),
                        policy_number=f"DEMO-POL-INS-{customer.id:05d}",
                    )
                )
                claim_id += 1

        for term, table, column, desc in GLOSSARY[domain]:
            db.add(
                Glossary(
                    domain=domain,
                    business_term=term,
                    table_name=table,
                    column_name=column,
                    description=desc,
                )
            )
        db.add(
            LiveEvent(
                domain=domain,
                event_type="seed",
                amount=0,
                created_at=datetime.utcnow(),
            )
        )
    db.commit()


def _seed_demo_sensitive_identifiers(db: Session) -> None:
    customers = db.query(Customer).filter(Customer.account_number.is_(None)).all()
    claims = db.query(Claim).filter(Claim.policy_number.is_(None), Claim.domain == "insurance").all()
    for customer in customers:
        customer.account_number = f"DEMO-ACCT-{customer.domain.upper()}-{customer.id:05d}"
    for claim in claims:
        claim.policy_number = f"DEMO-POL-INS-{claim.customer_id:05d}"
    if customers or claims:
        db.commit()


def apply_postgres_routines(db: Session) -> None:
    if not settings.database_url.startswith("postgresql"):
        return
    db.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION ryze_live_tick(p_domain text)
            RETURNS numeric AS $$
            DECLARE v_amount numeric;
            BEGIN
              v_amount := round((random() * 900 + 40)::numeric, 2);
              INSERT INTO live_events(domain, event_type, amount, created_at)
              VALUES (p_domain, 'plsql_tick', v_amount, NOW());
              RETURN v_amount;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    db.commit()
