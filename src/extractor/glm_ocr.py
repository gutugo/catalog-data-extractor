"""GLM-OCR client via Ollama HTTP API."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
import urllib.error


class GlmOcrError(Exception):
    """Error communicating with GLM-OCR via Ollama."""


class GlmOcrClient:
    """Client for GLM-OCR model running on Ollama.

    Uses the Ollama HTTP API to send page images for OCR.
    Reads OLLAMA_HOST env var (default http://localhost:11434).
    """

    def __init__(self, host: str | None = None, model: str = "glm-ocr:q8_0", timeout: int = 120):
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.model = model
        self.timeout = timeout

    def check_availability(self) -> bool:
        """Check if Ollama is reachable and glm-ocr model is loaded."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                models = [m.get("name", "") for m in data.get("models", [])]
                return any(self.model in name for name in models)
        except Exception:
            return False

    def recognize_table(self, image_bytes: bytes) -> str:
        """Send image to GLM-OCR with 'Table Recognition:' prompt.

        Args:
            image_bytes: PNG image bytes

        Returns:
            Markdown text with recognized tables

        Raises:
            GlmOcrError: On connection/parsing failures
        """
        return self._call("Table Recognition:", image_bytes)

    def recognize_text(self, image_bytes: bytes) -> str:
        """Send image to GLM-OCR with 'Text Recognition:' prompt.

        Args:
            image_bytes: PNG image bytes

        Returns:
            Recognized text content

        Raises:
            GlmOcrError: On connection/parsing failures
        """
        return self._call("Text Recognition:", image_bytes)

    def _call(self, prompt: str, image_bytes: bytes, retries: int = 2) -> str:
        """Send image + prompt to Ollama chat API with retry.

        Args:
            prompt: The recognition prompt
            image_bytes: PNG image bytes
            retries: Number of retries on 500 errors (model busy)

        Returns:
            Response text from the model

        Raises:
            GlmOcrError: On any failure after retries exhausted
        """
        import time

        b64_image = base64.b64encode(image_bytes).decode("ascii")

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64_image],
                }
            ],
            "stream": False,
        }).encode("utf-8")

        last_error = None
        for attempt in range(1 + retries):
            req = urllib.request.Request(
                f"{self.host}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                # Success
                try:
                    return data["message"]["content"]
                except (KeyError, TypeError) as e:
                    raise GlmOcrError(f"Unexpected response structure: {e}") from e
            except urllib.error.HTTPError as e:
                last_error = GlmOcrError(f"HTTP {e.code}: {e.reason}")
                if e.code >= 500 and attempt < retries:
                    time.sleep(2 * (attempt + 1))  # backoff: 2s, 4s
                    continue
                raise last_error from e
            except urllib.error.URLError as e:
                raise GlmOcrError(f"Connection failed: {e}") from e
            except TimeoutError as e:
                last_error = GlmOcrError(f"Request timed out after {self.timeout}s")
                if attempt < retries:
                    time.sleep(2)
                    continue
                raise last_error from e
            except json.JSONDecodeError as e:
                raise GlmOcrError(f"Malformed response: {e}") from e

        raise last_error or GlmOcrError("Unknown error")
