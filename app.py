"""
app.py — Flask Application Entry Point (Production-Improved)

This file orchestrates the entire backend. It creates and configures
the Flask app, sets up structured logging, and defines all API routes.

All business logic stays inside utils/ — app.py only coordinates.

Routes (v1, with backward-compatible aliases):
    GET  /                              → Health check (root)
    GET  /api                           → API information
    GET  /api/health                    → Detailed health status
    POST /api/v1/analyze                → Full resume analysis pipeline
    GET  /api/v1/report/<filename>      → Download a generated PDF report
    POST /api/analyze                   → Backward-compatible alias
    GET  /api/report/<filename>         → Backward-compatible alias
"""

import os
import time
import logging

from logging.handlers import RotatingFileHandler
from flask import Flask, request, send_from_directory, render_template
from werkzeug.utils import secure_filename

from config import Config

# Utility modules — each handles one layer of the pipeline.
from utils.helpers import (
    validate_uploaded_file,
    generate_unique_filename,
    success_response,
    error_response,
    get_file_size,
    format_analysis_result,
)
from utils.pdf_extractor import (
    extract_pdf_metadata,
    PDFExtractionError,
    PDFEmptyError,
    PDFPasswordProtectedError,
)
from utils.ai_analyzer import (
    analyze_resume,
    GeminiConfigError,
    GeminiAPIError,
    GeminiResponseParseError,
    EmptyResumeTextError,
)
from utils.report_generator import (
    create_pdf_report,
    ReportGenerationError,
)


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

def configure_logging(log_folder: str) -> logging.Logger:
    """
    Configure and return the application logger.

    Uses a RotatingFileHandler so log files never grow unbounded:
        - Max 5 MB per file
        - Keeps the last 3 rotated files (app.log, app.log.1, app.log.2)

    Logs are written to both:
        - logs/app.log   (file, persistent)
        - stdout/console (stream, visible in terminal)

    Args:
        log_folder: Path to the directory where app.log will be written.

    Returns:
        A configured Logger instance named 'ai_resume_analyzer'.
    """
    # Create the logs/ directory silently if it doesn't exist.
    os.makedirs(log_folder, exist_ok=True)

    log_file_path: str = os.path.join(log_folder, "app.log")

    # Format: timestamp | log level | message
    log_format = "%(asctime)s | %(levelname)-8s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # File handler — rotates at 5 MB, retains 3 backups.
    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Console handler — visible while running python app.py in dev.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    logger = logging.getLogger("ai_resume_analyzer")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if create_app() is called more than once (e.g., in tests).
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    """
    Create, configure, and return the production-ready Flask application.

    Improvements over the original:
        - Structured logging with rotation
        - /api/health detailed health check
        - /api endpoint with full route directory
        - Versioned routes under /api/v1/
        - Backward-compatible unversioned aliases
        - Request processing time measurement
        - File metadata (original name, saved name, size, page count)
        - Enriched JSON responses with timestamp + status
        - 413 handler for oversized uploads
    """
    app = Flask(__name__)

    # Pull all settings from the Config class.
    app.config.from_object(Config)

    # Create all required directories at startup.
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["REPORT_FOLDER"], exist_ok=True)

    # Initialize the logger — available to all route functions via closure.
    logger: logging.Logger = configure_logging(app.config["LOG_FOLDER"])
    logger.info("=" * 60)
    logger.info(
        f"Starting {Config.APP_NAME} v{Config.VERSION} "
        f"by {Config.AUTHOR}"
    )
    logger.info(f"Upload folder : {app.config['UPLOAD_FOLDER']}")
    logger.info(f"Report folder : {app.config['REPORT_FOLDER']}")
    logger.info(f"Log folder    : {app.config['LOG_FOLDER']}")
    logger.info("=" * 60)

    # -----------------------------------------------------------------------
    # Route 1 — Root Health Check  GET /
    # -----------------------------------------------------------------------

    @app.route("/", methods=["GET"])
    def index():
        """
        Root endpoint — renders the homepage UI template.
        """
        return render_template("index.html")

    # -----------------------------------------------------------------------
    # Route 1b — Web UI Resume Upload Page  GET /upload
    # -----------------------------------------------------------------------

    @app.route("/upload", methods=["GET"])
    def upload_page():
        """
        Renders the resume upload page UI template.
        """
        return render_template("upload.html")

    # -----------------------------------------------------------------------
    # Route 2 — API Information  GET /api
    # -----------------------------------------------------------------------

    @app.route("/api", methods=["GET"])
    def api_info():
        """
        Return a directory of all available API endpoints.

        Useful for developers integrating with the API for the first time.
        Acts as lightweight, always-up-to-date documentation.

        Returns:
            200 JSON with project metadata and endpoint list.
        """
        return success_response(
            message="API information retrieved.",
            data={
                "project":   Config.APP_NAME,
                "developer": Config.AUTHOR,
                "version":   Config.VERSION,
                "available_endpoints": [
                    {"method": "GET",  "path": "/",                         "description": "Root health check"},
                    {"method": "GET",  "path": "/api",                      "description": "API information"},
                    {"method": "GET",  "path": "/api/health",               "description": "Detailed health status"},
                    {"method": "POST", "path": "/api/v1/analyze",           "description": "Analyze resume (versioned)"},
                    {"method": "GET",  "path": "/api/v1/report/<filename>", "description": "Download report (versioned)"},
                    {"method": "POST", "path": "/api/analyze",              "description": "Analyze resume (alias)"},
                    {"method": "GET",  "path": "/api/report/<filename>",    "description": "Download report (alias)"},
                ],
            },
        )

    # -----------------------------------------------------------------------
    # Route 3 — Detailed Health Check  GET /api/health
    # -----------------------------------------------------------------------

    @app.route("/api/health", methods=["GET"])
    def health_check():
        """
        Return a detailed snapshot of the application's operational status.

        Checks:
            - Gemini API key presence (does NOT make a live API call)
            - Upload folder existence
            - Report folder existence

        Returns:
            200 JSON with per-component status flags.
        """
        gemini_status: str = "configured" if Config.GEMINI_API_KEY else "missing — set GEMINI_API_KEY in .env"
        upload_status: str = "exists" if os.path.isdir(app.config["UPLOAD_FOLDER"]) else "missing"
        report_status: str = "exists" if os.path.isdir(app.config["REPORT_FOLDER"]) else "missing"

        logger.info("Health check requested.")

        return success_response(
            message="Health check passed.",
            data={
                "status":        "healthy",
                "project":       Config.APP_NAME,
                "version":       Config.VERSION,
                "uptime":        "running",
                "gemini":        gemini_status,
                "upload_folder": upload_status,
                "report_folder": report_status,
            },
        )

    # -----------------------------------------------------------------------
    # Shared analysis logic (used by both versioned + alias routes)
    # -----------------------------------------------------------------------

    def _run_analysis_pipeline():
        """
        Execute the full resume analysis pipeline and return a Flask response.

        This private helper is called by both /api/v1/analyze and /api/analyze
        so that the pipeline code exists exactly once (DRY principle).

        Pipeline:
            1. Validate uploaded file
            2. Measure file size before saving
            3. Save to uploads/ with a unique filename
            4. Extract text from PDF
            5. Analyze with Gemini AI
            6. Format and normalize analysis result
            7. Generate downloadable PDF report
            8. Return enriched JSON response with timing and file metadata

        Returns:
            A Flask Response object (success or error).
        """
        # Start the request timer — we measure total wall-clock time.
        start_time: float = time.time()

        # ------------------------------------------------------------------
        # Step 1 — Validate the uploaded file
        # ------------------------------------------------------------------
        uploaded_file = request.files.get("resume")

        is_valid, validation_error = validate_uploaded_file(uploaded_file)
        if not is_valid:
            logger.warning(f"File validation failed: {validation_error}")
            return error_response(validation_error, status_code=400)

        # Capture original filename before we rename it.
        original_name: str = uploaded_file.filename

        # ------------------------------------------------------------------
        # Step 2 — Measure file size (before saving)
        # ------------------------------------------------------------------
        file_size_mb: float = get_file_size(uploaded_file)
        logger.info(f"Resume upload received: '{original_name}' ({file_size_mb} MB)")

        # ------------------------------------------------------------------
        # Step 3 — Generate safe unique filename and save to uploads/
        # ------------------------------------------------------------------
        # secure_filename() strips dangerous characters and path separators.
        # Edge case: if the original name is all non-ASCII (e.g., Chinese characters),
        # secure_filename() returns an empty string. We guard with a uuid fallback
        # so we never attempt to save a file with a blank name.
        safe_name: str = secure_filename(original_name)
        if not safe_name:
            # Fallback: use a generic name so the upload never silently fails.
            import uuid as _uuid
            safe_name = f"resume_{_uuid.uuid4().hex[:8]}.pdf"

        unique_name: str = generate_unique_filename(safe_name)
        upload_path: str = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)

        uploaded_file.save(upload_path)
        logger.info(f"File saved as: '{unique_name}'")

        # ------------------------------------------------------------------
        # Step 4 — Extract text + metadata from the saved PDF (single file open)
        # ------------------------------------------------------------------
        logger.info("Starting PDF text extraction...")

        try:
            pdf_meta: dict   = extract_pdf_metadata(upload_path)
            resume_text: str = pdf_meta["text"]
            page_count:  int = pdf_meta["pages"]
            logger.info(f"PDF extracted: {page_count} page(s), {pdf_meta['characters']} chars.")

        except PDFPasswordProtectedError as exc:
            logger.warning(f"PDF password protected: {exc}")
            return error_response(str(exc), status_code=400)

        except PDFEmptyError as exc:
            logger.warning(f"PDF empty (no extractable text): {exc}")
            return error_response(str(exc), status_code=400)

        except PDFExtractionError as exc:
            logger.error(f"PDF extraction failed: {exc}")
            return error_response(str(exc), status_code=400)

        except FileNotFoundError as exc:
            logger.error(f"Saved file not found after upload: {exc}")
            return error_response(f"Uploaded file could not be located: {exc}", status_code=500)

        # ------------------------------------------------------------------
        # Step 5 — Analyze resume text with Gemini AI
        # ------------------------------------------------------------------
        logger.info("Sending resume to Gemini AI for analysis...")

        try:
            raw_analysis: dict = analyze_resume(resume_text)
            logger.info("Gemini AI analysis completed successfully.")

        except EmptyResumeTextError as exc:
            logger.warning(f"Empty resume text passed to analyzer: {exc}")
            return error_response(str(exc), status_code=400)

        except GeminiConfigError as exc:
            logger.error(f"Gemini configuration error: {exc}")
            return error_response(str(exc), status_code=500)

        except GeminiAPIError as exc:
            logger.error(f"Gemini API error: {exc}")
            return error_response(str(exc), status_code=500)

        except GeminiResponseParseError as exc:
            logger.error(f"Gemini response parse error: {exc}")
            return error_response(str(exc), status_code=500)

        # ------------------------------------------------------------------
        # Step 6 — Normalize analysis result (fill any missing keys)
        # ------------------------------------------------------------------
        analysis: dict = format_analysis_result(raw_analysis)

        # ------------------------------------------------------------------
        # Step 7 — Generate PDF report
        # ------------------------------------------------------------------
        logger.info("Generating PDF report...")

        try:
            report_filename: str = create_pdf_report(
                result=analysis,
                output_folder=app.config["REPORT_FOLDER"],
                original_filename=original_name,   # drives the output filename
            )
            logger.info(f"Report generated: '{report_filename}'")

        except ReportGenerationError as exc:
            logger.error(f"Report generation failed: {exc}")
            return error_response(str(exc), status_code=500)

        # ------------------------------------------------------------------
        # Step 8 — Calculate processing time and return response
        # ------------------------------------------------------------------
        elapsed_seconds: float = round(time.time() - start_time, 2)
        processing_time: str   = f"{elapsed_seconds} seconds"

        logger.info(
            f"Request completed in {processing_time} | "
            f"Resume score: {analysis.get('resume_score')} | "
            f"ATS score: {analysis.get('ats_score')}"
        )

        return success_response(
            message="Resume analyzed successfully.",
            data={
                "file": {
                    "original_name": original_name,
                    "saved_name":    unique_name,
                    "size_mb":       file_size_mb,
                    "pages":         page_count,
                },
                "analysis": analysis,
                "report": {
                    "filename":     report_filename,
                    "download_url": f"/api/v1/report/{report_filename}",
                },
                "processing_time": processing_time,
            },
            status_code=200,
        )

    # -----------------------------------------------------------------------
    # Route 4 — Versioned Analyze  POST /api/v1/analyze
    # -----------------------------------------------------------------------

    @app.route("/api/v1/analyze", methods=["POST"])
    def analyze_v1():
        """
        Versioned resume analysis endpoint.

        Delegates entirely to _run_analysis_pipeline().
        Versioning allows future /api/v2/analyze with breaking changes
        while keeping existing integrations working on v1.
        """
        return _run_analysis_pipeline()

    # -----------------------------------------------------------------------
    # Route 5 — Backward-Compatible Alias  POST /api/analyze
    # -----------------------------------------------------------------------

    @app.route("/api/analyze", methods=["POST"])
    def analyze():
        """
        Backward-compatible alias for /api/v1/analyze.

        Any client built before versioning was introduced continues to work
        without modification. Both routes call identical logic.
        """
        return _run_analysis_pipeline()

    # -----------------------------------------------------------------------
    # Route 5b — Web UI Analysis Handler  POST /analyze
    # -----------------------------------------------------------------------

    @app.route("/analyze", methods=["POST"])
    def analyze_ui():
        """
        Web UI endpoint for analyzing resume.
        Saves the file, runs the AI analysis pipeline, and renders
        the dynamic result page or redirects with an error.
        """
        res, status_code = _run_analysis_pipeline()

        # If not successful or has error status, render upload.html with error
        if status_code != 200:
            err_data = res.get_json()
            error_message = err_data.get("message", "An error occurred during analysis.")
            return render_template("upload.html", error=error_message)

        # If successful, extract the analysis payload and render result.html
        res_data = res.get_json()
        payload = res_data.get("data", {})
        analysis_data = payload.get("analysis", {})
        report_data = payload.get("report", {})
        download_url = report_data.get("download_url", "")

        return render_template(
            "result.html",
            analysis=analysis_data,
            download_url=download_url
        )

    # -----------------------------------------------------------------------
    # Shared report download logic
    # -----------------------------------------------------------------------

    def _serve_report(filename: str):
        """
        Serve a generated PDF report as a file download.

        Args:
            filename: Report filename returned by /api/v1/analyze.

        Returns:
            PDF file download (200) or JSON 404 if not found.
        """
        report_folder: str = app.config["REPORT_FOLDER"]
        report_path:   str = os.path.join(report_folder, filename)

        if not os.path.isfile(report_path):
            logger.warning(f"Report not found: '{filename}'")
            return error_response(
                f"Report '{filename}' was not found. It may have expired or the filename is incorrect.",
                status_code=404,
            )

        logger.info(f"Report download served: '{filename}'")

        # as_attachment=True sets Content-Disposition: attachment, triggering
        # a browser download instead of inline rendering.
        return send_from_directory(report_folder, filename, as_attachment=True)

    # -----------------------------------------------------------------------
    # Route 6 — Versioned Download  GET /api/v1/report/<filename>
    # -----------------------------------------------------------------------

    @app.route("/api/v1/report/<filename>", methods=["GET"])
    def download_report_v1(filename: str):
        """Versioned report download endpoint."""
        return _serve_report(filename)

    # -----------------------------------------------------------------------
    # Route 7 — Backward-Compatible Download  GET /api/report/<filename>
    # -----------------------------------------------------------------------

    @app.route("/api/report/<filename>", methods=["GET"])
    def download_report(filename: str):
        """Backward-compatible alias for /api/v1/report/<filename>."""
        return _serve_report(filename)

    # -----------------------------------------------------------------------
    # Global Error Handlers
    # -----------------------------------------------------------------------
    # These catch any errors that bypass route-level try/except blocks.
    # Always return structured JSON — never Flask's default HTML error pages.

    @app.errorhandler(404)
    def not_found(error):
        """Requested route or resource does not exist."""
        logger.warning(f"404 Not Found: {request.url}")
        return error_response("The requested resource was not found.", status_code=404)

    @app.errorhandler(405)
    def method_not_allowed(error):
        """Client used the wrong HTTP method for this endpoint."""
        logger.warning(f"405 Method Not Allowed: {request.method} {request.url}")
        return error_response("This HTTP method is not allowed for this route.", status_code=405)

    @app.errorhandler(413)
    def file_too_large(error):
        """
        Flask raises 413 automatically when the upload body exceeds MAX_CONTENT_LENGTH.
        Returns a descriptive JSON message instead of Flask's default HTML error page.
        """
        max_mb: int = Config.MAX_CONTENT_LENGTH // (1024 * 1024)
        logger.warning(f"413 Payload Too Large — limit is {max_mb} MB.")
        return error_response(
            f"File too large. Maximum allowed upload size is {max_mb} MB.",
            status_code=413,
        )

    @app.errorhandler(500)
    def internal_server_error(error):
        """Unhandled server-side exception."""
        logger.error(f"500 Internal Server Error: {error}")
        return error_response("An unexpected server error occurred.", status_code=500)

    return app


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

# Module-level app instance — used by the Flask dev server and any WSGI
# server (Gunicorn, uWSGI) that expects an 'app' object in this file.
app = create_app()

if __name__ == "__main__":
    # debug=True enables hot-reload and the interactive debugger locally.
    # NEVER set debug=True in a production deployment.
    app.run(debug=True)
