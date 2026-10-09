from alembic import context
from wks_core.settings import Settings
from wks_core.storage.database import Base, database

engine, _ = database(Settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
