from __future__ import annotations

from typing import Any

DEFAULT_MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024


def install_upload_limit_policy(
    app_module: Any,
    *,
    max_upload_bytes: int,
    multipart_overhead_bytes: int = DEFAULT_MULTIPART_OVERHEAD_BYTES,
) -> int:
    from werkzeug.exceptions import RequestEntityTooLarge

    file_limit = max(1, int(max_upload_bytes))
    overhead = max(0, int(multipart_overhead_bytes))
    request_limit = file_limit + overhead
    app_module.app.config["MAX_CONTENT_LENGTH"] = request_limit

    def upload_too_large(_error: RequestEntityTooLarge):
        return app_module._json_error(
            "Plik przekracza dozwolony limit rozmiaru.",
            error_code="upload_size_limit",
            status_code=413,
            phase="upload",
            retryable=False,
            extra={"max_upload_bytes": file_limit},
        )

    app_module.app.register_error_handler(RequestEntityTooLarge, upload_too_large)
    app_module._PRODUCTION_MAX_UPLOAD_BYTES = file_limit
    app_module._PRODUCTION_MAX_REQUEST_BYTES = request_limit
    return request_limit
