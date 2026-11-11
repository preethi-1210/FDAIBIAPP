# Banking data access policy

Analytics answers are aggregates. Account numbers, card numbers, customer identifiers, transaction identifiers, names, phone numbers, email addresses, PAN, Aadhaar and addresses are not returned to business users, and approved queries use identifiers only to link tables.

CVV and card security codes are never available to anyone, including administrators. Card expiry dates are never returned in full.

Sessions are limited to one business domain, so a banking session cannot read insurance, retail or healthcare data, and asking for it is refused rather than answered from banking data.

Forecasts for business users are released only after an administrator approves them. Approval cannot override a privacy or access restriction.

Questions, generated SQL and results are recorded in the audit history. Sensitive values typed into a question are masked before the question is stored or sent to a language model.
