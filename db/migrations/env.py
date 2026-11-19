"""Alembic environment. Migrations run as pw_migrator and switch to pw_owner."""

from alembic import context
from sqlalchemy import create_engine, text

config = context.config
url = config.get_main_option("sqlalchemy.url")
if not url:
    raise RuntimeError("sqlalchemy.url is not set")

engine = create_engine(url)
with engine.connect() as connection:
    connection.execute(text("SET ROLE pw_owner"))
    context.configure(
        connection=connection,
        version_table_schema="pw_meta",
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
