from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_replace_once(text: str, pattern: str, replacement: str, *, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one regex match, found {count}")
    return updated


def function_block(text: str, name: str) -> tuple[int, int, str]:
    marker = f"def {name}("
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"Missing function {name}")
    decorator_start = text.rfind("\n@app.route", 0, start)
    if decorator_start >= 0 and not text[decorator_start + 1 : start].strip().startswith("def "):
        start = decorator_start + 1
    end = text.find("\n\n@app.route", text.find("\n", start))
    if end < 0:
        end = len(text)
    return start, end, text[start:end]


def replace_function_block(text: str, name: str, transform) -> str:
    start, end, block = function_block(text, name)
    updated = transform(block)
    if updated == block:
        raise RuntimeError(f"Function {name} was not changed")
    return text[:start] + updated + text[end:]


def wrap_job_json_response(block: str, *, job_id_expression: str) -> str:
    block = replace_once(
        block,
        "    response = jsonify(\n        {",
        "    response = jsonify(_sign_job_response_links(\n        {",
        label="json response start",
    )
    block = replace_once(
        block,
        "        }\n    )\n    response.status_code",
        f"        }},\n        {job_id_expression},\n    ))\n    response.status_code",
        label="json response end",
    )
    return block


def patch_app() -> None:
    path = ROOT / "app.py"
    text = path.read_text(encoding="utf-8")

    if "from conversion_job_access import" not in text:
        text = regex_replace_once(
            text,
            r"(from conversion_jobs import \(\n.*?\n\)\n)",
            r"\1from conversion_job_access import (\n"
            r"    GUEST_ID_HEADER,\n"
            r"    JOB_ACCESS_QUERY_PARAM,\n"
            r"    JobOwner,\n"
            r"    JobOwnerResolutionError,\n"
            r"    append_job_access_token,\n"
            r"    apply_job_owner,\n"
            r"    create_job_access_token,\n"
            r"    extract_job_access_token,\n"
            r"    is_local_request_host,\n"
            r"    job_owned_by,\n"
            r"    owner_scope,\n"
            r"    resolve_job_owner,\n"
            r"    verify_job_access_token,\n"
            r")\n",
            label="job access import",
        )

    text = replace_once(
        text,
        "from flask import Flask, request, jsonify, render_template, redirect, send_file, send_from_directory",
        "from flask import Flask, request, jsonify, render_template, redirect, send_file, send_from_directory, has_request_context",
        label="flask request context import",
    )
    text = replace_once(
        text,
        'requested_headers or "Authorization, Content-Type"',
        'requested_headers or f"Authorization, Content-Type, {GUEST_ID_HEADER}"',
        label="CORS guest header",
    )

    auth_anchor = "    return validate_bearer_token(token, config=config)\n\n\ndef _json_auth_error(context: AuthContext):"
    auth_helpers = '''    return validate_bearer_token(token, config=config)\n\n\ndef _resolve_request_job_owner(auth_context: AuthContext) -> JobOwner:\n    return resolve_job_owner(\n        authenticated=auth_context.authenticated,\n        user_id=auth_context.user_id,\n        guest_id=request.headers.get(GUEST_ID_HEADER),\n        request_host=request.host,\n    )\n\n\ndef _job_owner_error_response(error: JobOwnerResolutionError, *, phase: str):\n    status_code = 401 if error.error_code == "guest_identity_required" else 400\n    return _json_error(\n        "Nie można potwierdzić właściciela sesji konwersji.",\n        error_code=error.error_code,\n        status_code=status_code,\n        phase=phase,\n    )\n\n\ndef _request_job_access_token() -> str:\n    direct_token = str(request.args.get(JOB_ACCESS_QUERY_PARAM, "") or "").strip()\n    if direct_token:\n        return direct_token\n    return extract_job_access_token(request.referrer)\n\n\ndef _has_signed_job_read_access(job_id: str) -> bool:\n    if request.method not in {"GET", "HEAD"}:\n        return False\n    return verify_job_access_token(job_id, _request_job_access_token())\n\n\ndef _sign_job_response_links(value, job_id: str, *, token: str = ""):\n    if not has_request_context() or is_local_request_host(request.host):\n        return value\n    signed_token = token or create_job_access_token(job_id)\n\n    if isinstance(value, dict):\n        return {key: _sign_job_response_links(item, job_id, token=signed_token) for key, item in value.items()}\n    if isinstance(value, list):\n        return [_sign_job_response_links(item, job_id, token=signed_token) for item in value]\n    if not isinstance(value, str):\n        return value\n\n    parsed = urllib.parse.urlsplit(value)\n    if parsed.scheme or parsed.netloc:\n        return value\n    if not parsed.path.startswith(("/convert/", "/pdf/")):\n        return value\n    if f"/{job_id}" not in parsed.path:\n        return value\n    return append_job_access_token(value, signed_token)\n\n\ndef _sign_library_payload_links(value):\n    if isinstance(value, dict):\n        job_id = str(value.get("job_id") or "").strip()\n        if job_id:\n            return _sign_job_response_links(value, job_id)\n        return {key: _sign_library_payload_links(item) for key, item in value.items()}\n    if isinstance(value, list):\n        return [_sign_library_payload_links(item) for item in value]\n    return value\n\n\ndef _json_auth_error(context: AuthContext):'''
    text = replace_once(text, auth_anchor, auth_helpers, label="request owner helpers")

    history_start = text.index("def _build_conversion_job_history_item(")
    history_end = text.index("\n\n_INTERNAL_LIBRARY_FILENAMES", history_start)
    history_block = text[history_start:history_end]
    history_block = replace_once(
        history_block,
        "    return item\n",
        "    return _sign_job_response_links(item, response_job_id)\n",
        label="history signed links",
    )
    text = text[:history_start] + history_block + text[history_end:]

    text = regex_replace_once(
        text,
        r"def _visible_conversion_jobs_snapshot\(\) -> dict:\n    return \{\n        job_id: job\n        for job_id, job in _CONVERSION_JOB_STORE\.snapshot\(\)\.items\(\)\n        if not _is_internal_library_job\(dict\(job\)\)\n    \}\n",
        '''def _visible_conversion_jobs_snapshot(auth_context: AuthContext | None = None) -> dict:\n    jobs = {\n        job_id: job\n        for job_id, job in _CONVERSION_JOB_STORE.snapshot().items()\n        if not _is_internal_library_job(dict(job))\n    }\n    if auth_context is None:\n        return jobs\n    try:\n        owner = _resolve_request_job_owner(auth_context)\n    except JobOwnerResolutionError:\n        return {}\n    return {job_id: job for job_id, job in jobs.items() if job_owned_by(job, owner)}\n''',
        label="scoped visible jobs",
    )

    text = regex_replace_once(
        text,
        r"def _build_library_payload\(\*, default_include_text: bool = False\) -> dict:\n    _mark_timed_out_conversion_jobs\(\)\n    _cleanup_expired_conversion_jobs\(\)\n    filters = _resolve_library_filters\(default_include_text=default_include_text\)\n    return build_library_index\(\n        _visible_conversion_jobs_snapshot\(\),\n        quality_state_builder=lambda job_id, job: _build_job_quality_state\(job_id, dict\(job\)\),\n        output_size_resolver=lambda job: _read_output_size_bytes\(dict\(job\)\),\n        filters=filters,\n    \)\n",
        '''def _build_library_payload(*, auth_context: AuthContext, default_include_text: bool = False) -> dict:\n    _mark_timed_out_conversion_jobs()\n    _cleanup_expired_conversion_jobs()\n    filters = _resolve_library_filters(default_include_text=default_include_text)\n    payload = build_library_index(\n        _visible_conversion_jobs_snapshot(auth_context),\n        quality_state_builder=lambda job_id, job: _build_job_quality_state(job_id, dict(job)),\n        output_size_resolver=lambda job: _read_output_size_bytes(dict(job)),\n        filters=filters,\n    )\n    return _sign_library_payload_links(payload)\n''',
        label="scoped library payload",
    )
    text = text.replace(
        "_build_library_payload(default_include_text=default_include_text)",
        "_build_library_payload(auth_context=auth_context, default_include_text=default_include_text)",
    )

    scoped_start = text.index("def _build_scoped_library_payload(")
    scoped_end = text.index("\n\ndef _get_conversion_job_for_auth", scoped_start)
    scoped_block = text[scoped_start:scoped_end]
    scoped_block = replace_once(
        scoped_block,
        "        payload = build_library_index(\n",
        "        payload = _sign_library_payload_links(build_library_index(\n",
        label="cloud library sign start",
    )
    scoped_block = replace_once(
        scoped_block,
        "            filters=_resolve_library_filters(default_include_text=default_include_text),\n        )\n        payload[\"library_scope\"]",
        "            filters=_resolve_library_filters(default_include_text=default_include_text),\n        ))\n        payload[\"library_scope\"]",
        label="cloud library sign end",
    )
    text = text[:scoped_start] + scoped_block + text[scoped_end:]

    text = regex_replace_once(
        text,
        r"def _get_conversion_job_for_auth\(job_id: str, auth_context: AuthContext\) -> dict \| None:\n.*?\n\ndef _cleanup_deleted_conversion_job_files",
        '''def _get_conversion_job_for_auth(job_id: str, auth_context: AuthContext) -> dict | None:\n    local_job = _get_conversion_job(job_id)\n    if local_job and _has_signed_job_read_access(job_id):\n        return local_job\n    try:\n        owner = _resolve_request_job_owner(auth_context)\n    except JobOwnerResolutionError:\n        return None\n    if local_job and job_owned_by(local_job, owner):\n        return local_job\n    if not auth_context.authenticated:\n        return None\n    try:\n        cloud_job = _supabase_library_client().get_user_job(user_id=auth_context.user_id, job_id=job_id)\n    except Exception:\n        return None\n    if cloud_job:\n        cloud_job.setdefault("user_id", auth_context.user_id)\n    return cloud_job\n\n\ndef _get_existing_conversion_job_for_auth(job_id: str, auth_context: AuthContext) -> dict | None:\n    local_job = _CONVERSION_JOB_STORE.get(job_id)\n    if local_job and _has_signed_job_read_access(job_id):\n        return local_job\n    try:\n        owner = _resolve_request_job_owner(auth_context)\n    except JobOwnerResolutionError:\n        return None\n    if local_job and job_owned_by(local_job, owner):\n        return local_job\n    if not auth_context.authenticated:\n        return None\n    try:\n        cloud_job = _supabase_library_client().get_user_job(user_id=auth_context.user_id, job_id=job_id)\n    except Exception:\n        return None\n    if cloud_job:\n        cloud_job.setdefault("user_id", auth_context.user_id)\n    return cloud_job\n\n\ndef _cleanup_deleted_conversion_job_files''',
        label="canonical job auth lookup",
    )

    cloud_start = text.index("def _build_cloud_jobs_payload(")
    cloud_end = text.index("\n\ndef _sync_job_to_cloud", cloud_start)
    cloud_block = text[cloud_start:cloud_end]
    cloud_block = cloud_block.replace("jobs = _visible_conversion_jobs_snapshot()", "jobs = _visible_conversion_jobs_snapshot(auth_context)")
    if "_visible_conversion_jobs_snapshot()" in cloud_block:
        raise RuntimeError("Unscoped cloud fallback remains")
    text = text[:cloud_start] + cloud_block + text[cloud_end:]

    def patch_convert_start(block: str) -> str:
        block = replace_once(
            block,
            "    if auth_context.error:\n        return _json_auth_error(auth_context)\n",
            "    if auth_context.error:\n        return _json_auth_error(auth_context)\n    try:\n        request_owner = _resolve_request_job_owner(auth_context)\n    except JobOwnerResolutionError as error:\n        return _job_owner_error_response(error, phase=\"upload\")\n",
            label="convert start owner",
        )
        block = replace_once(
            block,
            "    cloud_user, cloud_token = _authenticated_request_context()\n    cloud_user_id = str(cloud_user.get(\"id\") or \"\") if cloud_user else \"\"\n",
            "    cloud_token = resolve_bearer_token(request.headers.get(\"Authorization\"))\n    cloud_user_id = auth_context.user_id if auth_context.authenticated else \"\"\n",
            label="convert start cloud identity",
        )
        old_owner = '''    if auth_context.authenticated:\n        job_record["user_id"] = auth_context.user_id\n        job_record["auth"] = {\n            "provider": "supabase",\n            "state": "authenticated",\n            "email_masked": auth_context.email_masked,\n        }\n'''
        new_owner = '''    apply_job_owner(job_record, request_owner)\n    job_record["auth"] = {\n        "provider": "supabase" if auth_context.authenticated else "local",\n        "state": "authenticated" if auth_context.authenticated else owner_scope(request_owner),\n        "email_masked": auth_context.email_masked if auth_context.authenticated else "",\n    }\n'''
        block = replace_once(block, old_owner, new_owner, label="convert start job owner fields")
        return wrap_job_json_response(block, job_id_expression="job_id")

    text = replace_function_block(text, "convert_start", patch_convert_start)

    def patch_convert_jobs(block: str) -> str:
        block = replace_once(
            block,
            "    if auth_context.error:\n        return _json_auth_error(auth_context)\n",
            "    if auth_context.error:\n        return _json_auth_error(auth_context)\n    try:\n        _resolve_request_job_owner(auth_context)\n    except JobOwnerResolutionError as error:\n        return _job_owner_error_response(error, phase=\"library\")\n",
            label="convert jobs owner",
        )
        return block.replace("_visible_conversion_jobs_snapshot()", "_visible_conversion_jobs_snapshot(auth_context)")

    text = replace_function_block(text, "convert_jobs", patch_convert_jobs)

    def patch_delete(block: str) -> str:
        block = replace_once(
            block,
            "def convert_job_delete(job_id: str):\n    _mark_timed_out_conversion_jobs()",
            "def convert_job_delete(job_id: str):\n    auth_context = _resolve_request_auth_context()\n    if auth_context.error:\n        return _json_auth_error(auth_context)\n    try:\n        _resolve_request_job_owner(auth_context)\n    except JobOwnerResolutionError as error:\n        return _job_owner_error_response(error, phase=\"delete\")\n    _mark_timed_out_conversion_jobs()",
            label="delete auth guard",
        )
        block = replace_once(
            block,
            "    job = _get_conversion_job(job_id)\n",
            "    job = _get_conversion_job_for_auth(job_id, auth_context)\n",
            label="delete scoped lookup",
        )
        old_cloud = '''    cloud_user, cloud_token = _authenticated_request_context()\n    cloud_delete = (\n        _delete_supabase_conversion_job(cloud_token, str(cloud_user.get("id") or ""), job_id)\n        if cloud_user and cloud_token\n        else {"status": "skipped", "provider": "supabase", "reason": "anonymous_or_local"}\n    )\n'''
        new_cloud = '''    cloud_token = resolve_bearer_token(request.headers.get("Authorization"))\n    cloud_delete = (\n        _delete_supabase_conversion_job(cloud_token, auth_context.user_id, job_id)\n        if auth_context.authenticated and cloud_token\n        else {"status": "skipped", "provider": "supabase", "reason": "anonymous_or_local"}\n    )\n'''
        return replace_once(block, old_cloud, new_cloud, label="delete verified cloud owner")

    text = replace_function_block(text, "convert_job_delete", patch_delete)

    def patch_retry(block: str) -> str:
        block = replace_once(
            block,
            "def convert_retry(job_id: str):\n    _mark_timed_out_conversion_jobs()",
            "def convert_retry(job_id: str):\n    auth_context = _resolve_request_auth_context()\n    if auth_context.error:\n        return _json_auth_error(auth_context)\n    try:\n        request_owner = _resolve_request_job_owner(auth_context)\n    except JobOwnerResolutionError as error:\n        return _job_owner_error_response(error, phase=\"retry\")\n    _mark_timed_out_conversion_jobs()",
            label="retry auth guard",
        )
        block = replace_once(
            block,
            "    cloud_user, cloud_token = _authenticated_request_context()\n    cloud_user_id = str(cloud_user.get(\"id\") or \"\") if cloud_user else \"\"\n",
            "    cloud_token = resolve_bearer_token(request.headers.get(\"Authorization\"))\n    cloud_user_id = auth_context.user_id if auth_context.authenticated else \"\"\n",
            label="retry cloud identity",
        )
        block = replace_once(
            block,
            "    previous_job = _get_conversion_job(job_id)\n",
            "    previous_job = _get_conversion_job_for_auth(job_id, auth_context)\n",
            label="retry scoped lookup",
        )
        block = replace_once(
            block,
            "    retry_record[\"artifacts\"] = {\"input\": input_artifact}\n    retry_record[\"artifact_storage\"] = _artifact_storage_status()\n",
            "    retry_record[\"artifacts\"] = {\"input\": input_artifact}\n    retry_record[\"artifact_storage\"] = _artifact_storage_status()\n    apply_job_owner(retry_record, request_owner)\n    retry_record[\"auth\"] = {\n        \"provider\": \"supabase\" if auth_context.authenticated else \"local\",\n        \"state\": \"authenticated\" if auth_context.authenticated else owner_scope(request_owner),\n        \"email_masked\": auth_context.email_masked if auth_context.authenticated else \"\",\n    }\n",
            label="retry owner inheritance",
        )
        return wrap_job_json_response(block, job_id_expression="retry_job_id")

    text = replace_function_block(text, "convert_retry", patch_retry)
    text = replace_function_block(text, "convert_status", lambda block: wrap_job_json_response(block, job_id_expression="job_id"))
    text = replace_function_block(text, "convert_quality", lambda block: wrap_job_json_response(block, job_id_expression="job_id"))

    text = replace_once(
        text,
        "    html_text = _rewrite_semantic_chess_asset_urls(html_text, asset_base=asset_base)\n",
        "    html_text = _rewrite_semantic_chess_asset_urls(\n        html_text,\n        asset_base=asset_base,\n        access_token=_request_job_access_token(),\n    )\n",
        label="chess reader signed assets",
    )
    text = regex_replace_once(
        text,
        r"def _rewrite_semantic_chess_asset_urls\(html_text: str, \*, asset_base: str\) -> str:\n.*?\n    return html_text\n\n\ndef _artifact_job_dir_from_path",
        '''def _rewrite_semantic_chess_asset_urls(\n    html_text: str,\n    *,\n    asset_base: str,\n    access_token: str = "",\n) -> str:\n    replacements = {\n        'href="styles.css"': f'href="{asset_base}styles.css"',\n        "href='styles.css'": f"href='{asset_base}styles.css'",\n        'src="app.js"': f'src="{asset_base}app.js"',\n        "src='app.js'": f"src='{asset_base}app.js'",\n        'href="assets/': f'href="{asset_base}assets/',\n        "href='assets/": f"href='{asset_base}assets/",\n        'src="assets/': f'src="{asset_base}assets/',\n        "src='assets/": f"src='{asset_base}assets/",\n    }\n    for old, new in replacements.items():\n        html_text = html_text.replace(old, new)\n    if access_token:\n        escaped_base = re.escape(asset_base)\n        pattern = re.compile(rf"((?:href|src)=[\\\"'])({escaped_base}[^\\\"']+)([\\\"'])")\n        html_text = pattern.sub(\n            lambda match: f"{match.group(1)}{append_job_access_token(match.group(2), access_token)}{match.group(3)}",\n            html_text,\n        )\n    return html_text\n\n\ndef _artifact_job_dir_from_path''',
        label="chess asset URL signer",
    )

    delete_block = function_block(text, "convert_job_delete")[2]
    retry_block = function_block(text, "convert_retry")[2]
    for name, block in (("delete", delete_block), ("retry", retry_block)):
        if "_get_conversion_job(job_id)" in block:
            raise RuntimeError(f"Unscoped job lookup remains in {name} route")
        if "_get_conversion_job_for_auth(job_id, auth_context)" not in block:
            raise RuntimeError(f"Scoped job lookup missing in {name} route")

    path.write_text(text, encoding="utf-8")


def patch_frontend() -> None:
    path = ROOT / "frontend" / "src" / "App.tsx"
    text = path.read_text(encoding="utf-8")

    marker = "function App() {"
    guest_helpers = '''const GUEST_ID_HEADER = "X-KindleMaster-Guest-Id";\nconst GUEST_ID_STORAGE_KEY = "kindlemaster.guest-id.v1";\nconst GUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{19,127}$/;\n\nfunction getOrCreateGuestIdentity(): string {\n  if (typeof window === "undefined") return "";\n  const stored = window.localStorage?.getItem(GUEST_ID_STORAGE_KEY) ?? "";\n  if (GUEST_ID_PATTERN.test(stored)) return stored;\n\n  let generated = "";\n  if (typeof window.crypto?.randomUUID === "function") {\n    generated = window.crypto.randomUUID();\n  } else if (window.crypto?.getRandomValues) {\n    const bytes = new Uint8Array(24);\n    window.crypto.getRandomValues(bytes);\n    generated = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");\n  }\n  if (!GUEST_ID_PATTERN.test(generated)) return "";\n  window.localStorage?.setItem(GUEST_ID_STORAGE_KEY, generated);\n  return generated;\n}\n\n'''
    text = replace_once(text, marker, guest_helpers + marker, label="frontend guest identity helper")

    old_api_fetch = '''  async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {\n    const token = await accessTokenFromClient(authClientRef.current);\n    const requestInput = apiRequestInput(input);\n    if (!token) return fetch(requestInput, init);\n    const baseHeaders =\n      init.headers instanceof Headers\n        ? Object.fromEntries(init.headers.entries())\n        : Array.isArray(init.headers)\n          ? Object.fromEntries(init.headers)\n          : { ...(init.headers as Record<string, string> | undefined) };\n    return fetch(requestInput, {\n      ...init,\n      headers: {\n        ...baseHeaders,\n        Authorization: `Bearer ${token}`,\n      },\n    });\n  }\n'''
    new_api_fetch = '''  async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {\n    const token = await accessTokenFromClient(authClientRef.current);\n    const requestInput = apiRequestInput(input);\n    const baseHeaders =\n      init.headers instanceof Headers\n        ? Object.fromEntries(init.headers.entries())\n        : Array.isArray(init.headers)\n          ? Object.fromEntries(init.headers)\n          : { ...(init.headers as Record<string, string> | undefined) };\n    const guestIdentity = getOrCreateGuestIdentity();\n    const headers: Record<string, string> = { ...baseHeaders };\n    if (guestIdentity) headers[GUEST_ID_HEADER] = guestIdentity;\n    if (token) headers.Authorization = `Bearer ${token}`;\n    return fetch(requestInput, {\n      ...init,\n      headers,\n    });\n  }\n'''
    text = replace_once(text, old_api_fetch, new_api_fetch, label="frontend authenticated guest API fetch")
    path.write_text(text, encoding="utf-8")


def patch_docs() -> None:
    path = ROOT / "docs" / "deployment-vercel-railway.md"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "KINDLEMASTER_ARTIFACT_ROOT=/data/output/artifacts\n",
        "KINDLEMASTER_ARTIFACT_ROOT=/data/output/artifacts\nKINDLEMASTER_JOB_ACCESS_SECRET=<long-random-secret-set-in-railway-only>\nKINDLEMASTER_JOB_ACCESS_TTL_SECONDS=3600\n",
        label="deployment job access secret",
    )
    text = replace_once(
        text,
        "- Confirm backend-only values exist only in Railway or trusted server environments.\n",
        "- Confirm backend-only values exist only in Railway or trusted server environments.\n- Confirm `KINDLEMASTER_JOB_ACCESS_SECRET` is stable, random, backend-only, and differs between environments.\n- Confirm public anonymous clients send `X-KindleMaster-Guest-Id`; raw guest identifiers are hashed before persistence.\n- Confirm `KINDLEMASTER_ALLOW_LEGACY_LOCAL_GUEST` is unset or `0` on public hosts.\n",
        label="deployment ownership hardening",
    )
    text += '''\n\n## Conversion ownership and direct artifact links\n\nAuthenticated jobs are bound to the verified Supabase user ID. Anonymous browser sessions use an opaque `X-KindleMaster-Guest-Id` generated and retained by the frontend; the backend stores only its SHA-256-derived owner identifier. Public requests without either a verified user or guest identity are rejected.\n\nDirect browser navigation cannot attach an Authorization header, so authorized API responses use short-lived HMAC-signed capability URLs for local EPUB, PDF, report, and Chess Reader routes. Configure a stable `KINDLEMASTER_JOB_ACCESS_SECRET` in Railway so links remain valid across process restarts. Signed links permit read-only GET/HEAD access and never authorize delete, retry, repair, feedback, or delivery mutations.\n'''
    path.write_text(text, encoding="utf-8")


def patch_ready_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "ready-enforcement.yml"
    text = path.read_text(encoding="utf-8")
    anchor = '''      - name: Run Sprint 1 QA regression gate\n        run: python -m unittest test_sprint1_quality_gates.py\n'''
    addition = anchor + '''\n      - name: Run conversion ownership security regressions\n        run: python -m unittest test_conversion_job_access.py test_job_ownership_security.py\n'''
    text = replace_once(text, anchor, addition, label="READY ownership security tests")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_app()
    patch_frontend()
    patch_docs()
    patch_ready_workflow()


if __name__ == "__main__":
    main()
