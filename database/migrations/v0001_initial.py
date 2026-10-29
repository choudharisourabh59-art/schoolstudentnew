from database.models import Base


def upgrade(conn) -> None:
    Base.metadata.create_all(conn)
