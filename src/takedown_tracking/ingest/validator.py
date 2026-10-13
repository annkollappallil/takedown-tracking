"""Safe validation logic for request log entries."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from takedown_tracking.models.metrics import ValidationFailure
from takedown_tracking.models.request import (
    Category,
    IncompletionReason,
    LegalRoute,
    Platform,
    Priority,
    RequestRecord,
    RequestStatus,
    RequestType,
    parse_iso_datetime,
)

HEX64_REGEX = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)


class RequestValidator:
    """Validates raw request dictionaries against the shared data contract.

    Guarantees privacy: Never echoes unvalidated user content, real complaint IDs,
    or URLs in error logs or failure objects.
    """

    REQUIRED_FIELDS = [
        "request_id",
        "is_synthetic",
        "request_type",
        "platform",
        "category",
        "priority",
        "sent_at",
        "police_unit_id",
        "is_complete_request",
        "status",
    ]

    @classmethod
    def validate_row(
        cls, row: Dict[str, Any], row_index: int
    ) -> Tuple[Optional[RequestRecord], List[ValidationFailure]]:
        """Validate a single raw record.

        Returns (RequestRecord, []) on success, or (None, [ValidationFailure, ...]) on failure.
        """
        failures: List[ValidationFailure] = []

        # 1. Required field checks
        for req_field in cls.REQUIRED_FIELDS:
            val = row.get(req_field)
            if val is None or (isinstance(val, str) and not val.strip()):
                failures.append(
                    ValidationFailure(
                        row_index=row_index,
                        field=req_field,
                        error_code="MISSING_REQUIRED_FIELD",
                        message=f"Mandatory field '{req_field}' is missing or empty.",
                    )
                )

        if failures:
            return None, failures

        # 2. Field type and Enum validations
        # request_id
        req_id = str(row["request_id"]).strip()
        if len(req_id) < 4 or len(req_id) > 64:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="request_id",
                    error_code="INVALID_LENGTH",
                    message="request_id must be between 4 and 64 characters.",
                )
            )

        # is_synthetic
        raw_synth = row["is_synthetic"]
        if isinstance(raw_synth, bool):
            is_synth = raw_synth
        elif isinstance(raw_synth, str) and raw_synth.strip().lower() in ("true", "1", "yes"):
            is_synth = True
        elif isinstance(raw_synth, str) and raw_synth.strip().lower() in ("false", "0", "no"):
            is_synth = False
        else:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="is_synthetic",
                    error_code="INVALID_BOOLEAN",
                    message="is_synthetic must be a valid boolean value.",
                )
            )
            is_synth = False

        # request_type
        req_type_str = str(row["request_type"]).strip()
        try:
            req_type = RequestType(req_type_str)
        except ValueError:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="request_type",
                    error_code="INVALID_ENUM_VALUE",
                    message=f"Invalid request_type. Allowed: {[e.value for e in RequestType]}.",
                )
            )
            req_type = RequestType.TAKEDOWN

        # platform
        platform_str = str(row["platform"]).strip()
        try:
            platform = Platform(platform_str)
        except ValueError:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="platform",
                    error_code="INVALID_ENUM_VALUE",
                    message=f"Invalid platform. Allowed: {[e.value for e in Platform]}.",
                )
            )
            platform = Platform.OTHER

        # category
        cat_str = str(row["category"]).strip()
        try:
            category = Category(cat_str)
        except ValueError:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="category",
                    error_code="INVALID_ENUM_VALUE",
                    message=f"Invalid category. Allowed: {[e.value for e in Category]}.",
                )
            )
            category = Category.OTHER

        # priority
        prio_str = str(row["priority"]).strip()
        try:
            priority = Priority(prio_str)
        except ValueError:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="priority",
                    error_code="INVALID_ENUM_VALUE",
                    message=f"Invalid priority. Allowed: {[e.value for e in Priority]}.",
                )
            )
            priority = Priority.ROUTINE

        # legal_route (optional)
        raw_route = row.get("legal_route")
        legal_route: Optional[LegalRoute] = None
        if raw_route:
            try:
                legal_route = LegalRoute(str(raw_route).strip())
            except ValueError:
                failures.append(
                    ValidationFailure(
                        row_index=row_index,
                        field="legal_route",
                        error_code="INVALID_ENUM_VALUE",
                        message=f"Invalid legal_route. Allowed: {[e.value for e in LegalRoute]}.",
                    )
                )

        # status
        status_str = str(row["status"]).strip()
        try:
            status = RequestStatus(status_str)
        except ValueError:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="status",
                    error_code="INVALID_ENUM_VALUE",
                    message=f"Invalid status. Allowed: {[e.value for e in RequestStatus]}.",
                )
            )
            status = RequestStatus.PENDING

        # is_complete_request
        raw_comp = row["is_complete_request"]
        if isinstance(raw_comp, bool):
            is_complete = raw_comp
        elif isinstance(raw_comp, str) and raw_comp.strip().lower() in ("true", "1", "yes"):
            is_complete = True
        elif isinstance(raw_comp, str) and raw_comp.strip().lower() in ("false", "0", "no"):
            is_complete = False
        else:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="is_complete_request",
                    error_code="INVALID_BOOLEAN",
                    message="is_complete_request must be a boolean.",
                )
            )
            is_complete = False

        # incompletion_reason
        raw_inc = row.get("incompletion_reason")
        inc_reason: Optional[IncompletionReason] = None
        if raw_inc:
            try:
                inc_reason = IncompletionReason(str(raw_inc).strip())
            except ValueError:
                failures.append(
                    ValidationFailure(
                        row_index=row_index,
                        field="incompletion_reason",
                        error_code="INVALID_ENUM_VALUE",
                        message="Invalid incompletion_reason provided.",
                    )
                )

        # police_unit_id
        police_unit = str(row["police_unit_id"]).strip()

        # 3. Timestamp parsing & chronological validation
        sent_at = parse_iso_datetime(row.get("sent_at"))
        if sent_at is None:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="sent_at",
                    error_code="INVALID_TIMESTAMP",
                    message="sent_at must be an ISO 8601 UTC timestamp string.",
                )
            )

        ack_at = parse_iso_datetime(row.get("acknowledged_at"))
        if row.get("acknowledged_at") and ack_at is None:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="acknowledged_at",
                    error_code="INVALID_TIMESTAMP",
                    message="acknowledged_at could not be parsed as ISO 8601.",
                )
            )

        removal_at = parse_iso_datetime(row.get("claimed_removal_at"))
        if row.get("claimed_removal_at") and removal_at is None:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="claimed_removal_at",
                    error_code="INVALID_TIMESTAMP",
                    message="claimed_removal_at could not be parsed as ISO 8601.",
                )
            )

        data_at = parse_iso_datetime(row.get("data_provided_at"))
        if row.get("data_provided_at") and data_at is None:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="data_provided_at",
                    error_code="INVALID_TIMESTAMP",
                    message="data_provided_at could not be parsed as ISO 8601.",
                )
            )

        # Chronological order checks
        if sent_at:
            if ack_at and ack_at < sent_at:
                failures.append(
                    ValidationFailure(
                        row_index=row_index,
                        field="acknowledged_at",
                        error_code="TEMPORAL_INVERSION",
                        message="acknowledged_at cannot precede sent_at.",
                    )
                )
            if removal_at and removal_at < sent_at:
                failures.append(
                    ValidationFailure(
                        row_index=row_index,
                        field="claimed_removal_at",
                        error_code="TEMPORAL_INVERSION",
                        message="claimed_removal_at cannot precede sent_at.",
                    )
                )
            if data_at and data_at < sent_at:
                failures.append(
                    ValidationFailure(
                        row_index=row_index,
                        field="data_provided_at",
                        error_code="TEMPORAL_INVERSION",
                        message="data_provided_at cannot precede sent_at.",
                    )
                )

        # 4. Status consistency checks
        if status == RequestStatus.CLAIMED_REMOVED and removal_at is None:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="claimed_removal_at",
                    error_code="INCONSISTENT_STATUS",
                    message="Status claimed_removed requires a valid claimed_removal_at timestamp.",
                )
            )

        if status == RequestStatus.DATA_PROVIDED and data_at is None:
            failures.append(
                ValidationFailure(
                    row_index=row_index,
                    field="data_provided_at",
                    error_code="INCONSISTENT_STATUS",
                    message="Status data_provided requires a valid data_provided_at timestamp.",
                )
            )

        # 5. Content URL HMAC / hash check (must be 64-character hex or null)
        url_identifier = row.get("content_url_hmac") or row.get("content_url_hash")
        if url_identifier:
            url_identifier = str(url_identifier).strip()
            if not HEX64_REGEX.match(url_identifier):
                failures.append(
                    ValidationFailure(
                        row_index=row_index,
                        field="content_url_hmac",
                        error_code="INVALID_HASH_FORMAT",
                        message="content_url_hmac must be a 64-character hex HMAC-SHA-256 string.",
                    )
                )

        if failures:
            return None, failures

        # Construct and return validated record
        assert sent_at is not None
        record = RequestRecord(
            request_id=req_id,
            is_synthetic=is_synth,
            request_type=req_type,
            platform=platform,
            category=category,
            priority=priority,
            legal_route=legal_route,
            sent_at=sent_at,
            acknowledged_at=ack_at,
            claimed_removal_at=removal_at,
            data_provided_at=data_at,
            police_unit_id=police_unit,
            is_complete_request=is_complete,
            incompletion_reason=inc_reason,
            status=status,
            rejection_reason=row.get("rejection_reason"),
            content_url_hmac=url_identifier,
            content_url_hash=url_identifier,
        )
        return record, []
