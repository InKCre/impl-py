# Core Runtime Security Boundaries

## Purpose And Authority

The shared [Security Boundary Model](../_shared/20-product-tdd/security-boundary-model.md)
owns InKCre-wide actors，assets，trust boundaries，security classification and proportionality
method。This document records only how `core-py` realizes those boundaries。

External vulnerability reporting is owned by the root [Security Policy](../../SECURITY.md)。
Executable authentication，database and runtime behavior remains owned by code，tests，CI and
the linked deployment contracts。

## Runtime Scope

This local projection covers the FastAPI runtime，the executable PostgreSQL contract，built-in
Extension loading，Source collection，Resolver/Storage access，background Jobs and the artifacts
and deployment surfaces delivered by this repository。

Browser rendering，native App storage and other Peer-local mechanics belong to their owning
repositories。Concrete Extension protocol credentials and admission belong to the Extension's
local durable contract。

## Local Boundary Realization

### Core And Database Peer Admission

Core API and PostgREST realize one admitted Peer trust boundary。Their exact JWT claims，database
roles，grants and denial behavior are owned by the [Executable Database
Contract](../40-deployment/database-contract.md) and its executable checks。

Public health and probe routes grant only their documented observations。CORS，route naming and
Peer identifiers do not add authority。

### Extension Protocols

An Extension may expose public，Peer-authenticated or Extension-authenticated routes as defined by
the shared [Unit Topology](../_shared/20-product-tdd/unit-topology.md)。Successful Extension
authentication does not grant unrelated core authority。

Built-in Extensions run as reviewed in-process application code。The registry organizes runtime
capabilities；it does not isolate an Extension from the process or database authority intentionally
available to application code。Concrete protocol mechanics remain with the owning Extension design。

### Persistence And Credentials

PostgreSQL is inside the current deployment trust boundary when accessed by the runtime and
admitted Peers。Credentials may be ordinary access-controlled configuration when that matches the
owning protocol。Exact config authorization，logging and replacement behavior belongs to the
implementing Unit；the shared model determines whether another deployment topology requires a
stronger boundary。

### External Data And Providers

Collected text，metadata，media，filenames，Resolver input and provider responses remain untrusted
data。Collection and resolution do not make them executable or authoritative。Adapters，Resolvers，
Storages and presentation Peers each own validation where data crosses into a more privileged
interpretation。

Configured external providers receive only requests and data selected by the calling capability。
Provider credentials and response handling remain owned by that capability and its runtime config。

### Runtime And Delivery

The deployment owner，host administrator，migration authority and artifact publisher already hold
their documented operational authority。Runtime ownership，readiness，database lifecycle，credential
locations and live delivery are owned by the [Deployment documentation](../40-deployment/README.md)。

Public observations must not expose credentials，database URLs or unrelated provider failures。
Artifact，migration and dependency integrity is enforced by repository code and CI rather than
restated as prose here。

## Local Review Routing

Use the shared security model to classify an observation，then inspect the executable owner：

- database roles，JWT and Peer protocol admission：[Executable Database
  Contract](../40-deployment/database-contract.md)；
- graph，Source，Resolver and Storage authority：[Business Pipeline And
  Authority](business-pipeline-and-authority.md)；
- one Extension's protocol or credential：that Extension's local Unit design；
- runtime，health and delivery：[Deployment documentation](../40-deployment/README.md)；
- external reporting and disclosure：[Security Policy](../../SECURITY.md)。

Update this note only when core-py's realization of a shared boundary changes。Update the Hub model
first when the actor，asset，boundary or classification method changes across InKCre。
