import enum
import aiohttp
import ssl
import certifi
import pydantic


enum_serializer = pydantic.PlainSerializer(
  lambda value: value.value if isinstance(value, enum.Enum) else value,
  return_type=str,
)


def AIOHTTP_CONNECTOR_GETTER():
  return aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))
