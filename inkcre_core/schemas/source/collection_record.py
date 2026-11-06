"""Native identity lookup; Block content remains the information authority."""

import sqlalchemy
import sqlmodel


class CollectionRecordModel(sqlmodel.SQLModel, table=True):
  __tablename__ = "collection_records"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      "length(namespace) BETWEEN 1 AND 200", name="collection_record_namespace"
    ),
    sqlalchemy.CheckConstraint(
      "length(native_key) BETWEEN 1 AND 1000", name="collection_record_key"
    ),
  )

  namespace: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
  )
  native_key: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
  )
  block: int = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.Integer,
      sqlalchemy.ForeignKey("blocks.id", ondelete="CASCADE", onupdate="CASCADE"),
      nullable=False,
      index=True,
    )
  )
