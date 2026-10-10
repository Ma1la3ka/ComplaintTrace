"""Thin client for the N-ATLAS server running in the Kaggle notebook."""
import time
import requests

NETWORK_MSG = ("I cannot reach the AI service right now. This is usually a network problem "
               "or the AI server is offline. Please check your connection and try again in a moment.")
SLOW_MSG = "The AI service is taking too long to answer. Please try again."
KEY_MSG = "The AI service rejected the request. Check that NATLAS_KEY matches the notebook's API_KEY."


class NatlasError(Exception):
    """The message is safe to show to the user. Technical detail goes to the server log."""


class NatlasClient:
    def __init__(self, base_url: str, api_key: str, timeout_chat: int = 120, timeout_asr: int = 60):
        self.base = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key}
        self.timeout_chat = timeout_chat
        self.timeout_asr = timeout_asr

    def _post(self, path: str, timeout: int, **kw):
        last, kind = None, "network"
        t0 = time.time()
        for attempt in range(3):
            try:
                r = requests.post(f"{self.base}{path}", headers=self.headers, timeout=timeout, **kw)
                if r.status_code >= 500:
                    raise requests.HTTPError(f"server error {r.status_code}", response=r)
                r.raise_for_status()
                print(f"[timing] {path} {time.time() - t0:.1f}s", flush=True)
                return r
            except requests.ConnectionError as e:        # DNS failure, connection reset, tunnel gone
                last, kind = e, "network"
            except requests.Timeout as e:
                last, kind = e, "slow"
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status is not None and status < 500:  # 401, 400 and so on: retrying will not help
                    print(f"[natlas] {path} refused with {status}", flush=True)
                    raise NatlasError(KEY_MSG if status == 401
                                      else f"The AI service refused the request ({status}).") from e
                last, kind = e, "network"                # 5xx, including 530 when the tunnel is down
            time.sleep(2 * (attempt + 1))
        print(f"[natlas] {path} failed after 3 tries: {last}", flush=True)
        raise NatlasError(SLOW_MSG if kind == "slow" else NETWORK_MSG) from last

    def health(self) -> dict:
        try:
            r = requests.get(f"{self.base}/health", timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            print(f"[natlas] health check failed: {e}", flush=True)
            raise NatlasError(NETWORK_MSG) from e

    def transcribe(self, audio: bytes, language: str, filename: str = "clip.webm") -> str:
        r = self._post("/transcribe", self.timeout_asr,
                       data={"language": language}, files={"audio": (filename, audio)})
        return r.json().get("text", "").strip()

    def generate(self, system: str, messages: list, max_new_tokens: int = 300,
                 temperature: float = 0.2) -> str:
        r = self._post("/generate", self.timeout_chat,
                       json={"system": system, "messages": messages,
                             "max_new_tokens": max_new_tokens, "temperature": temperature})
        return r.json().get("reply", "").strip()