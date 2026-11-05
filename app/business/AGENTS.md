# Service-owned execution

Extension installation/restart and FastAPI Sink composition belong here. Reusable domain operations and transaction ownership live in `inkcre_core`; remaining old domain modules only preserve published imports. Do not add application settings, scheduler or HTTP dependencies to the SDK.
