"""Opt-in genuine Hub consumer; run in a fresh child of an Infra-owned PG lease.

No Django, Hub, provider or database import occurs at module import. The caller
supplies the admitted source hashes and an already-created disposable schema.
This module never creates/stops a cluster, chooses a store, or deletes ORM users.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import secrets
import stat
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_RUNTIME_SOURCE = {
    "sdk_init": "src/scitex_app/sdk/__init__.py",
    "sdk_filesystem": "src/scitex_app/sdk/_filesystem.py",
    "sdk_plugins": "src/scitex_app/plugins.py",
}

REQUIRED_SOURCE = {
    "hub": {
        "config/urls.py", "config/settings/settings_dev.py",
        "config/settings/settings_shared.py", "config/settings/settings_auth.py",
        "config/settings/settings_middleware.py", "config/settings/settings_celery.py",
        "config/settings/settings_logging.py", "config/settings/_optional_apps.py",
        "apps/workspace/apps_app/services/plugin_guards.py",
        "apps/workspace/apps_app/services/plugin_apps.py",
        "apps/infra/auth_app/models.py", "apps/infra/auth_app/views/authentication.py",
        "apps/infra/accounts_app/models/profile.py",
        "apps/infra/project_app/models/repository/project.py",
        "apps/infra/project_app/models/repository/project_methods.py",
        "apps/infra/project_app/models/projects/collaboration.py",
        "apps/infra/project_app/services/project_utils.py",
        "apps/infra/project_app/services/project_scope.py",
        "apps/infra/project_app/views/projects/scope_api.py",
    },
    "storage": {"src/scitex_storage/_django/project_files.py",
                "src/scitex_storage/_django/urls.py",
                    "src/scitex_storage/_django/manifest.json",
                "tests/integration/managed_hub_consumer.py"},
    "app": set(APP_RUNTIME_SOURCE.values()),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_contract(contract):
    """Reject ambient/TCP/public targets before importing or changing anything."""
    lease = Path(contract["lease_root"]).resolve(strict=True)
    require(lease.is_dir() and lease != Path("/"), "lease root must already exist")
    require(lease.stat().st_uid == os.getuid() and stat.S_IMODE(lease.stat().st_mode) ==
        0o700,
            "lease root must be private and owned by the consumer uid")
    require(contract["producer_pid"] == os.getppid() and contract[
        "producer_pid"] != os.getpid(),
            "consumer must be a fresh direct child of the declared lease producer")
    uri = urlparse(contract["dsn"])
    query = parse_qs(uri.query, strict_parsing=True, keep_blank_values=True)
    require(uri.scheme == "postgresql" and uri.hostname is None,
            "consumer requires a private Unix-only PostgreSQL DSN")
    require(uri.username == "postgres" and uri.password is None and uri.port is None,
            "consumer requires the qualified password-free disposable PG user")
    require(uri.netloc == "postgres@" and not uri.fragment,
            "unexpected DSN authority or fragment")
    require(set(query) == {"host", "options"} and all(len(v) == 1 for v in query.values(
        )),
            "DSN must declare only one Unix socket and one schema search_path")
    socket_dir = Path(query["host"][0]).resolve(strict=True)
    require(socket_dir.is_dir() and socket_dir.is_relative_to(lease),
            "Unix socket must belong to this lease")
    options = query["options"][0]
    require(re.fullmatch(r"-csearch_path=[a-z][a-z0-9_]*_[0-9a-f]{12}", options),
            "only the generated disposable schema may be on search_path")
    schema = options.split("=", 1)[1]
    pgdata = Path(contract["pgdata"]).resolve(strict=True)
    require(pgdata.is_relative_to(lease) and (pgdata / "PG_VERSION").is_file(),
            "qualified PG data directory must belong to this lease")
    require(uri.path == "/postgres", "qualified Dev cluster database must be postgres")
    require(("app_source" in contract) == ("app" in contract.get("source_sha256", {})),
            "App source directory and source hashes must be admitted together")
    labels = ("hub", "storage", "app") if "app_source" in contract else ("hub", "storage")
    for label in labels:
        source = Path(contract[f"{label}_source"]).resolve(strict=True)
        require(source.is_dir() and source != Path("/") and not source.is_relative_to(lease) and
            not lease.is_relative_to(source),
                "source and disposable data must be mutually disjoint")
        pins = contract["source_sha256"][label]
        require(REQUIRED_SOURCE[label] <= pins.keys(),
                f"missing admitted {label} source hashes")
        for relative, expected in pins.items():
            path = (source / relative).resolve(strict=True)
            require(path.is_relative_to(source), "source pin escapes its checkout")
            require(hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                    f"admitted source hash differs: {label}/{relative}")
    validate_store_environment(contract)
    require(Path(contract["python"]).resolve() == Path(sys.executable).resolve(),
            "consumer interpreter differs from admission")
    for package in ("Django", "python-dotenv", "scitex-app", "psycopg"):
        require(importlib.metadata.version(package) == contract[
            "distribution_versions"][package],
                f"consumer distribution differs: {package}")
    runtime = contract["runtime_files"]
    require({"dotenv_main", "sdk_init", "sdk_filesystem",
        "sdk_plugins"} <= runtime.keys(),
            "admit the actual installed SDK including its normal plugin API")
    for name, pin in runtime.items():
        path = Path(pin["path"]).resolve(strict=True)
        if "app_source" in contract and name in APP_RUNTIME_SOURCE:
            relative = APP_RUNTIME_SOURCE[name]
            admitted = (Path(contract["app_source"]) / relative).resolve(strict=True)
            require(path == admitted and pin["sha256"] == contract[
                "source_sha256"]["app"][relative],
                    "runtime pin differs from the exact admitted App source")
        else:
            require(path.is_relative_to(Path(sys.prefix).resolve()),
                    "runtime pin is outside this interpreter or admitted App source")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == pin["sha256"],
                f"runtime source hash differs: {path.name}")
    return lease, socket_dir, pgdata, schema


def validate_store_environment(contract):
    stores = contract["store_environment"]
    require(stores.get("SCITEX_STORE_DSN") == contract["dsn"] and
            stores.get("SCITEX_HUB_CARDS_STORE") == contract["dsn"],
            "package-store bindings must match the disposable DSN")
    require(contract["notification_store_variable"] == "SCITEX_CARDS_NOTIFY_DSN"
            and stores.get("SCITEX_CARDS_NOTIFY_DSN") == contract["dsn"],
            "Cards LISTEN/NOTIFY must use its actual disposable DSN override")
    require(set(stores) == {"SCITEX_STORE_DSN", "SCITEX_HUB_CARDS_STORE",
                            contract["notification_store_variable"]},
            "only explicitly declared store channels may be overridden")
    require(all(re.fullmatch(r"SCITEX_(?:[A-Z0-9]+_)*(?:STORE|DSN)", key) and value ==
        contract["dsn"]
                for key, value in stores.items()),
                    "unexpected store environment override")


class ConsumerFence:
    """Refuse external I/O in this fresh consumer, not in the lease producer.

    Audit hooks are permanent. Never call this consumer in the process whose
    finally block owns pg_ctl cleanup. Native libpq is checked separately through
    the explicit DB binding and server identity, not claimed covered by this hook.
    """

    def __init__(self, lease, socket_dir, hub):
        self.lease, self.socket_dir, self.hub = lease, socket_dir, hub
        self.settings_import = True
        self.refused_bootstrap = []
        self.violations = []

    def path(self, value, dir_fd=None):
        if isinstance(value, int):
            value = os.readlink(f"/proc/self/fd/{value}")
        path = Path(os.fsdecode(value))
        if not path.is_absolute() and dir_fd not in (None, -1):
            path = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / path
        return path.resolve()

    def refuse(self, event, args=()):
        frame = sys._getframe(2)
        settings_call = False
        while frame is not None:
            if Path(frame.f_code.co_filename) in {
                self.hub / "config/settings/settings_shared.py",
                self.hub / "config/settings/settings_dev.py",
            }:
                settings_call = True
                break
            frame = frame.f_back
        redis_probe = ((event == "socket.connect" and len(args) > 1 and
                        args[1] == ("127.0.0.1", 6379)) or
                       (event == "socket.getaddrinfo" and args[:2] == ("127.0.0.1",
                           6379)))
        dev_hostname = (event == "socket.gethostbyname" and frame is not None and
                        Path(frame.f_code.co_filename) == self.hub /
                            "config/settings/settings_dev.py" and
                        frame.f_code.co_name == "<module>" and frame.f_lineno == 113)
        if self.settings_import and settings_call and (redis_probe or dev_hostname):
            self.refused_bootstrap.append(event)
        else:
            self.violations.append(event)
        raise PermissionError(f"disposable consumer refused {event}")

    def __call__(self, event, args):
        if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.fork",
            "os.forkpty", "os.exec"}:
            self.refuse(event)
        if event in {"socket.getaddrinfo", "socket.gethostbyname",
            "socket.gethostbyaddr"}:
            self.refuse(event, args)
        if event in {"socket.connect", "socket.bind", "socket.sendto"}:
            address = args[1]
            if not isinstance(address, str) or not self.path(address).is_relative_to(
                self.socket_dir):
                self.refuse(event, args)
        if event == "open":
            path, _mode, flags = args
            target = self.path(path)
            writing = bool(flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and not target.is_relative_to(self.lease):
                self.refuse("write-outside-lease")
            requested = Path(os.fsdecode(path)) if not isinstance(path, int) else target
            if not target.is_relative_to(self.lease) and (
                requested.name.startswith(".env") or target.name.startswith(".env") or
                "SECRET" in target.parts or any(part in target.parts for part in
                (".ssh", ".hermes", ".scitex", ".aws", ".azure", ".kube", ".netrc"))
            ):
                self.refuse("ambient-credential-file")
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod"}:
            fd = args[2] if event in {"os.mkdir", "os.chmod"} else args[1]
            if not self.path(args[0], fd).is_relative_to(self.lease):
                self.refuse("filesystem-outside-lease")
        if event == "os.rename":
            if any(not self.path(path, fd).is_relative_to(self.lease)
                   for path, fd in ((args[0], args[2]), (args[1], args[3]))):
                self.refuse("rename-outside-lease")
        if event in {"os.truncate", "os.utime", "os.chown"}:
            fd = args[3] if event == "os.utime" else args[
                3] if event == "os.chown" else None
            if not self.path(args[0], fd).is_relative_to(self.lease):
                self.refuse("filesystem-outside-lease")
        if event in {"os.symlink", "os.link"}:
            fd = args[2] if event == "os.symlink" else args[3]
            if not self.path(args[1], fd).is_relative_to(self.lease):
                self.refuse("link-outside-lease")
            if event == "os.link" and not self.path(args[0], args[2]).is_relative_to(
                self.lease):
                self.refuse("hardlink-from-outside-lease")

    def check(self):
        require(not self.violations,
                f"unexpected consumer I/O refusals: {self.violations}")


def initialize(contract):
    roots = {"django", "config", "apps", "scitex_storage", "scitex_app", "dotenv",
             "scitex", "scitex_config", "figrecipe", "psycopg"}
    require(not any(name.split(".", 1)[0] in roots for name in sys.modules),
            "consumer requires a fresh child without preloaded application modules")
    require("scitex_dev.store.testing" not in sys.modules,
            "do not install consumer audit fence in the PG lease producer")
    lease, socket_dir, pgdata, schema = validate_contract(contract)
    hub = Path(contract["hub_source"]).resolve()
    storage = Path(contract["storage_source"]).resolve()
    retained = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL")
                if key in os.environ}
    os.environ.clear()
    os.environ.update(retained)  # HOME is preserved, never repointed.
    os.environ.update(contract["store_environment"])
    os.environ.update({
        "DJANGO_SETTINGS_MODULE": "config.settings.settings_dev",
        "PYTHON_DOTENV_DISABLED": "1", "SCITEX_HUB_TEST_MODE": "1",
        "SCITEX_HUB_ENV": "development", "SCITEX_HUB_DJANGO_DEBUG": "true",
        "SCITEX_HUB_DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "SCITEX_HUB_DB_HOST_DEV": str(socket_dir), "SCITEX_HUB_DB_NAME_DEV": "postgres",
        "SCITEX_HUB_DB_USER_DEV": "postgres", "SCITEX_HUB_DB_PASSWORD_DEV": "",
        "SCITEX_HUB_DB_PORT_DEV": "5432", "SCITEX_HUB_DB_SCHEMA_DEV": schema,
        "SCITEX_HUB_LOG_DIR": str(lease / "logs"),
        "SCITEX_HUB_USER_DATA_ROOT": str(lease / "files/data"),
        "SCITEX_DIR": str(lease / "state"), "MPLCONFIGDIR": str(lease / "mpl"),
        "SCITEX_HUB_GITEA_SSH_PORT_DEV": "22", "SCITEX_HUB_VITE_HOST_IP": "127.0.0.1",
        "SCITEX_HUB_ALLOWED_HOSTS": "testserver",
            "SCITEX_HUB_SITE_URL": "http://testserver",
        "SCITEX_HUB_REDIS_URL": "redis://127.0.0.1:6379/0",
        "SCITEX_INSTANCE_NAME": "storage-disposable-consumer",
        "PGPASSFILE": str(lease / "empty-consumer.pgpass"),
    })
    sys.dont_write_bytecode = True
    os.chdir(lease)
    sys.path[:0] = [str(storage / "src"), str(hub)]
    fence = ConsumerFence(lease, socket_dir, hub)
    sys.addaudithook(fence)
    # libpq may read HOME/.pgpass natively even when the DSN has no password.
    # Exclusive creation prevents reusing any caller-provided credential file.
    with open(os.environ["PGPASSFILE"], "x"):
        os.chmod(os.environ["PGPASSFILE"], 0o600)
    # Native libpq does not emit Python socket audit events. Verify its explicit
    # lease DSN before Hub ready() hooks can query any relational model.
    import psycopg
    with psycopg.connect(contract["dsn"], connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            verify_database(cursor, schema, pgdata)
    # Fail before Hub import if this runtime cannot actually disable dotenv.
    dotenv = importlib.import_module("dotenv.main")
    require(Path(dotenv.__file__).resolve() == Path(contract["runtime_files"][
        "dotenv_main"]["path"]).resolve(),
            "actual dotenv source differs from admission")
    require(dotenv._load_dotenv_disabled(), "runtime must honor PYTHON_DOTENV_DISABLED")
    dev = importlib.import_module("config.settings.settings_dev")
    require(Path(dev.__file__).resolve() == hub / "config/settings/settings_dev.py",
            "actual Hub settings source differs from admission")
    fence.settings_import = False
    dev.BASE_DIR = lease / "files"
    dev.USER_DATA_ROOT = dev.BASE_DIR / "data"
    dev.CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                               "LOCATION": schema}}
    dev.SESSION_ENGINE = "django.contrib.sessions.backends.db"
    require(dev.ROOT_URLCONF == "config.urls", "normal Hub URLconf must remain intact")
    db = dev.DATABASES
    require(set(db) == {"default"}, "consumer must have only its declared DB alias")
    require(db["default"]["HOST"] == str(socket_dir) and
            db["default"]["NAME"] == "postgres" and db["default"]["USER"] ==
                "postgres" and
            db["default"]["ENGINE"] == "django.db.backends.postgresql" and
            db["default"]["PASSWORD"] == "" and str(db["default"]["PORT"]) == "5432" and
            db["default"]["OPTIONS"]["options"] == f"-c search_path={schema}",
            "Hub relational binding differs from lease")
    django = importlib.import_module("django")
    django.setup()
    for module_name, pin_name in (("scitex_app.sdk", "sdk_init"),
                                  ("scitex_app.plugins", "sdk_plugins")):
        actual_module = importlib.import_module(module_name)
        require(Path(actual_module.__file__).resolve() ==
                Path(contract["runtime_files"][pin_name]["path"]).resolve(),
                f"actual SDK module differs from admission: {module_name}")
    from django.db import connection
    with connection.cursor() as cursor:
        verify_database(cursor, schema, pgdata)
    quiesce()
    fence.check()
    return lease, fence


def verify_database(cursor, schema, pgdata):
    cursor.execute("SELECT current_database(), current_schema(), inet_server_addr(), "
                   "current_setting('search_path'), current_setting('data_directory')")
    database, actual_schema, address, search_path, actual_data = cursor.fetchone()
    require((database, actual_schema, address, search_path) == ("postgres", schema,
        None, schema)
            and Path(actual_data).resolve() == pgdata,
                "actual DB identity differs from lease")


def quiesce():
    """Never report success with an application startup worker still running."""
    workers = [t for t in threading.enumerate() if t is not threading.current_thread()]
    for worker in workers:
        worker.join(timeout=1)
    require(not any(t.is_alive() for t in workers),
        "consumer startup worker is still running")


def snapshot(root):
    return {str(p.relative_to(root)): ("symlink", os.readlink(p)) if p.is_symlink() else
            ("dir", None) if p.is_dir() else
            ("file", hashlib.sha256(p.read_bytes()).hexdigest()) if p.is_file() else
            ("other", stat.S_IFMT(p.lstat().st_mode))
            for p in sorted(root.rglob("*"))}


def consume(contract):
    """Migrate and exercise genuine in-process HTTP requests inside the lease."""
    try:
        lease, fence = initialize(contract)
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.test import Client
        from apps.infra.accounts_app.models import UserProfile as AccountsProfile
        from apps.infra.auth_app.models import LoginHistory, UserProfile as AuthProfile
        from apps.infra.project_app.models import Project, ProjectMembership
        from scitex_storage._django import project_files

        require(Path(project_files.__file__).resolve() ==
                Path(contract["storage_source"]).resolve() /
                    "src/scitex_storage/_django/project_files.py",
                "actual Storage source differs from admission")
        require(project_files._GET_CURRENT_PROJECT is None,
            "fake resolver is forbidden")
        require(project_files._current_project_fn().__module__ ==
                "apps.infra.project_app.services.project_utils",
                    "actual Hub resolver required")
        resolver = importlib.import_module(project_files._current_project_fn(
            ).__module__)
        require(Path(resolver.__file__).resolve() == Path(contract[
            "hub_source"]).resolve() /
                "apps/infra/project_app/services/project_utils.py",
                    "actual resolver source differs")
        call_command("migrate", interactive=False, verbosity=0)
        fence.check()
        User = get_user_model()
        require(not User.objects.exists(), "consumer schema must start with no users")
        nonce, password = secrets.token_hex(6), secrets.token_urlsafe(32)
        users = [User(username=f"storage-{label}-{nonce}",
                      email=f"{label}@storage.invalid",
                      is_active=True, is_staff=False, is_superuser=False) for label in (
                          "a", "b")]
        for user in users:
            user.set_password(password)
        User.objects.bulk_create(users)
        AccountsProfile.objects.bulk_create([AccountsProfile(user=u) for u in users])
        AuthProfile.objects.bulk_create([AuthProfile(user=u) for u in users])
        roots = [lease / "files/data/users" / u.username / "proj/shared" for u in users]
        projects = [Project(owner=u, name=f"fixture {label}", slug="shared",
            visibility="private",
                            local_path=str(root), gitea_enabled=False, is_home=False)
                    for u, root, label in zip(users, roots, ("A", "B"))]
        Project.objects.bulk_create(projects)
        ProjectMembership.objects.bulk_create([ProjectMembership(
            project=projects[0], user=users[1], invited_by=users[0],
            role="viewer", permission_level="read")])
        require(projects[0].can_edit(users[0]) is True and projects[1].can_edit(users[
            1]) is True
                and projects[0].can_edit(users[1]) is False,
                    "real fixture permissions differ")
        for root, label in zip(roots, ("A", "B")):
            root.mkdir(parents=True)
            (root / "shared.txt").write_text(f"only project {label}\n")
            (root / f"{label}-only.txt").write_text(f"{label} sentinel\n")

        clients = [Client(enforce_csrf_checks=True) for _ in users]
        checks = []

        def request(index, method, path, body=None, status=200):
            client = clients[index]
            kwargs = {} if body is None else {
                "data": json.dumps(body), "content_type": "application/json",
                "HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
            response = getattr(client, method)(path, **kwargs)
            require(response.status_code == status,
                    f"{method} {path}: expected {status}, got {response.status_code}")
            require(response.wsgi_request.user.pk == users[index].pk and
                    str(client.session["_auth_user_id"]) == str(users[index].pk),
                    "response/session principal mismatch")
            fence.check()
            checks.append({"actor": index, "method": method, "route": path,
                "status": status})
            return response

        for i, (client, user) in enumerate(zip(clients, users)):
            require(client.get("/auth/signin/").status_code == 200 and
                "csrftoken" in client.cookies,
                    "normal sign-in did not establish CSRF")
            response = client.post("/auth/login/", {"username": user.username,
                "password": password,
                "csrfmiddlewaretoken": client.cookies["csrftoken"].value})
            require(response.status_code == 302 and str(client.session[
                "_auth_user_id"]) == str(user.pk),
                    "normal login did not establish intended session")
            require(LoginHistory.objects.filter(user=user).count() == 1 and
                    AuthProfile.objects.get(user=user).total_login_count == 1,
                    "normal auth history/profile effects missing")
            expected = {f"{users[0].username}/shared"}
            if i == 1:
                expected.add(f"{user.username}/shared")
            require({p["id"] for p in request(i, "get", "/api/project/scope/").json()[
                "projects"]} == expected,
                    "accessible-project listing differs")
        profiles = [AccountsProfile.objects.get(user=u) for u in users]
        require(profiles[0].public_id != profiles[1].public_id,
            "principals must be distinct")

        def select(actor, owner, status=200):
            key = f"{users[owner].username}/shared"
            before = AccountsProfile.objects.get(user=users[
                actor]).last_active_repository_id
            response = request(actor, "post", "/api/project/scope/", {"id": key},
                status)
            if status == 200:
                require(response.json() == {"current": key} and
                        AccountsProfile.objects.get(user=users[
                            actor]).last_active_repository_id == projects[owner].pk,
                        "actual profile project selection differs")
            else:
                require(response.json() == {"error": "project not accessible"} and
                        AccountsProfile.objects.get(user=users[
                            actor]).last_active_repository_id == before,
                        "selection refusal changed the real active project")

        def read(actor, owner):
            response = request(actor, "get", "/apps/storage/api/read?path=shared.txt")
            payload = response.json()
            expected = f"only project {'AB'[owner]}\n"
            require((payload["project"]["id"], payload["project"]["slug"], payload[
                "path"],
                     payload["content"], payload["size"]) ==
                    (str(projects[owner].pk), "shared", "shared.txt", expected, len(
                        expected.encode())),
                    "actual selected project/content differs")
            scope = project_files.resolve_project_scope(response.wsgi_request)
            backend = project_files.build_backend(scope)
            require(type(backend).__module__ == "scitex_app.sdk._filesystem" and
                    type(backend).__name__ == "FileSystemBackend" and backend.root ==
                        roots[owner].resolve(),
                    "unpatched backend/selected owner root differs")
            actual_backend_module = importlib.import_module(type(backend).__module__)
            require(Path(actual_backend_module.__file__).resolve() ==
                    Path(contract["runtime_files"]["sdk_filesystem"]["path"]).resolve(),
                    "actual SDK backend source differs from admission")
            listing = request(actor, "get", "/apps/storage/api/list").json()
            require(listing["project"]["id"] == str(projects[owner].pk) and len(listing[
                "entries"]) == 2 and
                    {(e["name"], e["path"], e["type"]) for e in listing["entries"]} ==
                    {(name, name, "file") for name in
                     ("shared.txt", f"{'AB'[owner]}-only.txt")},
                    "actual owner directory listing differs")
            download = request(actor, "get",
                "/apps/storage/api/download?path=shared.txt")
            require(download.content == expected.encode(),
                "actual owner download differs")

        select(0, 0)
        select(1, 1)
        for actor in (0, 1, 0):
            read(actor, actor)
        select(1, 0)
        read(1, 0)
        mutations = {"write": {"path": "shared.txt", "content": "unexpected"},
                     "rename": {"old_path": "shared.txt", "new_path": "moved.txt"},
                     "delete": {"path": "shared.txt"}, "mkdir": {"path": "blocked-dir"}}
        def permissions():
            return (list(Project.objects.order_by("pk").values_list(
                "pk", "owner_id", "local_path", "visibility")),
                    list(ProjectMembership.objects.order_by("pk").values_list(
                        "project_id", "user_id", "role", "permission_level")))

        for action, body in mutations.items():
            before = [snapshot(root) for root in roots]
            memberships = permissions()
            selection = AccountsProfile.objects.get(user=users[
                1]).last_active_repository_id
            response = request(1, "post", f"/apps/storage/api/{action}", body, 403)
            require(response.json().get("error") == "permission_denied",
                "generic denial does not prove can_edit")
            require(before == [snapshot(root) for root in roots] and
                    memberships == permissions() and
                    selection == AccountsProfile.objects.get(user=users[
                        1]).last_active_repository_id,
                    "read-only denial changed fixture files/permissions/selection")
        select(0, 1, 403)
        read(0, 0)
        select(1, 1)
        before = [snapshot(root) for root in roots]
        for action, body in (("write", {"path": "fresh.txt",
            "content": "owner write\n"}),
                             ("rename", {"old_path": "fresh.txt",
                                 "new_path": "moved.txt"}),
                             ("mkdir", {"path": "fresh-dir"}), ("delete", {
                                 "path": "moved.txt"}),
                             ("delete", {"path": "fresh-dir"})):
            require(request(1, "post", f"/apps/storage/api/{action}", body).json()[
                "project"]["id"] == str(projects[1].pk),
                    "owner mutation project differs")
            if action == "write":
                require((roots[1] / "fresh.txt").read_bytes() == b"owner write\n",
                    "owner write was a no-op")
            elif action == "rename":
                require(not (roots[1] / "fresh.txt").exists() and
                        (roots[1] / "moved.txt").read_bytes() == b"owner write\n",
                            "owner rename was a no-op")
            elif action == "mkdir":
                require((roots[1] / "fresh-dir").is_dir(), "owner mkdir was a no-op")
            else:
                require(not (roots[1] / body["path"]).exists(),
                    "owner delete was a no-op")
            require(before[0] == snapshot(roots[0]),
                "owner mutation crossed into A root")
        require(before == [snapshot(root) for root in roots],
            "owner cleanup did not restore synthetic roots")
        anonymous = Client(enforce_csrf_checks=True).get(
            "/apps/storage/api/read?path=shared.txt")
        require(anonymous.status_code == 302 and "/auth/login/" in anonymous[
            "Location"],
                "normal anonymous mount boundary differs")
        quiesce()
        fence.check()
        return {"kind": "genuine-disposable-in-process-hub-consumer", "passed": True,
                "actors": [str(p.public_id) for p in profiles], "project_ids": [str(
                    p.pk) for p in projects],
                "checks": checks, "refused_settings_probes": fence.refused_bootstrap,
                "module_origins": {"project_files": project_files.__file__,
                    "resolver": project_files._current_project_fn().__module__},
                "binding": {"schema": parse_qs(urlparse(contract["dsn"]).query)[
                    "options"][0].split("=", 1)[1],
                            "python": sys.executable, "producer_pid": os.getppid()},
                "admitted_source_sha256": contract["source_sha256"],
                "admitted_runtime_files": contract["runtime_files"],
                "production_runtime_acceptance": False,
                    "cloud_file_backend_acceptance": False}
    finally:
        if "django.db" in sys.modules:
            sys.modules["django.db"].connections.close_all()


if __name__ == "__main__":
    require(len(sys.argv) == 2,
        "usage: managed_hub_consumer.py admitted-lease-contract.json")
    context = json.loads(Path(sys.argv[1]).read_text())
    print("STORAGE_CONSUMER_RECEIPT=" + json.dumps(consume(context), sort_keys=True))
