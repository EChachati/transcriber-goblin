import httpx

from .config import settings


def _parse_ysweet_url(connection_string: str) -> tuple[httpx.Client, str]:
    rest = connection_string.removeprefix("ys://")
    auth, _, host_part = rest.partition("@")
    if not host_part:
        host_part, auth = auth, ""
    return httpx.Client(base_url=f"http://{host_part}"), auth


class YSweetClient:
    def __init__(self) -> None:
        self._http, self._auth = _parse_ysweet_url(settings.ysweet_url)

    @staticmethod
    def _to_http(url: str) -> str:
        return url.replace("wss://", "https://").replace("ws://", "http://")

    def _doc_base_and_token(self, doc_id: str) -> tuple[str, str]:
        token = self.get_client_token(doc_id)
        base = self._to_http(token["baseUrl"].rstrip("/"))
        return base, token.get("token", "")

    def _request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._auth}"} if self._auth else {}
        response = self._http.request(method, path, json=json_body, headers=headers)
        response.raise_for_status()
        return response.json()

    def create_doc(self, doc_id: str | None = None) -> dict:
        return self._request("POST", "/doc/new", {"docId": doc_id} if doc_id else {})

    def get_client_token(self, doc_id: str) -> dict:
        return self._request("POST", f"/doc/{doc_id}/auth", {})

    def get_or_create_client_token(self, doc_id: str) -> dict:
        try:
            return self.get_client_token(doc_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self.create_doc(doc_id)
                return self.get_client_token(doc_id)
            raise

    def get_websocket_url(self, doc_id: str) -> str:
        token = self.get_or_create_client_token(doc_id)
        url = token["url"].rstrip("/")
        query = f"?token={token['token']}" if token.get("token") else ""
        return f"{url}{query}"

    def check_store(self) -> dict:
        return self._request("GET", "/check_store")

    def get_doc_as_update(self, doc_id: str) -> bytes | None:
        try:
            base, doc_token = self._doc_base_and_token(doc_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        headers = {"Authorization": f"Bearer {doc_token}"} if doc_token else {}
        response = self._http.get(f"{base}/as-update", headers=headers)
        response.raise_for_status()
        return response.content

    def update_doc(self, doc_id: str, update: bytes) -> None:
        self.create_doc(doc_id)
        base, doc_token = self._doc_base_and_token(doc_id)
        headers = {"Authorization": f"Bearer {doc_token}"} if doc_token else {}
        response = self._http.post(
            f"{base}/update", content=update, headers=headers
        )
        response.raise_for_status()


_manager: YSweetClient | None = None


def ysweet_manager() -> YSweetClient:
    global _manager
    if _manager is None:
        _manager = YSweetClient()
    return _manager
