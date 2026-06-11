# RFC: Migrate tenant ingress from NGINX to Istio

We propose migrating tenant routing for the platform from NGINX ingress
controllers to an Istio service mesh. This will give us mTLS between
services, richer traffic policy, and per-tenant ABAC enforcement at the mesh
layer. The migration guarantees zero downtime for all tenants.

## Context

The platform currently serves 40 tenants through a shared NGINX ingress
layer. Policy enforcement happens in application middleware, which has
drifted across services. Latency through the current path averages 12 ms.

## Decision

Adopt Istio with a phased, per-tenant migration. Each tenant moves behind an
Istio gateway with a VirtualService per seal. Authorization moves to
AuthorizationPolicy resources generated from the ABAC policy store. We
expect a 30% reduction in policy-related incidents.

## Alternatives

We considered staying on NGINX with OPA sidecars, and a managed API gateway.
NGINX+OPA keeps the data plane simple but leaves mTLS unsolved. The managed
gateway introduces a hard vendor dependency and per-request pricing.

## Migration plan

Tenants migrate in cohorts of five per week. A canary cohort runs for two
weeks with TODO success criteria. DNS cutover happens per tenant.

## Operations

Mesh telemetry flows into the existing dashboards. SLO is 99.95% for the
ingress path.
