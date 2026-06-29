"""
utils/ai_analyzer.py — Google Gemini AI Resume Analysis

This module is the ONLY part of the application that communicates with
the Google Gemini API. It receives plain resume text as input, sends it
to Gemini with a carefully engineered prompt, and returns a structured
Python dictionary containing the full analysis.

Responsibilities:
    - Configure the Gemini client using the API key from config.py
    - Build the analysis prompt
    - Send the request to Gemini
    - Parse and validate the JSON response
    - Return a clean, predictable Python dictionary

This module has NO knowledge of Flask, routes, or PDF files.
"""

import re
import json
import google.generativeai as genai

from config import Config


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The Gemini model to use for all analysis requests.
# gemini-2.5-flash is fast, cost-effective, and supports large contexts —
# ideal for resume text which can be several thousand tokens long.
GEMINI_MODEL: str = "gemini-2.5-flash"

# All keys the analysis result must contain.
# Used by validate_analysis() to fill in any gaps from the API response.
REQUIRED_ANALYSIS_KEYS: list[str] = [
    "resume_score",
    "ats_score",
    "technical_skills",
    "soft_skills",
    "missing_skills",
    "strengths",
    "weaknesses",
    "suggestions",
    "summary",
]


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------

class GeminiConfigError(Exception):
    """Raised when the Gemini API key is missing or invalid."""
    pass


class GeminiAPIError(Exception):
    """Raised when the Gemini API request fails (network, rate limit, etc.)."""
    pass


class GeminiResponseParseError(Exception):
    """Raised when the Gemini response cannot be parsed as valid JSON."""
    pass


class EmptyResumeTextError(Exception):
    """Raised when the resume text passed for analysis is empty."""
    pass


# ---------------------------------------------------------------------------
# 1. configure_gemini
# ---------------------------------------------------------------------------

def configure_gemini() -> None:
    """
    Configure the google-generativeai client with the API key from config.py.

    This must be called once before any genai.GenerativeModel is used.
    genai.configure() sets the API key globally for the entire library,
    so all subsequent API calls in this process are automatically authenticated.

    Raises:
        GeminiConfigError: If GEMINI_API_KEY is not set in the environment.
    """
    api_key: str | None = Config.GEMINI_API_KEY

    # Fail loudly and early — a missing key means NO analysis can run.
    # Better to raise here than to get an obscure auth error from Google's servers.
    if not api_key:
        raise GeminiConfigError(
            "GEMINI_API_KEY is not set. "
            "Please add it to your .env file: GEMINI_API_KEY=your-key-here"
        )

    # This single call authenticates all future genai API requests.
    genai.configure(api_key=api_key)


# ---------------------------------------------------------------------------
# 2. create_analysis_prompt
# ---------------------------------------------------------------------------

def create_analysis_prompt(resume_text: str) -> str:
    """
    Build the structured prompt sent to Gemini for resume analysis.

    Prompt engineering is critical here. We explicitly:
        - Define Gemini's role ("expert HR consultant and ATS specialist")
        - Tell it the exact output format (JSON only, no markdown)
        - Specify every field's type and meaning
        - Set the scoring criteria clearly

    The prompt uses an f-string so the resume text is embedded directly.
    Gemini processes text up to ~1M tokens, so even a long resume is fine.

    Args:
        resume_text: The cleaned plain-text content of the uploaded resume.

    Returns:
        A complete, ready-to-send prompt string.
    """
    # The triple-quoted f-string keeps the prompt readable and easy to iterate on.
    prompt: str = f"""
You are an expert HR consultant, career coach, and ATS (Applicant Tracking System) specialist
with over 15 years of experience evaluating resumes across all industries.

Analyze the resume text provided below and return a comprehensive evaluation.

IMPORTANT INSTRUCTIONS:
- Return ONLY valid JSON. Do NOT include any markdown formatting, backticks, or explanatory text.
- Your entire response must be a single JSON object that can be parsed directly.
- Be specific, actionable, and professional in all text fields.
- Score honestly — do not inflate scores.

SCORING CRITERIA:
- resume_score (0–100): Overall resume quality. Consider clarity, structure, impact statements,
  quantified achievements, formatting signals, and professional tone.
- ats_score (0–100): ATS compatibility. Consider keyword density, standard section headings,
  avoidance of tables/graphics in text, proper date formats, and job title clarity.

JSON FORMAT TO RETURN:
{{
    "resume_score": <integer 0–100>,
    "ats_score": <integer 0–100>,
    "technical_skills": [<list of strings — programming languages, tools, frameworks, platforms detected>],
    "soft_skills": [<list of strings — communication, leadership, teamwork, etc.>],
    "missing_skills": [<list of strings — important skills typically expected but absent from the resume>],
    "strengths": [<list of strings — specific strong points of this resume>],
    "weaknesses": [<list of strings — specific weak points or gaps>],
    "suggestions": [<list of strings — concrete, actionable improvement recommendations>],
    "summary": "<2–3 sentence professional summary of the candidate based on the resume>"
}}

RESUME TEXT TO ANALYZE:
---
{resume_text}
---
"""
    return prompt.strip()


# ---------------------------------------------------------------------------
# 3. analyze_resume
# ---------------------------------------------------------------------------

def analyze_resume(resume_text: str) -> dict:
    """
    Run a full AI analysis on resume text and return a structured result.

    This is the main public function of this module. It orchestrates all
    the steps: configure → prompt → send → parse → validate → return.

    Args:
        resume_text: Cleaned plain text extracted from the uploaded PDF.

    Returns:
        A Python dictionary with all analysis fields populated.

    Raises:
        EmptyResumeTextError:      If resume_text is blank or whitespace-only.
        GeminiConfigError:         If the API key is missing.
        GeminiAPIError:            If the API request fails.
        GeminiResponseParseError:  If the response JSON is malformed.
    """
    # Guard: empty text means the AI will hallucinate — reject early.
    if not resume_text or not resume_text.strip():
        raise EmptyResumeTextError(
            "Resume text is empty. PDF extraction may have failed. "
            "Please ensure the uploaded file contains readable text."
        )

    # Step 1: Authenticate the Gemini client.
    configure_gemini()

    # Step 2: Build the structured prompt.
    prompt: str = create_analysis_prompt(resume_text)

    # Step 3: Initialize the model and send the request.
    raw_response_text: str = _send_gemini_request(prompt)

    # Step 4: Extract JSON from the response (strips markdown if present).
    parsed_result: dict = parse_ai_response(raw_response_text)

    # Step 5: Ensure all expected keys are present with valid defaults.
    validated_result: dict = validate_analysis(parsed_result)

    return validated_result


# ---------------------------------------------------------------------------
# 4. parse_ai_response
# ---------------------------------------------------------------------------

def parse_ai_response(response_text: str) -> dict:
    """
    Parse the raw Gemini response string into a Python dictionary.

    Even when we instruct Gemini to return pure JSON, it sometimes wraps
    the output in a markdown code block like:

        ```json
        { ... }
        ```

    This function strips that wrapper before parsing. It also handles edge
    cases like empty responses or responses containing only whitespace.

    Args:
        response_text: The raw string returned by the Gemini API.

    Returns:
        A Python dictionary parsed from the JSON content.

    Raises:
        GeminiResponseParseError: If the text is empty or not valid JSON
                                  after stripping markdown formatting.
    """
    if not response_text or not response_text.strip():
        raise GeminiResponseParseError(
            "Gemini returned an empty response. "
            "This may be a temporary API issue. Please try again."
        )

    cleaned_text: str = response_text.strip()

    # Remove markdown code fences: ```json ... ``` or ``` ... ```
    # re.DOTALL makes '.' match newlines so we capture multi-line JSON blocks.
    markdown_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(markdown_pattern, cleaned_text)

    if match:
        # Extract just the content between the fences.
        cleaned_text = match.group(1).strip()

    # Attempt to parse the cleaned string as JSON.
    try:
        result: dict = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        raise GeminiResponseParseError(
            f"Could not parse Gemini's response as JSON. "
            f"The model may have returned an unexpected format. "
            f"Parse error: {exc}. "
            f"Raw response (first 500 chars): {response_text[:500]}"
        ) from exc

    return result


# ---------------------------------------------------------------------------
# 5. validate_analysis
# ---------------------------------------------------------------------------

def validate_analysis(result: dict) -> dict:
    """
    Ensure the parsed analysis dictionary contains all required keys.

    The Gemini API is probabilistic — on rare occasions it may omit a field
    or return a slightly different structure. This function guarantees that
    the rest of the application always receives a complete, predictable dict,
    regardless of what the API returned.

    Defaults by type:
        - Score fields (int)  → 0
        - List fields (list)  → []
        - Text fields (str)   → ""

    Args:
        result: The dictionary returned by parse_ai_response().

    Returns:
        A new dictionary with all required keys present and valid values.
    """
    # Default values mirror the JSON schema we request in the prompt.
    defaults: dict = {
        "resume_score":     0,
        "ats_score":        0,
        "technical_skills": [],
        "soft_skills":      [],
        "missing_skills":   [],
        "strengths":        [],
        "weaknesses":       [],
        "suggestions":      [],
        "summary":          "",
    }

    validated: dict = {}

    for key, default_value in defaults.items():
        # Use the AI's value if present; fall back to the default if not.
        raw_value = result.get(key, default_value)

        # Additional type safety: if the AI returned the wrong type for a key,
        # fall back to the default rather than propagating a bad value.
        if not isinstance(raw_value, type(default_value)):
            validated[key] = default_value
        else:
            validated[key] = raw_value

    # Clamp score values to valid range [0, 100] in case the AI overshoots.
    validated["resume_score"] = max(0, min(100, validated["resume_score"]))
    validated["ats_score"]    = max(0, min(100, validated["ats_score"]))

    return validated


# ---------------------------------------------------------------------------
# Private Helper
# ---------------------------------------------------------------------------

def _send_gemini_request(prompt: str) -> str:
    """
    Send a prompt to Gemini and return the raw response text.

    This is a private function (prefixed with _) — it is an implementation
    detail of analyze_resume() and should not be called directly from outside
    this module. Isolating the API call here makes it easy to mock in tests.

    Args:
        prompt: The complete prompt string to send to Gemini.

    Returns:
        The raw text content of Gemini's response.

    Raises:
        GeminiAPIError: If the request fails for any reason (network timeout,
                        rate limit, invalid model name, empty response, etc.).
    """
    try:
        model = genai.GenerativeModel(model_name=GEMINI_MODEL)

        # generate_content() is a synchronous blocking call.
        # For a production app with high concurrency, consider the async variant.
        response = model.generate_content(prompt)

        # response.text raises ValueError if the response was blocked by
        # Gemini's safety filters — we catch that below.
        return response.text

    except ValueError as exc:
        # Gemini's safety system blocked the response.
        raise GeminiAPIError(
            "The resume content was flagged by Gemini's safety filters "
            "and could not be analyzed. Please check the uploaded file."
        ) from exc

    except Exception as exc:
        error_message: str = str(exc).lower()

        # Provide more specific guidance for common failure modes.
        if "quota" in error_message or "rate" in error_message:
            raise GeminiAPIError(
                "Gemini API rate limit reached. "
                "Please wait a moment and try again."
            ) from exc

        if "api key" in error_message or "auth" in error_message:
            raise GeminiAPIError(
                "Gemini API authentication failed. "
                "Please verify that your GEMINI_API_KEY in .env is correct."
            ) from exc

        if "network" in error_message or "connect" in error_message:
            raise GeminiAPIError(
                "Could not connect to the Gemini API. "
                "Please check your internet connection."
            ) from exc

        # Fallback for any other unexpected error.
        raise GeminiAPIError(
            f"Gemini API request failed: {exc}"
        ) from exc
