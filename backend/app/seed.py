from datetime import date, datetime, timedelta
from random import Random

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine
from .domains import DEMO_USERS
from .models import (
    AppUser,
    BankAccount,
    BankLoan,
    BankTransaction,
    Claim,
    Customer,
    Glossary,
    HealthcareClaim,
    HealthcareDiagnosis,
    HealthcareEncounter,
    HealthcareFacility,
    HealthcarePatient,
    HealthcareProcedure,
    HealthcareProvider,
    HealthcareQualityMetric,
    HitlReview,
    LiveEvent,
    Order,
    OrderItem,
    Product,
    UserDomainTenant,
)
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
        ("deposits", "accounts", "balance", "Synthetic account deposit balance"),
        ("balance", "accounts", "balance", "Current synthetic account balance"),
        ("account", "accounts", "account_type", "Synthetic bank account"),
        ("transaction", "transactions", "amount", "Synthetic bank transaction"),
        ("loan", "loans", "outstanding_balance", "Synthetic loan outstanding balance"),
        ("npl", "loans", "status", "Non-performing if loan status is delinquent"),
        ("churn", "customers", "churned", "1 if the account holder churned"),
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
    "healthcare": [
        ("patient", "healthcare_patients", "patient_key", "Synthetic tenant-scoped patient key"),
        ("encounter", "healthcare_encounters", "encounter_key", "Synthetic care encounter"),
        ("diagnosis", "healthcare_diagnoses", "diagnosis_category", "Synthetic diagnosis category"),
        ("procedure", "healthcare_procedures", "procedure_category", "Synthetic procedure category"),
        ("claim", "healthcare_claims", "claim_amount", "Synthetic claim amount"),
        ("approved amount", "healthcare_claims", "approved_amount", "Synthetic approved claim amount"),
        ("provider", "healthcare_providers", "provider_specialty", "Synthetic provider specialty"),
        ("facility", "healthcare_facilities", "facility_region", "Synthetic facility region"),
        ("quality metric", "healthcare_quality_metrics", "metric_value", "Synthetic facility quality metric"),
    ],
}


def ensure_schema() -> None:
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "customers" in tables:
        cols = {c["name"] for c in inspector.get_columns("customers")}
        if "domain" not in cols:
            raise RuntimeError(
                "The existing customers table uses a legacy schema without a domain column. "
                "Automatic migration is not safe and no data was changed; back up and migrate it explicitly."
            )
    Base.metadata.create_all(bind=engine)
    _add_column_if_missing("query_history", "audit_id", "VARCHAR(40)")
    _add_column_if_missing("query_history", "tables_used", "VARCHAR(240)")
    _add_column_if_missing("query_history", "row_count", "INTEGER DEFAULT 0")
    _add_column_if_missing("query_history", "guardrail_ok", "INTEGER DEFAULT 1")
    _add_column_if_missing("query_history", "result_json", "TEXT")
    _add_column_if_missing("query_history", "tenant_id", "VARCHAR(64)")
    _add_column_if_missing("conversations", "tenant_id", "VARCHAR(64)")
    _add_column_if_missing("hitl_reviews", "tenant_id", "VARCHAR(64)")
    _add_column_if_missing("customers", "account_number", "VARCHAR(40)")
    _add_column_if_missing("claims", "policy_number", "VARCHAR(40)")
    _add_column_if_missing("domain_access_requests", "decision_seen", "INTEGER DEFAULT 0")
    _add_column_if_missing("hitl_reviews", "conversation_id", "VARCHAR(36)")
    _add_column_if_missing("hitl_reviews", "reviewed_by", "VARCHAR(80)")
    _add_column_if_missing("hitl_reviews", "reviewed_at", "TIMESTAMP")
    _add_column_if_missing("hitl_reviews", "decision_note", "TEXT")
    _add_column_if_missing("hitl_reviews", "completed_at", "TIMESTAMP")
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_query_history_tenant_id ON query_history (tenant_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_tenant_id ON conversations (tenant_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_hitl_reviews_tenant_id ON hitl_reviews (tenant_id)"))


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
    if not settings.demo_mode:
        return
    existing_usernames = {username for (username,) in db.query(AppUser.username).all()}
    for user in DEMO_USERS:
        if user["username"] not in existing_usernames:
            db.add(
                AppUser(
                    username=user["username"],
                    password_hash=hash_password(user["password"]),
                    domain=user["domain"],
                    display_name=user["display_name"],
                )
            )
    db.flush()
    if db.query(Customer).count() > 0:
        _seed_demo_sensitive_identifiers(db)
        _seed_banking_demo_data(db)
        _seed_demo_healthcare(db)
        return
    rng = Random(42)

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
    for domain in PRODUCTS:
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
    if settings.demo_mode:
        _seed_banking_demo_data(db)
        _seed_demo_healthcare(db)


def _seed_banking_demo_data(db: Session) -> None:
    if db.query(BankAccount).count() > 0:
        return
    customers = db.query(Customer).filter(Customer.domain == "banking").order_by(Customer.id).all()
    if not customers:
        return

    rng = Random(84)
    account_types = ("checking", "savings", "money_market")
    transactions: list[BankTransaction] = []
    loans: list[BankLoan] = []
    for customer in customers:
        for account_index in range(1 if rng.random() < 0.72 else 2):
            account_type = rng.choice(account_types)
            account = BankAccount(
                domain="banking",
                customer_id=customer.id,
                account_number=f"DEMO-BANK-{customer.id:06d}-{account_index + 1:02d}",
                account_type=account_type,
                balance=round(rng.uniform(250, 125000), 2),
                status="active" if rng.random() < 0.96 else "dormant",
                opened_date=customer.signup_date,
            )
            db.add(account)
            db.flush()
            for _ in range(rng.randint(3, 8)):
                amount = round(rng.uniform(5, 4500), 2)
                transactions.append(
                    BankTransaction(
                        domain="banking",
                        account_id=account.id,
                        transaction_date=date.today() - timedelta(days=rng.randint(0, 365)),
                        transaction_type=rng.choice(
                            ("deposit", "withdrawal", "payment", "transfer")
                        ),
                        amount=amount if rng.random() < 0.55 else -amount,
                        status="posted" if rng.random() < 0.98 else "reversed",
                        channel=rng.choice(("branch", "online", "mobile", "ATM")),
                    )
                )

        if rng.random() < 0.42:
            principal = round(rng.uniform(5000, 350000), 2)
            outstanding = round(principal * rng.uniform(0.18, 1.0), 2)
            origin = customer.signup_date
            loans.append(
                BankLoan(
                    domain="banking",
                    customer_id=customer.id,
                    loan_type=rng.choice(("mortgage", "personal", "auto", "small_business")),
                    principal_amount=principal,
                    outstanding_balance=outstanding,
                    interest_rate=round(rng.uniform(0.025, 0.16), 4),
                    status="delinquent" if rng.random() < 0.08 else "performing",
                    origination_date=origin,
                    maturity_date=origin + timedelta(days=rng.randint(365, 365 * 30)),
                )
            )

    db.add_all(transactions)
    db.add_all(loans)
    glossary_terms = (
        ("account", "accounts", "account_type", "Synthetic bank account type"),
        ("balance", "accounts", "balance", "Current synthetic bank account balance"),
        ("transaction", "transactions", "amount", "Synthetic posted transaction amount"),
        ("loan", "loans", "outstanding_balance", "Synthetic loan outstanding balance"),
        ("npl", "loans", "status", "Non-performing if loan status is delinquent"),
    )
    existing_terms = {
        term
        for (term,) in db.query(Glossary.business_term)
        .filter(Glossary.domain == "banking")
        .all()
    }
    db.add_all(
        Glossary(
            domain="banking",
            business_term=term,
            table_name=table,
            column_name=column,
            description=description,
        )
        for term, table, column, description in glossary_terms
        if term not in existing_terms
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


def _seed_demo_healthcare(db: Session) -> None:
    tenant_id = "demo-healthcare"
    usernames = {username for (username,) in db.query(AppUser.username).all()}
    existing_assignments = {
        username
        for (username,) in db.query(UserDomainTenant.username)
        .filter(UserDomainTenant.domain == "healthcare")
        .all()
    }
    db.add_all(
        UserDomainTenant(username=username, domain="healthcare", tenant_id=tenant_id)
        for username in ("admin", "health.user")
        if username in usernames and username not in existing_assignments
    )
    existing_terms = {
        term
        for (term,) in db.query(Glossary.business_term)
        .filter(Glossary.domain == "healthcare")
        .all()
    }
    db.add_all(
        Glossary(domain="healthcare", business_term=term, table_name=table, column_name=column, description=description)
        for term, table, column, description in GLOSSARY["healthcare"]
        if term not in existing_terms
    )
    if db.query(HealthcarePatient).filter(HealthcarePatient.tenant_id == tenant_id).first():
        db.commit()
        return

    rng = Random(164)
    regions = ("North", "South", "East", "West")
    facilities = [
        HealthcareFacility(
            tenant_id=tenant_id,
            facility_key=f"DEMO-FAC-{index:03d}",
            facility_type=facility_type,
            facility_region=region,
        )
        for index, (facility_type, region) in enumerate(
            zip(("hospital", "clinic", "urgent care", "hospital"), regions),
            start=1,
        )
    ]
    db.add_all(facilities)
    providers = [
        HealthcareProvider(
            tenant_id=tenant_id,
            provider_key=f"DEMO-PROV-{index:04d}",
            provider_specialty=rng.choice(("primary care", "cardiology", "orthopedics", "pediatrics")),
            facility_key=facilities[(index - 1) % len(facilities)].facility_key,
            provider_region=facilities[(index - 1) % len(facilities)].facility_region,
        )
        for index in range(1, 13)
    ]
    db.add_all(providers)
    patient_count = max(40, min(settings.seed_customers_per_domain, 250))
    patients = [
        HealthcarePatient(
            tenant_id=tenant_id,
            patient_key=f"DEMO-PAT-{index:05d}",
            age_band=rng.choice(("0-17", "18-34", "35-49", "50-64", "65+")),
            sex=rng.choice(("female", "male", "unspecified")),
            postal_region=rng.choice(regions),
            enrollment_date=date.today() - timedelta(days=rng.randint(1, 900)),
            patient_status=rng.choice(("active", "active", "inactive")),
        )
        for index in range(1, patient_count + 1)
    ]
    db.add_all(patients)
    db.flush()

    encounters: list[HealthcareEncounter] = []
    diagnoses: list[HealthcareDiagnosis] = []
    procedures: list[HealthcareProcedure] = []
    claims: list[HealthcareClaim] = []
    for patient_index, patient in enumerate(patients, start=1):
        for encounter_index in range(rng.randint(1, 4)):
            ordinal = len(encounters) + 1
            facility = facilities[(patient_index + encounter_index) % len(facilities)]
            provider = providers[(patient_index + encounter_index) % len(providers)]
            encounter = HealthcareEncounter(
                tenant_id=tenant_id,
                encounter_key=f"DEMO-ENC-{ordinal:06d}",
                patient_key=patient.patient_key,
                provider_key=provider.provider_key,
                facility_key=facility.facility_key,
                encounter_date=date.today() - timedelta(days=rng.randint(1, 365)),
                encounter_type=rng.choice(("outpatient", "inpatient", "urgent care", "telehealth")),
                encounter_status=rng.choice(("completed", "completed", "cancelled")),
            )
            encounters.append(encounter)
            if rng.random() < 0.8:
                diagnoses.append(
                    HealthcareDiagnosis(
                        tenant_id=tenant_id,
                        diagnosis_key=f"DEMO-DX-{len(diagnoses) + 1:06d}",
                        encounter_key=encounter.encounter_key,
                        diagnosis_code=f"SYN-{rng.randint(100, 999)}",
                        diagnosis_category=rng.choice(("preventive", "cardiovascular", "respiratory", "musculoskeletal")),
                        diagnosis_date=encounter.encounter_date,
                    )
                )
            if rng.random() < 0.45:
                procedures.append(
                    HealthcareProcedure(
                        tenant_id=tenant_id,
                        procedure_key=f"DEMO-PR-{len(procedures) + 1:06d}",
                        encounter_key=encounter.encounter_key,
                        procedure_code=f"SYN-{rng.randint(100, 999)}",
                        procedure_category=rng.choice(("imaging", "laboratory", "therapy", "screening")),
                        procedure_date=encounter.encounter_date,
                    )
                )
            if rng.random() < 0.85:
                claim_amount = round(rng.uniform(75, 4200), 2)
                claim_status = rng.choice(("approved", "approved", "pending", "denied"))
                claims.append(
                    HealthcareClaim(
                        tenant_id=tenant_id,
                        claim_key=f"DEMO-CLM-{len(claims) + 1:06d}",
                        encounter_key=encounter.encounter_key,
                        service_date=encounter.encounter_date,
                        claim_amount=claim_amount,
                        approved_amount=round(claim_amount * rng.uniform(0.45, 1), 2)
                        if claim_status == "approved"
                        else 0,
                        claim_status=claim_status,
                        payer_key=f"DEMO-PAYER-{rng.randint(1, 4):02d}",
                    )
                )
 
    # Insert parent encounters before dependent records.
    db.add_all(encounters)
    db.flush()

    # Insert child records after their parent encounters exist.
    db.add_all(diagnoses)
    db.add_all(procedures)
    db.add_all(claims)
    db.add_all(
        HealthcareQualityMetric(
            tenant_id=tenant_id,
            metric_key=f"DEMO-QM-{index:05d}",
            facility_key=facility.facility_key,
            metric_date=date.today() - timedelta(days=30 * period),
            metric_name=metric_name,
            metric_value=round(rng.uniform(0.72, 0.99), 3),
            reporting_period=f"{date.today().year}-{date.today().month:02d}",
        )
        for index, (facility, metric_name, period) in enumerate(
            (
                (facility, metric, period)
                for facility in facilities
                for metric in ("follow-up within 7 days", "preventive screening completion")
                for period in range(1, 7)
            ),
            start=1,
        )
    )
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
