# Disposable Hub consumer

`managed_hub_consumer.py` is an opt-in callback for the existing Storage
permission acceptance lane. It is deliberately outside pytest collection:
importing it loads only the standard library. It never launches PostgreSQL,
chooses a production store, installs dependencies, or tears down ORM users.

Infra must run this file as a **fresh direct child** inside both its genuine
`ephemeral_cluster_dsn` and `ephemeral_schema` contexts. Use the existing
qualified existing interpreter for that target; the private agent environment
uses `/uvwork/venv-agent/bin/python`. The producer owns the finite timeout, output
capture, child termination, and measured schema/cluster cleanup. The Python
audit hook is permanent and must never run inside the producer process. The
child must exit before the producer drops the schema or invokes `pg_ctl`.

The single CLI argument is an Infra-admitted JSON contract with these fields:

| Field | Required value |
| --- | --- |
| `lease_root`, `pgdata`, `producer_pid` | Existing consumer-owned 0700 lease, its real PG data directory, direct parent's PID. |
| `dsn` | Qualified password-free `postgresql://postgres@/postgres` URI with only `host=<lease Unix socket directory>` and URL-encoded `options=-csearch_path=<generated prefix_uuid12>`; no TCP/public fallback. |
| `hub_source`, `storage_source` | Existing admitted source directories, mutually disjoint from the lease. |
| `app_source` | Optional actual App checkout for a development runtime with source bindings outside the interpreter prefix; mutually disjoint from the lease. Does not alter import paths. |
| `source_sha256` | `hub` and `storage` maps of checkout-relative files to SHA256. Include all `REQUIRED_SOURCE` paths, including this callback and current owner-dirty Hub files. When `app_source` is present, also provide `app` with all three fixed `APP_RUNTIME_SOURCE` files. |
| `python`, `distribution_versions` | Actual consumer interpreter and exact Django, python-dotenv, scitex-app and psycopg versions. Producer metadata does not admit the child. |
| `runtime_files` | `dotenv_main`, `sdk_init`, `sdk_filesystem`, `sdk_plugins`: objects with actual importable `path` and `sha256` inside the interpreter prefix. Only the three named App pins may instead match their exact admitted `app_source` paths and hashes. Additional runtime pins retain the prefix requirement. |
| `notification_store_variable`, `store_environment` | `SCITEX_CARDS_NOTIFY_DSN` and exactly that key plus `SCITEX_STORE_DSN`/`SCITEX_HUB_CARDS_STORE`, all set to the qualified DSN. Cards uses the notification key only for LISTEN/NOTIFY; durable writes use the store binding. |

Source/runtime/DSN checks run before environment mutation or application imports.
The initial private runtime inspected during preparation had scitex-app0.22.1,
which lacked `scitex_app.plugins`. Later private preparation bound App0.26.1,
UI0.23.1 and SDK0.3.1, but did not qualify a complete Hub dependency graph.
The existing development Docker target imports App from `/scitex-app/src`, so
metadata wheel paths cannot stand in for those genuine source origins. This
callback uses the current legacy `scitex_app.sdk` and plugin seam; canonical
`scitex_sdk` availability is a separate migration prerequisite.
Do not replace that mount with a raw URL include. Runtime admission remains an
execution prerequisite; this source does not request a package installation.

For a container target, the producer and child must share the declared PID,
lease, Unix socket and PGDATA view. A host `docker exec` launch alone does not
establish that the consumer is the lease producer's direct child. Preserve the
real server's admitted `data_directory`; do not rewrite its identity to fit
the contract or import the callback into the cleanup-owning producer.

Initialization preserves HOME, clears ambient application/provider settings,
disables python-dotenv loading, isolates data/log/Matplotlib configuration paths,
and uses Hub's real development settings and existing Celery test mode.
Source-derived Redis/hostname probes are denied before connections and recorded
as expected fallbacks. Other denied I/O remains a failure. A credential-file read
fence also covers the independent scitex-config dotenv loader. Native libraries
are **not** an OS sandbox: libpq's explicit disposable DSN and actual server,
schema, search path and data directory are checked before Hub imports and again
through Django. An exclusively created empty lease-local `PGPASSFILE` prevents
native libpq from looking up ambient HOME credentials. The parent remains
responsible for the admitted process scope.

The callback changes only fixture filesystem roots and cache/session backing;
it preserves normal installed apps, URLconf, middleware, auth backends, CSRF,
project resolver, SDK backend and permissions. Ordinary AppConfig discovery
still runs. Startup workers must finish before a success receipt is emitted.
Unexpected discovery effects or missing dependencies fail rather than skip.

Normal migrations run in the declared schema. Authorized real ORM bulk inserts
create two ordinary Users with real password hashes, both Accounts and Auth
profiles, same-slug private Projects, and a read-only Membership. Bulk insertion
skips provisioning save/signals; it does not replace models or permission
methods. Real `/auth/login/` requests update User/profile/login history and
device/session bookkeeping. Real project selection changes the Accounts
profile's active-project FK. No ORM deletion teardown is used because it can
invoke Gitea; database connections close before child retirement.

Requests use normal mounted routes and `Client(enforce_csrf_checks=True)`.
Assertions cover exact session principals, accessible scopes, A→B→A distinct
bytes/listings/downloads, shared reads, four JSON permission refusals with
unchanged trees/memberships/selection, rejected foreign selection, actual owner
filesystem mutations, and anonymous mount login enforcement. The result line
starts with `STORAGE_CONSUMER_RECEIPT=`; other normal Hub output may precede it.
Exceptions propagate with a nonzero exit; never infer success from an exit
without the receipt. Do not publish passwords, CSRF or session values.

Preparation/offline guard checks do not execute this fixture. A future successful
run proves this disposable in-process PostgreSQL/Hub/filesystem configuration.
It does not prove serving image admission, live HTTP tenancy, cloud files,
native quota isolation, public package release, or the separate PR109 human CLA.
