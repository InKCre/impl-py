__all__ = [
  "Base",
  "BlockModel",
  "StorageModel",
  "StorageBlobModel",
  "StorageTypesModel",
  "RelationModel",
  "SourceModel",
  "JobTypeModel",
  "JobModel",
  "CronModel",
  "ExtensionModel",
  "ExtensionPeerParticipationModel",
  "AIDialectModel",
  "AIProviderModel",
  "AIModelModel",
  "EmbeddingProfileModel",
  "RelationEmbeddingModel",
  "BlockEmbeddingModel",
  "BlockLexicalRecordModel",
  "AgentDefinitionModel",
  "PeerModel",
  "DeploymentConfigModel",
  "SinkModel",
  "SinkTypeModel",
]

import sqlalchemy.orm
import sqlmodel

from app.database_contract import PROTOCOL_SCHEMA


Base = sqlalchemy.orm.declarative_base(metadata=sqlalchemy.MetaData(schema=PROTOCOL_SCHEMA))
sqlmodel.SQLModel.metadata = Base.metadata

from .info_base.block import BlockModel
from .info_base.storage import StorageBlobModel, StorageModel, StorageTypesModel
from .info_base.relation import RelationModel
from .source import SourceModel
from .job import JobModel, JobTypeModel
from .cron import CronModel
from .extension.main import ExtensionModel, ExtensionPeerParticipationModel
from .ai import (
  AIDialectModel,
  AIModelModel,
  AIProviderModel,
  BlockEmbeddingModel,
  EmbeddingProfileModel,
  RelationEmbeddingModel,
)
from .agent import AgentDefinitionModel
from .peer import PeerModel
from .deployment_config import DeploymentConfigModel
from .lexical_retrieval import BlockLexicalRecordModel
from .sink import SinkModel, SinkTypeModel
