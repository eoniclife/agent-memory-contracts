"""Evidence object contracts and standard-library validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .evidence_ids import make_episode_id, make_source_id, make_span_id

SCHEMA_VERSION = "1.0.0"

SOURCE_TYPES = {
    "chatgpt_conversation",
    "codex_session",
    "email_thread",
    "whatsapp_thread",
    "meeting_transcript",
    "bookmark",
    "web_article",
    "pdf",
    "repo_diff",
    "manual_note",
    "other",
}
RAW_REF_KINDS = {"local_path", "external_uri", "redacted_fixture", "synthetic_fixture", "content_hash_only"}
PRIVACY_CLASSES = {"public", "internal", "private", "sensitive", "highly_sensitive"}
CUSTODY_STATUSES = {"synthetic", "redacted", "local_ignored", "external_pointer", "unavailable"}
EPISODE_TYPES = {
    "conversation_segment",
    "meeting_segment",
    "email_thread",
    "bookmark_event",
    "task_event",
    "decision_event",
    "document_section",
    "other",
}
LOCATOR_KINDS = {
    "line_range",
    "byte_range",
    "message_range",
    "timestamp_range",
    "page_range",
    "url_fragment",
    "synthetic_locator",
}
EPISODE_LOCATOR_KINDS = {
    "ordinal",
    "line_range",
    "message_range",
    "timestamp_range",
    "page_range",
    "section_id",
    "synthetic_locator",
}
EXCERPT_POLICIES = {"none", "synthetic", "redacted", "short_quote_allowed"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_iso8601_or_none(value: str | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _validate_string_list(name: str, value: list[str]) -> None:
    _require(isinstance(value, list), f"{name} must be a list")
    for item in value:
        _require(isinstance(item, str), f"{name} entries must be strings")


def _validate_sha256(name: str, value: str) -> None:
    _require(isinstance(value, str), f"{name} must be a string")
    _require(len(value) == 64, f"{name} must be 64 hex chars")
    int(value, 16)


def _build_record(cls: type, data: dict[str, Any]):
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"invalid {cls.__name__}: {exc}") from exc


@dataclass(frozen=True)
class SourceRecord:
    id: str
    schema_version: str
    source_type: str
    title: str
    origin_uri: str | None
    raw_ref: dict[str, str]
    content_hash_sha256: str
    captured_at: str
    observed_at: str | None
    author_or_sender: str | None
    participants: list[str]
    privacy_class: str
    custody_status: str
    parser_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRecord":
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "SourceRecord schema_version must be 1.0.0")
        _require(self.source_type in SOURCE_TYPES, "invalid source_type")
        _require(isinstance(self.title, str) and self.title, "title is required")
        _require(self.origin_uri is None or isinstance(self.origin_uri, str), "origin_uri must be string or null")
        _require(isinstance(self.raw_ref, dict), "raw_ref must be object")
        _require(self.raw_ref.get("kind") in RAW_REF_KINDS, "invalid raw_ref.kind")
        _require(isinstance(self.raw_ref.get("value"), str), "raw_ref.value must be string")
        _validate_sha256("content_hash_sha256", self.content_hash_sha256)
        _require(_is_iso8601_or_none(self.captured_at), "captured_at must be ISO-8601")
        _require(_is_iso8601_or_none(self.observed_at), "observed_at must be ISO-8601 or null")
        _require(self.author_or_sender is None or isinstance(self.author_or_sender, str), "author_or_sender invalid")
        _validate_string_list("participants", self.participants)
        _require(self.privacy_class in PRIVACY_CLASSES, "invalid privacy_class")
        _require(self.custody_status in CUSTODY_STATUSES, "invalid custody_status")
        _require(isinstance(self.parser_version, str) and self.parser_version, "parser_version is required")
        _require(isinstance(self.metadata, dict), "metadata must be object")
        expected = make_source_id(self.source_type, self.raw_ref if self.origin_uri is None else self.origin_uri, self.content_hash_sha256)
        _require(self.id == expected, f"SourceRecord id mismatch: expected {expected}")


@dataclass(frozen=True)
class EpisodeRecord:
    id: str
    schema_version: str
    source_id: str
    episode_type: str
    episode_locator: dict[str, str]
    title: str
    summary: str
    event_time_start: str | None
    event_time_end: str | None
    actors: list[str]
    topics: list[str]
    project_refs: list[str]
    evidence_span_ids: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EpisodeRecord":
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "EpisodeRecord schema_version must be 1.0.0")
        _require(self.source_id.startswith("src_"), "source_id must reference SourceRecord")
        _require(self.episode_type in EPISODE_TYPES, "invalid episode_type")
        _require(isinstance(self.episode_locator, dict), "episode_locator must be object")
        _require(self.episode_locator.get("kind") in EPISODE_LOCATOR_KINDS, "invalid episode_locator.kind")
        _require(
            isinstance(self.episode_locator.get("value"), str) and bool(self.episode_locator.get("value")),
            "episode_locator.value is required",
        )
        _require(isinstance(self.title, str) and self.title, "title is required")
        _require(isinstance(self.summary, str) and self.summary, "summary is required")
        _require(_is_iso8601_or_none(self.event_time_start), "event_time_start must be ISO-8601 or null")
        _require(_is_iso8601_or_none(self.event_time_end), "event_time_end must be ISO-8601 or null")
        _validate_string_list("actors", self.actors)
        _validate_string_list("topics", self.topics)
        _validate_string_list("project_refs", self.project_refs)
        _validate_string_list("evidence_span_ids", self.evidence_span_ids)
        for span_id in self.evidence_span_ids:
            _require(span_id.startswith("span_"), "evidence_span_ids must reference EvidenceSpan")
        _require(isinstance(self.metadata, dict), "metadata must be object")
        _require("locator_or_ordinal" not in self.metadata, "metadata must not contain identity-critical locator_or_ordinal")
        expected = make_episode_id(
            self.source_id,
            self.episode_type,
            self.episode_locator["kind"],
            self.episode_locator["value"],
        )
        _require(self.id == expected, f"EpisodeRecord id mismatch: expected {expected}")


@dataclass(frozen=True)
class EvidenceSpan:
    id: str
    schema_version: str
    source_id: str
    episode_id: str | None
    locator: dict[str, str]
    text_excerpt: str | None
    excerpt_policy: str
    span_hash_sha256: str
    privacy_class: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceSpan":
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "EvidenceSpan schema_version must be 1.0.0")
        _require(self.source_id.startswith("src_"), "source_id must reference SourceRecord")
        _require(self.episode_id is None or self.episode_id.startswith("ep_"), "episode_id must reference EpisodeRecord or null")
        _require(isinstance(self.locator, dict), "locator must be object")
        _require(self.locator.get("kind") in LOCATOR_KINDS, "invalid locator.kind")
        _require(isinstance(self.locator.get("value"), str) and self.locator.get("value"), "locator.value is required")
        _require(self.text_excerpt is None or isinstance(self.text_excerpt, str), "text_excerpt must be string or null")
        _require(self.excerpt_policy in EXCERPT_POLICIES, "invalid excerpt_policy")
        if self.text_excerpt is not None:
            _require(self.excerpt_policy in {"synthetic", "redacted", "short_quote_allowed"}, "text excerpts require explicit policy")
        _validate_sha256("span_hash_sha256", self.span_hash_sha256)
        _require(self.privacy_class in PRIVACY_CLASSES, "invalid privacy_class")
        _require(isinstance(self.metadata, dict), "metadata must be object")
        expected = make_span_id(self.source_id, self.locator["kind"], self.locator["value"])
        _require(self.id == expected, f"EvidenceSpan id mismatch: expected {expected}")
