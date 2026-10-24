"""Analytics engine: periodic insights, MIS reports and data science over the invoices in the database.

Everything is computed from rows read from the database (never from the extraction pipeline's memory),
every finding names the invoices behind it, and every run is recorded in the audit log.
"""
ENGINE_VERSION = "analytics-1.0"
