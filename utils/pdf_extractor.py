"""
utils/pdf_extractor.py — PDF Text Extraction

This module is the ONLY part of the application that interacts with
PDF files. It uses pdfplumber to open, read, and extract raw text
from uploaded resume PDFs.

Responsibilities:
    - Extract full text from all pages of a PDF
    - Clean and normalize that raw text
    - Return page count and basic metadata

This module has NO knowledge of Flask, routes, or the Gemini API.
It is a pure utility — plug it in anywhere that needs PDF text.
"""

import re
import os
import pdfplumber


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------
# Using specific exception types (instead of generic Exception) lets callers
# catch exactly the error they care about, making error handling more precise.

class PDFExtractionError(Exception):
    """
    Raised when text extraction from a PDF fails for any reason.

    Wraps lower-level errors (IOError, pdfplumber exceptions, etc.)
    with a human-readable message specific to this application.
    """
    pass


class PDFPasswordProtectedError(PDFExtractionError):
    """
    Raised specifically when pdfplumber detects a password-protected PDF.

    Subclasses PDFExtractionError so callers can catch either the
    specific case or the general extraction failure with one handler.
    """
    pass


class PDFEmptyError(PDFExtractionError):
    """
    Raised when a PDF opens successfully but contains no extractable text.

    This can happen with scanned image-only PDFs or completely blank files.
    """
    pass


# ---------------------------------------------------------------------------
# 1. extract_text_from_pdf
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract all readable text from a PDF file and return it as one string.

    Opens the PDF with pdfplumber, iterates every page, collects any text
    found (skipping image-only or blank pages), joins the results, and
    passes the combined text through clean_resume_text() before returning.

    Args:
        pdf_path: Absolute or relative path to the PDF file on disk.

    Returns:
        A single cleaned string containing all extractable text from the PDF.

    Raises:
        FileNotFoundError:        If the path does not point to an existing file.
        PDFPasswordProtectedError: If the PDF is encrypted/password-protected.
        PDFEmptyError:            If the PDF contains no extractable text at all.
        PDFExtractionError:       For any other pdfplumber or I/O failure.
    """
    # Guard: check the file exists before attempting to open it.
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at path: '{pdf_path}'")

    page_texts: list[str] = []  # Collect text from each page separately.

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # pdfplumber raises pdfminer's PDFPasswordIncorrect when the
            # document is encrypted and no password is supplied.
            for page_number, page in enumerate(pdf.pages, start=1):
                page_text: str | None = page.extract_text()

                # extract_text() returns None for image-only pages — skip those.
                if page_text and page_text.strip():
                    page_texts.append(page_text)

    except pdfplumber.pdfminer.pdfparser.PDFSyntaxError as exc:
        raise PDFExtractionError(
            f"The PDF file appears to be corrupted and cannot be read. "
            f"Original error: {exc}"
        ) from exc

    except Exception as exc:
        # Catch pdfminer's PDFPasswordIncorrect (imported path varies by version)
        # by checking the class name — avoids a brittle deep import.
        if "password" in type(exc).__name__.lower() or "incorrect" in str(exc).lower():
            raise PDFPasswordProtectedError(
                "This PDF is password-protected. Please upload an unencrypted resume."
            ) from exc

        # Any other unexpected error becomes a generic extraction failure.
        raise PDFExtractionError(
            f"An unexpected error occurred while reading the PDF: {exc}"
        ) from exc

    # If we looped through all pages and found nothing, the PDF has no text.
    if not page_texts:
        raise PDFEmptyError(
            "No readable text was found in this PDF. "
            "It may be a scanned image. Please upload a text-based PDF."
        )

    # Join pages with double newlines to preserve page-level separation,
    # then clean the combined text before returning.
    combined_text: str = "\n\n".join(page_texts)
    return clean_resume_text(combined_text)


# ---------------------------------------------------------------------------
# 2. clean_resume_text
# ---------------------------------------------------------------------------

def clean_resume_text(text: str) -> str:
    """
    Normalize and clean raw text extracted from a PDF.

    PDF text often contains:
        - Multiple consecutive spaces from column layouts
        - Three or more blank lines between sections
        - Leading/trailing whitespace per line
        - Windows-style line endings (\\r\\n)

    This function collapses all of those into clean, readable plain text
    while preserving the paragraph/section structure the AI will need.

    Args:
        text: Raw string as returned by pdfplumber's extract_text().

    Returns:
        Cleaned string with normalized spacing and line breaks.
    """
    if not text:
        return ""

    # Step 1: Normalize Windows (\r\n) and old Mac (\r) line endings to \n.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Step 2: Collapse multiple consecutive spaces/tabs into a single space.
    # The regex \t matches tabs; [ \t] matches either space or tab.
    text = re.sub(r"[ \t]+", " ", text)

    # Step 3: Strip trailing whitespace from every individual line.
    lines: list[str] = [line.rstrip() for line in text.split("\n")]

    # Step 4: Collapse three or more consecutive blank lines into two.
    # Two blank lines (one empty line between paragraphs) is readable;
    # more than that wastes space and confuses the AI parser.
    cleaned_lines: list[str] = []
    consecutive_blank_count: int = 0

    for line in lines:
        if line == "":
            consecutive_blank_count += 1
            # Allow at most 1 blank line between paragraphs.
            if consecutive_blank_count <= 1:
                cleaned_lines.append(line)
        else:
            consecutive_blank_count = 0
            cleaned_lines.append(line)

    # Step 5: Strip overall leading/trailing whitespace from the full string.
    return "\n".join(cleaned_lines).strip()


# ---------------------------------------------------------------------------
# 3. get_pdf_page_count
# ---------------------------------------------------------------------------

def get_pdf_page_count(pdf_path: str) -> int:
    """
    Return the total number of pages in a PDF file.

    A quick read that opens the PDF, checks the page list, and closes it.
    Does not extract any text — useful for metadata and logging.

    Args:
        pdf_path: Absolute or relative path to the PDF file on disk.

    Returns:
        Integer count of pages in the PDF.

    Raises:
        FileNotFoundError:  If the file does not exist.
        PDFExtractionError: If the file cannot be opened as a valid PDF.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at path: '{pdf_path}'")

    try:
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception as exc:
        raise PDFExtractionError(
            f"Could not determine page count for '{pdf_path}': {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# 4. extract_pdf_metadata
# ---------------------------------------------------------------------------

def extract_pdf_metadata(pdf_path: str) -> dict:
    """
    Extract basic metadata from a PDF in a single file-open pass.

    Combines text extraction and page counting into one operation so the
    file is opened only once. This avoids the double-read that occurred
    when calling extract_text_from_pdf() and get_pdf_page_count() separately.

    Args:
        pdf_path: Absolute or relative path to the PDF file on disk.

    Returns:
        A dictionary with the following keys:
            pages      (int):  Total number of pages.
            characters (int):  Total character count of extracted text.
            words      (int):  Total word count of extracted text.
            text       (str):  The full extracted and cleaned resume text.

    Raises:
        FileNotFoundError:  If the file does not exist.
        PDFEmptyError:      If no text could be extracted.
        PDFExtractionError: For any other read failure.

    Example return value:
        {
            "pages": 2,
            "characters": 5210,
            "words": 740,
            "text": "John Doe ...."
        }
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found at path: '{pdf_path}'")

    page_texts: list[str] = []
    page_count: int = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Count pages and collect text in a single open.
            page_count = len(pdf.pages)

            for page in pdf.pages:
                page_text: str | None = page.extract_text()
                if page_text and page_text.strip():
                    page_texts.append(page_text)

    except pdfplumber.pdfminer.pdfparser.PDFSyntaxError as exc:
        raise PDFExtractionError(
            f"The PDF file appears to be corrupted and cannot be read. "
            f"Original error: {exc}"
        ) from exc

    except Exception as exc:
        if "password" in type(exc).__name__.lower() or "incorrect" in str(exc).lower():
            raise PDFPasswordProtectedError(
                "This PDF is password-protected. Please upload an unencrypted resume."
            ) from exc
        raise PDFExtractionError(
            f"An unexpected error occurred while reading the PDF: {exc}"
        ) from exc

    if not page_texts:
        raise PDFEmptyError(
            "No readable text was found in this PDF. "
            "It may be a scanned image. Please upload a text-based PDF."
        )

    combined_text: str = "\n\n".join(page_texts)
    extracted_text: str = clean_resume_text(combined_text)

    return {
        "pages":      page_count,
        "characters": len(extracted_text),
        "words":      len(extracted_text.split()),
        "text":       extracted_text,
    }
