from alembic import context

from wks.infrastructure.database import Base, database
from wks.settings import Settings

engine, _ = database(Settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
