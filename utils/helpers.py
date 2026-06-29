"""
utils/helpers.py — Shared Utility Functions

This module contains small, reusable helper functions that support
the rest of the application. Nothing here is Flask-route-specific;
every function is pure logic that can be imported and called from
anywhere in the project:

    - app.py
    - utils/pdf_extractor.py
    - utils/ai_analyzer.py
    - utils/report_generator.py

Design principle: write logic once, use it everywhere.
"""

import os
import uuid
from datetime import datetime, timezone
from flask import jsonify


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The only file extension this application accepts.
# Stored as a set for O(1) lookup — efficient even if more types are added later.
ALLOWED_EXTENSIONS: set[str] = {"pdf"}


# ---------------------------------------------------------------------------
# 1. allowed_file
# ---------------------------------------------------------------------------

def allowed_file(filename: str) -> bool:
    """
    Check whether a filename has a permitted extension.

    The check is case-insensitive, so 'Resume.PDF' is treated the same
    as 'resume.pdf'. A bare name with no dot (e.g., 'resume') returns False
    because os.path.splitext returns an empty extension string in that case.

    Args:
        filename: The original filename from the uploaded file.

    Returns:
        True if the extension is in ALLOWED_EXTENSIONS, False otherwise.

    Examples:
        >>> allowed_file("resume.pdf")   # True
        >>> allowed_file("resume.docx")  # False
        >>> allowed_file("resume")       # False
    """
    # os.path.splitext("resume.pdf") → ("resume", ".pdf")
    # We strip the leading dot and lowercase it: ".PDF" → "pdf"
    _, extension = os.path.splitext(filename)

    # An empty extension string (no dot in name) will not be in the set.
    return extension.lstrip(".").lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# 2. generate_unique_filename
# ---------------------------------------------------------------------------

def generate_unique_filename(filename: str) -> str:
    """
    Generate a safe, unique filename to prevent collisions in the uploads folder.

    Two files named 'resume.pdf' uploaded by different users would overwrite
    each other without this. UUID4 guarantees uniqueness; removing spaces
    prevents URL-encoding issues on some operating systems.

    Strategy:
        <uuid4_hex>_<sanitized_original_name>.<lowercase_extension>

    Args:
        filename: The original filename provided by the user.

    Returns:
        A new filename that is unique, space-free, and lowercase-extended.

    Example:
        "My Resume.pdf"  →  "4f8f29d6a1c24d6c9b8e_my resume.pdf"
        (UUID prefix changes every call)
    """
    # Split the original name and its extension apart.
    original_name, extension = os.path.splitext(filename)

    # Normalize: lowercase extension, remove spaces from the base name.
    clean_extension: str = extension.lower()                  # ".PDF" → ".pdf"
    clean_name: str = original_name.replace(" ", "_")         # "My Resume" → "My_Resume"

    # uuid4() generates a random 128-bit value; .hex gives a clean 32-char string.
    unique_prefix: str = uuid.uuid4().hex

    return f"{unique_prefix}_{clean_name}{clean_extension}"


# ---------------------------------------------------------------------------
# 3. validate_uploaded_file
# ---------------------------------------------------------------------------

def validate_uploaded_file(file) -> tuple[bool, str]:
    """
    Validate a Flask FileStorage object before processing it.

    Runs three sequential checks. The first check that fails immediately
    returns (False, error_message) so the caller gets a precise reason.

    Args:
        file: A werkzeug.datastructures.FileStorage object from Flask's
              request.files dictionary. May be None if nothing was uploaded.

    Returns:
        A tuple of (is_valid: bool, message: str).
        On success  → (True, "")
        On failure  → (False, "Human-readable error message")

    Usage in a route:
        is_valid, error_msg = validate_uploaded_file(request.files.get("resume"))
        if not is_valid:
            return error_response(error_msg, 400)
    """
    # Check 1: Was any file object included in the request at all?
    if file is None:
        return False, "No file uploaded."

    # Check 2: Does the file have a name?
    # An empty filename means the user submitted the form without choosing a file.
    if file.filename == "" or file.filename is None:
        return False, "Filename is empty."

    # Check 3: Is the file's extension in our allowed list?
    if not allowed_file(file.filename):
        return False, "Only PDF files are allowed."

    # All checks passed.
    return True, ""


# ---------------------------------------------------------------------------
# 4. success_response
# ---------------------------------------------------------------------------

def success_response(message: str, data: dict | None = None, status_code: int = 200):
    """
    Build a standardized JSON success response for every API endpoint.

    Keeping all responses in the same shape makes frontend integration
    predictable — the client always knows exactly where to find the message
    and the payload.

    Args:
        message:     A short human-readable description of what happened.
        data:        Optional dictionary of response payload. Defaults to {}.
        status_code: HTTP status code to send. Defaults to 200 OK.

    Returns:
        A Flask Response object (from jsonify) with the given status code.

    Response format:
        {
            "success": true,
            "timestamp": "2026-06-29T14:20:31",
            "status": 200,
            "message": "Resume analyzed successfully.",
            "data": { ... }
        }
    """
    # Replace None with an empty dict so the frontend always gets a "data" key.
    payload: dict = data if data is not None else {}

    # ISO 8601 timestamp (UTC, no microseconds) for every response —
    # useful for logs, debugging, and client-side caching decisions.
    timestamp: str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    return jsonify({
        "success":   True,
        "timestamp": timestamp,
        "status":    status_code,
        "message":   message,
        "data":      payload,
    }), status_code


# ---------------------------------------------------------------------------
# 5. error_response
# ---------------------------------------------------------------------------

def error_response(message: str, status_code: int = 400):
    """
    Build a standardized JSON error response for every API endpoint.

    Using this function (instead of ad-hoc jsonify calls) ensures error
    responses are always structurally identical to success responses,
    just with success=false and no data field.

    Args:
        message:     A human-readable explanation of what went wrong.
        status_code: HTTP status code to send. Defaults to 400 Bad Request.

    Returns:
        A Flask Response object (from jsonify) with the given status code.

    Response format:
        {
            "success": false,
            "timestamp": "2026-06-29T14:20:31",
            "status": 400,
            "message": "Only PDF files are allowed."
        }
    """
    timestamp: str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    return jsonify({
        "success":   False,
        "timestamp": timestamp,
        "status":    status_code,
        "message":   message,
    }), status_code


# ---------------------------------------------------------------------------
# 6. get_file_size
# ---------------------------------------------------------------------------

def get_file_size(file) -> float:
    """
    Calculate the size of an uploaded file in megabytes.

    Flask's FileStorage object wraps a stream. We seek to the end to get
    the byte count, then seek back to the beginning so the file can still
    be read by subsequent code (e.g., the PDF extractor).

    Args:
        file: A werkzeug.datastructures.FileStorage object.

    Returns:
        File size in MB, rounded to 2 decimal places.

    Example:
        A 2,621,440-byte file → 2.50 MB
    """
    # Move the stream cursor to the very end; tell() returns that byte offset.
    file.stream.seek(0, os.SEEK_END)
    size_in_bytes: int = file.stream.tell()

    # Reset the cursor to the start so the file can be read normally afterward.
    file.stream.seek(0)

    # Convert bytes → megabytes and round for a clean display value.
    size_in_mb: float = round(size_in_bytes / (1024 * 1024), 2)
    return size_in_mb


# ---------------------------------------------------------------------------
# 7. format_analysis_result
# ---------------------------------------------------------------------------

def format_analysis_result(result: dict) -> dict:
    """
    Normalize the raw AI analysis dictionary before returning it to the frontend.

    The Gemini API response may be incomplete or inconsistently structured
    (e.g., a field may be missing if the model skipped it). This function
    ensures every expected key is always present with a sensible default,
    so the frontend never crashes from a missing key.

    Args:
        result: Raw dictionary parsed from the Gemini AI response.

    Returns:
        A clean, fully-populated dictionary with all expected keys.

    Expected keys and their default values:
        resume_score    → 0          (int: 0–100 overall resume quality score)
        ats_score       → 0          (int: 0–100 ATS compatibility score)
        technical_skills → []        (list of detected technical skills)
        soft_skills      → []        (list of detected soft skills)
        missing_skills   → []        (list of skills the resume lacks)
        strengths        → []        (list of resume strengths)
        weaknesses       → []        (list of resume weaknesses)
        suggestions      → []        (list of improvement suggestions)
        summary          → ""        (short text summary of the analysis)
    """
    # dict.get(key, default) safely returns the default when the key is absent,
    # instead of raising a KeyError. This makes the function resilient to any
    # partial or malformed AI response.
    return {
        "resume_score":      result.get("resume_score", 0),
        "ats_score":         result.get("ats_score", 0),
        "technical_skills":  result.get("technical_skills", []),
        "soft_skills":       result.get("soft_skills", []),
        "missing_skills":    result.get("missing_skills", []),
        "strengths":         result.get("strengths", []),
        "weaknesses":        result.get("weaknesses", []),
        "suggestions":       result.get("suggestions", []),
        "summary":           result.get("summary", ""),
    }
