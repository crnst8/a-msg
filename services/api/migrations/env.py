from alembic import context
from sqlalchemy import create_engine, pool

from amsg.db.models import Base

config = context.config
url = config.get_main_option("sqlalchemy.url")


def run() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
