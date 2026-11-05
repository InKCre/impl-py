"""Log batches commit independently of application transactions."""

from collections.abc import Sequence

from inkcre_core.context import current_core
from inkcre_core.libs.obsrv.log_record import LogModel


async def write_logs(entries: Sequence[LogModel]) -> None:
  async with current_core().sessions.begin() as session:
    session.add_all(entries)
