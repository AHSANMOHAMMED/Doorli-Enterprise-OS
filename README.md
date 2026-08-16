# Doorli Enterprise OS

Doorli Enterprise OS is Doorli's isolated enterprise ERP platform for vendors, retailers, supermarkets, and multi-branch businesses. It runs Frappe/ERPNext with Doorli's `doorli_core` integration and branding layer.

## What This Repository Does

- Runs the Enterprise ERP at `https://enterprise.doorli.me`.
- Stores enterprise data separately from the Doorli consumer marketplace.
- Provides standard ERPNext accounting, buying, selling, stock, CRM, manufacturing, quality, assets, projects, support, users, reports, and settings.
- Receives marketplace vendor provisioning and orders through authenticated APIs.
- Sends order and delivery status updates back to Doorli.
- Exposes tenant-aware inventory and control-plane operations.
- Provides a Doorli-branded, beginner-friendly visual Desk workspace.

## Architecture

| Service | Purpose |
|---|---|
| `backend` | Frappe Python application and Doorli API methods |
| `frontend` | Nginx public frontend and WebSocket proxy |
| `db` | MariaDB 10.6 ERP database |
| `redis-cache` | Frappe cache |
| `redis-queue` | Background jobs and queues |
| `queue-*` | Background workers |
| `scheduler` | Scheduled ERP jobs |
| `websocket` | Realtime Desk updates |
| `traefik` | HTTPS ingress for `enterprise.doorli.me` |

The deployment is defined in `docker-compose.yml`. The custom app is in `apps/doorli_core` and is copied into the image by `Dockerfile`.

## Doorli Integration

The custom API lives in `apps/doorli_core/doorli_core/api.py`.

| Operation | Frappe method | Purpose |
|---|---|---|
| Provision vendor | `doorli_core.api.provision_vendor` | Creates a Company and scoped vendor user |
| Create order | `doorli_core.api.create_order` | Creates an idempotent ERP Sales Order |
| Update order | `doorli_core.api.update_order_status` | Applies marketplace status updates |
| Read inventory | `doorli_core.api.get_inventory` | Returns Company/warehouse-scoped stock |
| Tenant status | `doorli_core.api.control_status` | Reads tenant status and maintenance state |
| Tenant control | `doorli_core.api.control_tenant` | Applies tenant lifecycle settings |
| Module control | `doorli_core.api.control_module` | Enables or disables modules |
| Quota control | `doorli_core.api.control_quota` | Reads or updates tenant quotas |

All integration calls require the production `X-Doorli-Secret`. Do not place secrets in Git, README files, screenshots, or chat.

## Local Development

Requirements:

- Docker and Docker Compose
- Git
- A configured `.env` file

```bash
cp .env.example .env
# Set DB_ROOT_PASSWORD, DB_PASSWORD, ADMIN_PASSWORD,
# DOORLI_WEBHOOK_SECRET, and DOORLI_MARKETPLACE_ORDER_STATUS_URL.
docker compose up -d db redis-cache redis-queue
docker compose run --rm configurator
docker compose run --rm create-site
docker compose up -d backend frontend queue-default queue-long queue-short scheduler websocket
```

Open `http://localhost` after the frontend is ready. The production URL is `https://enterprise.doorli.me`.

## Deploying

Before deployment:

- Confirm `.env` contains real non-placeholder values.
- Confirm DNS points `enterprise.doorli.me` to the Enterprise OCI host.
- Confirm the Doorli Marketplace callback URL is reachable.
- Confirm database and Redis volumes have a current backup.

Build and refresh assets:

```bash
docker compose exec backend bench build --apps frappe,erpnext,doorli_core --production --force
docker compose exec backend bench --site enterprise.doorli.me clear-cache
```

The `sites` volume is the authoritative asset location. Do not add a second volume mounted at `sites/assets`; that masks generated Frappe and ERPNext assets and causes CSS/JS 404s.

## Roles

Use native Frappe permissions. Do not give every user Administrator access.

- `System Manager`: configuration, users, workspaces, and system setup
- `Accounts User` / `Accounts Manager`: money and invoices
- `Sales User` / `Sales Manager`: customers and sales
- `Purchase User` / `Purchase Manager`: suppliers and buying
- `Stock User` / `Stock Manager`: items, warehouses, and movements
- `Report Manager`: reports and analytics
- `HR User`: staff workflows
- `Projects User`: jobs and tasks
- `Administrator`: emergency unrestricted platform administration

Passwords cannot be recovered. Reset them from the User form and never commit them.

## Verification

```bash
curl -k https://enterprise.doorli.me/api/method/frappe.ping
curl -k -I https://enterprise.doorli.me/app/home
docker compose ps
docker compose logs --since=10m backend frontend
```

Authenticated checks should cover Home, Accounting, Stock, Selling, Buying, CRM, Support, Manufacturing, Quality, Assets, Users, Website, Integrations, and Settings.

## Important Limitations

- Payment, SMS, email, maps, storage, AI, and delivery providers require their real production credentials.
- Enterprise product catalog synchronization requires a configured Marketplace catalog contract.
- Backups and restore testing must be managed at the OCI/database level.
- Native ERPNext forms remain underneath the Doorli visual workspace to preserve ERP validation and permissions.

## Security

- Never commit `.env`, passwords, API keys, or production database exports.
- Rotate `DOORLI_WEBHOOK_SECRET` if it is exposed.
- Keep the Enterprise database isolated from Marketplace databases.
- Review user roles quarterly and disable test accounts before production use.
