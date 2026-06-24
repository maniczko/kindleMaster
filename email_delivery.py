from __future__ import annotations

import hashlib
import os
import re
import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime, make_msgid
from email.message import EmailMessage
from pathlib import Path
from typing import Mapping

from local_env import resolve_runtime_environment
from user_profile import load_user_profile


DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_SECURITY = "starttls"
DEFAULT_EMAIL_TIMEOUT_SECONDS = 30
DEFAULT_EMAIL_MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
VALID_SMTP_SECURITY = {"starttls", "ssl", "none"}
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
EMAIL_IN_TEXT_RE = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")


class EmailDeliveryError(Exception):
    def __init__(self, code: str, message: str, *, diagnostics: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class EmailDeliveryConfig:
    enabled: bool
    host: str
    port: int
    security: str
    username: str
    password: str
    from_address: str
    max_attachment_bytes: int
    timeout_seconds: int = DEFAULT_EMAIL_TIMEOUT_SECONDS
    provider: str = "smtp"
    profile_configured: bool = False
    config_source: str = "env"
    secret_registered: bool = False

    @property
    def missing_config(self) -> list[str]:
        if not self.enabled:
            return []
        missing: list[str] = []
        if not self.host:
            missing.append("KINDLEMASTER_SMTP_HOST")
        if not self.username:
            missing.append("KINDLEMASTER_SMTP_USERNAME")
        if not self.password and not self.secret_registered:
            missing.append("KINDLEMASTER_SMTP_PASSWORD")
        if not self.from_address:
            missing.append("KINDLEMASTER_SMTP_FROM")
        return missing

    @property
    def configured(self) -> bool:
        return self.enabled and not self.missing_config

    def to_public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "send_ready": bool(self.enabled and self.host and self.username and self.from_address and self.password),
            "provider": self.provider,
            "host_configured": bool(self.host),
            "from_configured": bool(self.from_address),
            "secret_configured": bool(self.password),
            "secret_registered": bool(self.secret_registered or self.password),
            "profile_configured": self.profile_configured,
            "config_source": self.config_source,
            "port": self.port,
            "security": self.security,
            "max_attachment_bytes": self.max_attachment_bytes,
            "missing_config": self.missing_config,
        }


@dataclass(frozen=True)
class EmailDeliveryResult:
    status: str
    channel: str
    target: str
    masked_recipient: str
    recipient_hash: str
    sent_at: str
    attachment_filename: str
    attachment_size_bytes: int
    diagnostics: dict = field(default_factory=dict)

    def to_public_dict(self) -> dict:
        return {
            "status": self.status,
            "channel": self.channel,
            "target": self.target,
            "masked_recipient": self.masked_recipient,
            "recipient_hash": self.recipient_hash,
            "sent_at": self.sent_at,
            "attachment_filename": self.attachment_filename,
            "attachment_size_bytes": self.attachment_size_bytes,
            "diagnostics": self.diagnostics,
        }


def load_email_delivery_config(environ: Mapping[str, str] | None = None) -> EmailDeliveryConfig:
    environment = resolve_runtime_environment(environ)
    profile = load_user_profile(environment).get("email_delivery", {})
    profile_configured = bool(
        profile.get("enabled")
        or profile.get("host")
        or profile.get("username")
        or profile.get("from_address")
    )
    secret_registered = _truthy(profile.get("secret_registered", ""))
    env_used = any(
        str(environment.get(name, "") or "").strip()
        for name in (
            "KINDLEMASTER_EMAIL_DELIVERY",
            "KINDLEMASTER_SMTP_HOST",
            "KINDLEMASTER_SMTP_PORT",
            "KINDLEMASTER_SMTP_SECURITY",
            "KINDLEMASTER_SMTP_USERNAME",
            "KINDLEMASTER_SMTP_FROM",
            "KINDLEMASTER_EMAIL_MAX_ATTACHMENT_BYTES",
        )
    ) or bool(str(environment.get("KINDLEMASTER_SMTP_PASSWORD", "") or ""))
    enabled = (
        _truthy(environment.get("KINDLEMASTER_EMAIL_DELIVERY", ""))
        if "KINDLEMASTER_EMAIL_DELIVERY" in environment
        else bool(profile.get("enabled", False))
    )
    security = _env_or_profile_text(
        environment,
        "KINDLEMASTER_SMTP_SECURITY",
        profile.get("security", DEFAULT_SMTP_SECURITY),
    ).lower()
    if security not in VALID_SMTP_SECURITY:
        security = DEFAULT_SMTP_SECURITY
    host = _env_or_profile_text(environment, "KINDLEMASTER_SMTP_HOST", profile.get("host", ""))
    username = _env_or_profile_text(environment, "KINDLEMASTER_SMTP_USERNAME", profile.get("username", ""))
    from_address = _env_or_profile_text(environment, "KINDLEMASTER_SMTP_FROM", profile.get("from_address", ""))
    config_source = "env+profile" if env_used and profile_configured else "env" if env_used else "profile" if profile_configured else "defaults"
    return EmailDeliveryConfig(
        enabled=enabled,
        host=host,
        port=_positive_int(
            environment.get("KINDLEMASTER_SMTP_PORT")
            if "KINDLEMASTER_SMTP_PORT" in environment
            else profile.get("port"),
            DEFAULT_SMTP_PORT,
        ),
        security=security,
        username=username,
        password=str(environment.get("KINDLEMASTER_SMTP_PASSWORD", "") or ""),
        from_address=from_address,
        max_attachment_bytes=_positive_int(
            environment.get("KINDLEMASTER_EMAIL_MAX_ATTACHMENT_BYTES")
            if "KINDLEMASTER_EMAIL_MAX_ATTACHMENT_BYTES" in environment
            else profile.get("max_attachment_bytes"),
            DEFAULT_EMAIL_MAX_ATTACHMENT_BYTES,
        ),
        timeout_seconds=_positive_int(environment.get("KINDLEMASTER_EMAIL_TIMEOUT_SECONDS"), DEFAULT_EMAIL_TIMEOUT_SECONDS),
        profile_configured=profile_configured,
        config_source=config_source,
        secret_registered=secret_registered,
    )


def validate_single_email_address(value: str) -> str:
    address = str(value or "").strip()
    if not address or "," in address or ";" in address or "<" in address or ">" in address or not EMAIL_RE.match(address):
        raise EmailDeliveryError("invalid_delivery_request", "Podaj pojedynczy poprawny adres email.")
    return address


def mask_email_address(value: str) -> str:
    address = validate_single_email_address(value)
    local, domain = address.split("@", 1)
    first = local[:1] or "*"
    return f"{first}***@{domain.lower()}"


def recipient_hash(value: str) -> str:
    address = validate_single_email_address(value).lower()
    return hashlib.sha256(address.encode("utf-8")).hexdigest()


def send_epub_email(
    *,
    config: EmailDeliveryConfig,
    to_address: str,
    subject: str,
    body: str,
    attachment_path: str | Path,
    attachment_filename: str,
) -> EmailDeliveryResult:
    return send_attachment_email(
        config=config,
        to_address=to_address,
        subject=subject,
        body=body,
        attachment_path=attachment_path,
        attachment_filename=attachment_filename,
        attachment_content_type="application/epub+zip",
        default_subject="KindleMaster EPUB",
        default_body="EPUB wygenerowany przez KindleMaster jest w zalaczniku.",
        attachment_label="EPUB",
    )


def send_attachment_email(
    *,
    config: EmailDeliveryConfig,
    to_address: str,
    subject: str,
    body: str,
    attachment_path: str | Path,
    attachment_filename: str,
    attachment_content_type: str,
    default_subject: str = "KindleMaster",
    default_body: str = "Dokument wygenerowany przez KindleMaster jest w zalaczniku.",
    attachment_label: str = "plik",
) -> EmailDeliveryResult:
    if not config.enabled:
        raise EmailDeliveryError("delivery_unavailable", "Wysylka email jest wylaczona.")
    if not config.configured:
        raise EmailDeliveryError("delivery_unavailable", "Konfiguracja SMTP jest niekompletna.")

    recipient = validate_single_email_address(to_address)
    path = Path(attachment_path)
    if not path.is_file():
        raise EmailDeliveryError("delivery_not_ready", f"Brak pliku {attachment_label} do wysylki.")
    size_bytes = path.stat().st_size
    if size_bytes > config.max_attachment_bytes:
        raise EmailDeliveryError("delivery_not_ready", f"Plik {attachment_label} przekracza limit zalacznika email.")
    maintype, subtype = _split_content_type(attachment_content_type)

    message = EmailMessage()
    message["From"] = config.from_address
    message["To"] = recipient
    message["Subject"] = _safe_header(subject, default=default_subject)
    message["Date"] = format_datetime(datetime.now(UTC), usegmt=True)
    message["Message-ID"] = make_msgid(domain=_message_id_domain(config.from_address))
    message.set_content(str(body or default_body))
    attachment_bytes = path.read_bytes()
    message.add_attachment(
        attachment_bytes,
        maintype=maintype,
        subtype=subtype,
        filename=attachment_filename or path.name,
    )

    refused_recipients: dict = {}
    try:
        if config.security == "ssl":
            smtp_factory = smtplib.SMTP_SSL
        else:
            smtp_factory = smtplib.SMTP
        with smtp_factory(config.host, config.port, timeout=config.timeout_seconds) as smtp:
            if config.security == "starttls":
                smtp.starttls()
            smtp.login(config.username, config.password)
            raw_refused = smtp.send_message(message)
            refused_recipients = raw_refused if isinstance(raw_refused, Mapping) else {}
            if refused_recipients:
                diagnostics = _build_delivery_diagnostics(
                    config=config,
                    recipient=recipient,
                    message=message,
                    attachment_path=path,
                    attachment_filename=attachment_filename or path.name,
                    attachment_size_bytes=size_bytes,
                    smtp_refused=refused_recipients,
                )
                raise EmailDeliveryError(
                    "delivery_failed",
                    "Serwer SMTP odmowil przyjecia wiadomosci dla odbiorcy.",
                    diagnostics=diagnostics,
                )
    except EmailDeliveryError:
        raise
    except Exception as error:
        diagnostics = _build_delivery_diagnostics(
            config=config,
            recipient=recipient,
            message=message,
            attachment_path=path,
            attachment_filename=attachment_filename or path.name,
            attachment_size_bytes=size_bytes,
            smtp_error=f"{error.__class__.__name__}: {error}",
        )
        raise EmailDeliveryError("delivery_failed", "Nie udalo sie wyslac emaila przez SMTP.", diagnostics=diagnostics) from error

    diagnostics = _build_delivery_diagnostics(
        config=config,
        recipient=recipient,
        message=message,
        attachment_path=path,
        attachment_filename=attachment_filename or path.name,
        attachment_size_bytes=size_bytes,
        smtp_refused=refused_recipients,
    )
    return EmailDeliveryResult(
        status="sent",
        channel="email",
        target="send_to_kindle",
        masked_recipient=mask_email_address(recipient),
        recipient_hash=recipient_hash(recipient),
        sent_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        attachment_filename=attachment_filename or path.name,
        attachment_size_bytes=size_bytes,
        diagnostics=diagnostics,
    )


def _build_delivery_diagnostics(
    *,
    config: EmailDeliveryConfig,
    recipient: str,
    message: EmailMessage,
    attachment_path: Path,
    attachment_filename: str,
    attachment_size_bytes: int,
    smtp_refused: Mapping | None = None,
    smtp_error: str = "",
) -> dict:
    attachments = list(message.iter_attachments())
    attachment = attachments[0] if attachments else None
    refused = smtp_refused or {}
    safe_error = _safe_diagnostic_text(smtp_error)
    response_summary = "transport_error" if safe_error else "recipient_refused" if refused else "all_recipients_accepted"
    return {
        "smtp": {
            "host": config.host,
            "port": config.port,
            "security": config.security,
            "starttls": config.security == "starttls",
            "ssl": config.security == "ssl",
            "from_matches_smtp_username": _email_identity_match(config.from_address, config.username),
            "accepted_by_smtp": not bool(refused or safe_error),
            "response_summary": response_summary,
            "error": safe_error,
            "refused_recipient_count": len(refused),
            "refused_recipients": _summarize_refused_recipients(refused),
        },
        "message": {
            "from": _mask_email_or_empty(config.from_address),
            "to": mask_email_address(recipient),
            "subject": str(message.get("Subject", "") or ""),
            "has_date_header": bool(message.get("Date")),
            "has_message_id_header": bool(message.get("Message-ID")),
            "content_type": message.get_content_type(),
            "is_multipart": message.is_multipart(),
            "has_plain_text_body": any(part.get_content_type() == "text/plain" for part in message.walk()),
            "attachment_count": len(attachments),
            "raw_mime_logged": False,
            "raw_mime_reason": "redacted_to_avoid_recipient_or_document_leak",
        },
        "attachment": {
            "filename": attachment.get_filename() if attachment else attachment_filename,
            "content_type": attachment.get_content_type() if attachment else "",
            "content_disposition": attachment.get_content_disposition() if attachment else "",
            "content_transfer_encoding": str(attachment.get("Content-Transfer-Encoding", "") or "") if attachment else "",
            "size_bytes": attachment_size_bytes,
            "sha256": _sha256_file(attachment_path),
        },
    }


def _summarize_refused_recipients(refused: Mapping) -> list[dict]:
    summary: list[dict] = []
    for recipient, value in refused.items():
        code = ""
        text = ""
        if isinstance(value, tuple) and value:
            code = str(value[0])
            text = _safe_diagnostic_text(str(value[1] if len(value) > 1 else ""))
        summary.append(
            {
                "recipient": _mask_email_or_empty(str(recipient)),
                "smtp_code": code,
                "smtp_message": text,
            }
        )
    return summary


def _mask_email_or_empty(value: str) -> str:
    try:
        return mask_email_address(value)
    except EmailDeliveryError:
        return ""


def _email_identity_match(left: str, right: str) -> bool | None:
    try:
        return validate_single_email_address(left).lower() == validate_single_email_address(right).lower()
    except EmailDeliveryError:
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_diagnostic_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = EMAIL_IN_TEXT_RE.sub(lambda match: _mask_email_or_empty(match.group(0)) or "***@***", text)
    return text[:180]


def _message_id_domain(from_address: str) -> str:
    try:
        return validate_single_email_address(from_address).split("@", 1)[1].lower()
    except EmailDeliveryError:
        return "kindlemaster.local"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None, default: int) -> int:
    try:
        converted = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return converted if converted > 0 else default


def _env_or_profile_text(environment: Mapping[str, str], env_name: str, profile_value: object) -> str:
    if env_name in environment:
        return str(environment.get(env_name, "") or "").strip()
    return str(profile_value or "").strip()


def _safe_header(value: str, *, default: str) -> str:
    text = str(value or "").strip() or default
    return re.sub(r"[\r\n]+", " ", text)[:180]


def _split_content_type(value: str) -> tuple[str, str]:
    content_type = str(value or "").strip().lower()
    if "/" not in content_type:
        return ("application", "octet-stream")
    maintype, subtype = content_type.split("/", 1)
    maintype = re.sub(r"[^a-z0-9!#$&^_.+-]", "", maintype) or "application"
    subtype = re.sub(r"[^a-z0-9!#$&^_.+-]", "", subtype) or "octet-stream"
    return maintype, subtype
