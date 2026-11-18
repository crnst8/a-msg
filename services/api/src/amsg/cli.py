"""Host command line: serve, setup, doctor, pair, backup, restore."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import Settings, load_settings


def _settings(args: argparse.Namespace) -> Settings:
    return load_settings(
        data_dir=getattr(args, "data_dir", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        provider=getattr(args, "provider", None),
        dev_mode=getattr(args, "dev", None) or None,
    )


def _domain(settings: Settings):  # type: ignore[no-untyped-def]
    from .db.migrate import upgrade
    from .domain import Domain

    settings.ensure_dirs()
    upgrade(settings.db_path, settings.backup_dir)
    domain = Domain(settings)
    domain.seed()
    return domain


def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app
    from .logging import configure

    settings = _settings(args)
    settings.ensure_dirs()
    configure(settings.log_dir, level="DEBUG" if args.verbose else "INFO")
    app = create_app(settings)
    # Forwarded headers are read by the app, from configured proxies only (api/web.py).
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=8,
    )
    server = uvicorn.Server(config)
    if not settings.web_listen:
        server.run()
        return 0
    from .api.web import parse_listen

    web_host, web_port = parse_listen(settings.web_listen)
    if web_port == settings.port:
        print("web_listen needs a port of its own.", file=sys.stderr)
        return 2
    server.run(sockets=[_bind(settings.host, settings.port), _bind(web_host, web_port)])
    return 0


def _bind(host: str, port: int) -> socket.socket:
    """A listening socket. IP_FREEBIND lets it bind a VPN address before that interface is up."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    freebind = getattr(socket, "IP_FREEBIND", None)
    if freebind is not None:
        sock.setsockopt(socket.SOL_IP, freebind, 1)
    sock.bind((host, port))
    sock.set_inheritable(True)
    return sock


def web(args: argparse.Namespace) -> int:
    """Browser access: the public origin, the proxy listener, the password and the authenticator secret."""
    import getpass

    from .security import totp

    settings = _settings(args)
    changed = False
    if args.off:
        settings.web_origin, settings.web_listen, changed = None, None, True
    if args.origin:
        if not args.origin.startswith("https://") and not args.origin.startswith("http://127.0.0.1"):
            print("The origin must use HTTPS.", file=sys.stderr)
            return 2
        settings.web_origin, changed = args.origin.rstrip("/"), True
    if args.listen:
        settings.web_listen, changed = args.listen, True
    if args.trust is not None:
        settings.trusted_proxies, changed = [p for p in args.trust if p], True
    if args.root:
        settings.web_root, changed = Path(args.root).expanduser().resolve(), True
    if changed:
        settings.save()
    domain = _domain(settings)
    report: dict[str, object] = {}
    try:
        password_set = False
        if args.password_stdin or args.password:
            if args.password_stdin:
                password = sys.stdin.readline().rstrip("\n")
            else:
                password = getpass.getpass("New password: ")
                if getpass.getpass("Again: ") != password:
                    print("The passwords differ.", file=sys.stderr)
                    return 2
            try:
                domain.web_set_password(password)
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return 2
            report["password"] = "set"  # noqa: S105
            password_set = True
        with domain.db.read() as session:
            has_code = domain.meta_get(session, "web.totp_secret") is not None
        if args.totp_stdin or args.new_code or (password_set and not has_code):
            supplied = sys.stdin.readline().strip() if args.totp_stdin else None
            try:
                secret = domain.web_new_totp(supplied)
            except ValueError as error:
                print(str(error), file=sys.stderr)
                return 2
            if supplied:
                report["totp"] = "set"
            else:
                account = settings.web_origin.split("//", 1)[-1] if settings.web_origin else "a-msg"
                report["totp_secret"] = secret
                report["totp_uri"] = totp.provisioning_uri(secret, account)
        if args.sign_out_all:
            report["signed_out"] = domain.web_revoke_all()
        report.update(
            origin=settings.web_origin,
            listen=settings.web_listen,
            trusted_proxies=settings.trusted_proxies,
            interface=str(settings.web_files) if settings.web_files else None,
            configured=domain.web_configured(),
        )
    finally:
        domain.close()
    if args.json:
        print(json.dumps(report))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
        if "totp_secret" in report:
            print("Add the secret or the otpauth address to your authenticator (Bitwarden: Authenticator key).")
    if settings.web_enabled and not report["configured"]:
        print("Sign-in stays closed until a password is set: amsg web --password", file=sys.stderr)
    if changed:
        print("Restart the service to apply address changes.", file=sys.stderr)
    return 0


def pair(args: argparse.Namespace) -> int:
    domain = _domain(_settings(args))
    code, expires = domain.issue_pairing_code()
    domain.close()
    print(code)
    print(f"Expires {expires}. Single use.", file=sys.stderr)
    return 0


def device_token(args: argparse.Namespace) -> int:
    domain = _domain(_settings(args))
    print(domain.issue_device_token(args.name))
    domain.close()
    return 0


def connector_token(args: argparse.Namespace) -> int:
    domain = _domain(_settings(args))
    connection = args.connection
    if connection is None:
        with domain.db.read() as session:
            connection = domain.primary_connection_id(session)
    print(domain.issue_connector_token(connection, args.name))
    domain.close()
    return 0


def runner_token(args: argparse.Namespace) -> int:
    domain = _domain(_settings(args))
    print(domain.issue_runner_token(args.name))
    domain.close()
    return 0


def runner(args: argparse.Namespace) -> int:
    from .runner import main as run

    return run(args)


def doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Service version", True, __version__))
    writable = True
    try:
        settings.ensure_dirs()
        probe = settings.data_dir / ".probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as error:
        writable = False
        checks.append(("Data directory", False, str(error)))
    if writable:
        checks.append(("Data directory", True, str(settings.data_dir)))
    try:
        from .db.migrate import current_revision, head_revision

        current, head = current_revision(settings.db_path), head_revision(settings.db_path)
        checks.append(("Database schema", current in (head, None), f"{current or 'not created'} (build {head})"))
    except Exception as error:
        checks.append(("Database schema", False, str(error)))
    with socket.socket() as sock:
        sock.settimeout(0.5)
        running = sock.connect_ex((settings.host if settings.host != "0.0.0.0" else "127.0.0.1", settings.port)) == 0  # noqa: S104
    checks.append(("Service listening", running, f"{settings.host}:{settings.port}"))
    hermes = shutil.which("hermes")
    checks.append(("Hermes command", hermes is not None or settings.provider == "fake", hermes or "not found on PATH"))
    if settings.host not in ("127.0.0.1", "::1", "localhost") and not settings.public_url:
        checks.append(("Remote access", False, "Bound beyond loopback with no HTTPS public URL set"))
    ok = True
    for name, passed, detail in checks:
        ok = ok and passed
        print(f"{'ok ' if passed else 'FAIL'}  {name}: {detail}")
    return 0 if ok else 1


def setup(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if args.public_url:
        if not args.public_url.startswith("https://"):
            print("The public URL must use HTTPS.", file=sys.stderr)
            return 2
        settings.public_url = args.public_url
    if args.workspace_root:
        settings.workspace_roots = [Path(p).expanduser() for p in args.workspace_root]
    settings.save()
    domain = _domain(settings)
    with domain.db.read() as session:
        connection = domain.primary_connection_id(session)
        general = domain.general_space_id(session)
    print(f"Data: {settings.data_dir}")
    print(f"Listening address: {settings.host}:{settings.port}")
    if args.hermes_env:
        token = domain.issue_connector_token(connection, "hermes-plugin")
        target = Path(args.hermes_env).expanduser()
        lines = target.read_text().splitlines() if target.exists() else []
        owned = ("AMSG_URL=", "AMSG_CONNECTOR_TOKEN=", "AMSG_ALLOWED_USERS=", "AMSG_HOME_CHANNEL=")
        lines = [line for line in lines if not line.startswith(owned)]
        lines += [
            f"AMSG_URL=http://{settings.host}:{settings.port}",
            f"AMSG_CONNECTOR_TOKEN={token}",
            "AMSG_ALLOWED_USERS=amsg-owner",
            f"AMSG_HOME_CHANNEL={general}",
        ]
        target.write_text("\n".join(lines) + "\n")
        target.chmod(0o600)
        domain.set_connection_config(connection, config_path=str(target.parent / "config.yaml"))
        print(f"Connector credential written to {target}")
    code, expires = domain.issue_pairing_code()
    domain.close()
    print(f"Pairing code: {code} (expires {expires})")
    return 0


def backup(args: argparse.Namespace) -> int:
    from .backup import create_backup, verify_backup

    settings = _settings(args)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(args.output).expanduser() if args.output else settings.backup_dir / f"amsg-{stamp}"
    create_backup(settings, destination)
    print(json.dumps({"path": str(destination), **verify_backup(destination)}))
    return 0


def restore(args: argparse.Namespace) -> int:
    from .backup import RestoreFailed, restore_backup

    try:
        report = restore_backup(Path(args.source).expanduser(), _settings(args))
    except RestoreFailed as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(report))
    print("Device credentials were revoked. Run `amsg pair` and re-pair each device.", file=sys.stderr)
    return 0


def dev_seed(args: argparse.Namespace) -> int:
    from .devdata import seed

    if not args.dev:
        print("dev-seed requires --dev.", file=sys.stderr)
        return 2
    settings = _settings(args)
    settings.provider = "fake"
    domain = _domain(settings)
    seed(domain)
    domain.close()
    return 0


def openapi(args: argparse.Namespace) -> int:
    import tempfile

    from .api.app import create_app

    with tempfile.TemporaryDirectory() as directory:
        app = create_app(Settings(data_dir=Path(directory), provider="fake", dev_mode=True))
        print(json.dumps(app.openapi(), indent=2, sort_keys=True))
        app.state.services.domain.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="amsg", description="a-msg service")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, help_text: str):  # type: ignore[no-untyped-def]
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--data-dir")
        command.set_defaults(handler=handler)
        return command

    run = add("serve", serve, "Run the service")
    run.add_argument("--host")
    run.add_argument("--port", type=int)
    run.add_argument("--provider", choices=["hermes", "fake"])
    run.add_argument("--dev", action="store_true", help="Enable development-only features")
    run.add_argument("--verbose", action="store_true")
    add("pair", pair, "Issue a ten-minute, single-use pairing code")
    add("doctor", doctor, "Check this installation").add_argument("--port", type=int)
    conf = add("setup", setup, "Configure this host and issue a pairing code")
    conf.add_argument("--host")
    conf.add_argument("--port", type=int)
    conf.add_argument("--public-url")
    conf.add_argument("--workspace-root", action="append")
    conf.add_argument("--hermes-env", help="Hermes profile .env to receive the connector credential")
    save = add("backup", backup, "Back up the database and files")
    save.add_argument("--output")
    load = add("restore", restore, "Restore a backup into a fresh data directory")
    load.add_argument("source")
    add("device-token", device_token, "Issue a device token directly").add_argument("--name", default="dev")
    tok = add("connector-token", connector_token, "Issue a connector credential")
    tok.add_argument("--connection")
    tok.add_argument("--name", default="connector")
    add("runner-token", runner_token, "Issue the credential for a coding agent on another host").add_argument(
        "--name", required=True
    )
    work = commands.add_parser("runner", help="Run a coding agent on this host for a service on another host")
    work.add_argument("--agent", choices=["codex", "claude"], default="codex")
    work.add_argument("--root", action="append", required=True, help="A folder sessions may use. Repeatable")
    work.add_argument("--command", dest="program", help="Path to the agent program")
    work.add_argument("--sandbox", choices=["read-only", "workspace-write", "danger-full-access"])
    work.add_argument("--permission-mode", help="Claude Code permission mode")
    work.add_argument(
        "--max-access",
        choices=["read", "workspace", "full"],
        help="The highest access a session may choose. Default: the level of --sandbox or --permission-mode",
    )
    work.set_defaults(handler=runner)
    www = add("web", web, "Set up browser access: origin, proxy listener, password and authenticator code")
    www.add_argument("--origin", help="Public HTTPS origin, for example https://ag.example.net")
    www.add_argument("--listen", help="Extra listener for the HTTPS proxy, address:port")
    www.add_argument("--trust", action="append", help="Proxy address whose X-Forwarded-For is believed. Repeatable")
    www.add_argument("--root", help="Built interface folder (default: web beside the executable)")
    www.add_argument("--password", action="store_true", help="Set a new password (asks twice)")
    www.add_argument("--password-stdin", action="store_true", help="Read the new password from standard input")
    www.add_argument("--new-code", action="store_true", help="Issue a new authenticator secret")
    www.add_argument(
        "--totp-stdin",
        action="store_true",
        help="Read the authenticator secret from standard input, on the line after any --password-stdin password",
    )
    www.add_argument("--sign-out-all", action="store_true", help="End every browser session")
    www.add_argument("--off", action="store_true", help="Turn browser access off")
    www.add_argument("--json", action="store_true")
    add("dev-seed", dev_seed, "Load development fixtures").add_argument("--dev", action="store_true")
    add("openapi", openapi, "Print the OpenAPI document")
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
