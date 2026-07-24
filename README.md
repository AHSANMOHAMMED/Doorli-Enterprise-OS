# Doorli Enterprise OS

This repository contains the proprietary **Doorli Enterprise OS** backend. This system acts as the isolated, Tier-2 enterprise resource planning environment tailored specifically for large-scale vendors, supermarket chains, and multi-branch retail distributors operating on the Doorli Marketplace.

## Architecture & Purpose

While small vendors utilize the lightweight Doorli POS dashboard built into our core Node.js marketplace, enterprise vendors require deep operational capabilities (advanced accounting, human resources, complex supply chain manufacturing, and multi-currency ledgers). 

This repository houses the monolithic enterprise environment required to support those operations. It is completely decoupled from the main Doorli e-commerce repository to ensure uncompromising system stability and data isolation.

- **Core Infrastructure:** High-performance Python backend, relational SQL data layer (MariaDB), and Redis-backed caching/queues.
- **Integration:** Communicates with the primary Doorli e-commerce infrastructure exclusively via secure, asynchronous REST API webhooks.
- **Custom Business Logic:** Contains the `doorli_core` proprietary service module, which enforces Doorli's specific marketplace logic, inventory deduction rules, and dynamic UI branding elements.

## Repository Structure

- `/env_docker/` - The containerized orchestration environment for deployment.
- `/apps/doorli_core/` - The proprietary Python service layer that handles incoming webhooks and enforces enterprise business rules.
- `docker-compose.yml` - Production deployment configuration.

## Getting Started

*Instructions for provisioning the local development containers will be updated by the infrastructure team.*

## Deployment Topology

This system is strictly designed to be deployed on an isolated OCI (Oracle Cloud Infrastructure) node. It must never share physical or virtual database resources with the core marketplace to prevent heavy enterprise reporting queries from degrading consumer-facing API latency.

---
*CONFIDENTIAL AND PROPRIETARY. ALL RIGHTS RESERVED. DO NOT DISTRIBUTE.*
