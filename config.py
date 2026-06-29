"""
config.py — Application Configuration

This module centralizes all configuration for the Flask app.
Instead of scattering settings across multiple files, we load
everything here from environment variables (.env file) using
python-dotenv. This keeps secrets out of source code and makes
it easy to change settings per environment (dev, staging, prod).

Pattern: Config class — Flask's recommended approach for config.
"""
# Improvement: added APP_NAME, VERSION, AUTHOR, LOG_FOLDER so every module
# can import project identity from one canonical location instead of hard-coding
# strings in multiple files.

import os
from dotenv import load_dotenv

# Load variables from the .env file into the system environment.
# This must be called before accessing os.environ for any .env keys.
load_dotenv()


class Config:
    """
    Central configuration class for the AI Resume Analyzer.

    Flask reads settings directly from this class when we call
    app.config.from_object(Config). Every attribute defined here
    becomes a Flask config key.
    """

    # ----------------------------------------------------------------
    # Project Metadata
    # ----------------------------------------------------------------
    # Single source of truth for project identity — read by app.py,
    # health check, and API info endpoints.

    APP_NAME: str = "AI Resume Analyzer"
    VERSION:  str = "1.0"
    AUTHOR:   str = "Chetan"

    # ----------------------------------------------------------------
    # Security
    # ----------------------------------------------------------------

    # SECRET_KEY is used by Flask to cryptographically sign session cookies
    # and flash messages. Falls back to a hard-coded dev key if not set,
    # but a .env value MUST be provided in production.
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

    # ----------------------------------------------------------------
    # Google Gemini AI
    # ----------------------------------------------------------------

    # API key for authenticating requests to Google Gemini.
    # Will be None if not set — the AI analysis feature will check
    # for this before attempting any API calls.
    GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")

    # ----------------------------------------------------------------
    # File Storage
    # ----------------------------------------------------------------

    # Folder where uploaded resume PDFs will be saved temporarily.
    # Default is a relative path; override in .env for absolute paths.
    UPLOAD_FOLDER: str = os.environ.get("UPLOAD_FOLDER", "uploads")

    # Folder where generated PDF reports will be saved.
    REPORT_FOLDER: str = os.environ.get("REPORT_FOLDER", "reports")

    # Folder where rotating log files are stored.
    LOG_FOLDER: str = os.environ.get("LOG_FOLDER", "logs")

    # ----------------------------------------------------------------
    # Upload Limits
    # ----------------------------------------------------------------

    # Flask enforces this limit automatically on incoming request bodies.
    # 5 MB is generous enough for any resume PDF.
    MAX_CONTENT_LENGTH: int = 5 * 1024 * 1024  # 5 MB in bytes