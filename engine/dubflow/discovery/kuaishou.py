"""Only the verified visionHotRank operation is exposed."""

from urllib.parse import quote

from ._base import DiscoveryAdapter, object_at, require_success, rows_at, text_at
from .models import DiscoveryPage, TrendItem, optional_int


BASE = "https://www.kuaishou.com"
HOT_QUERY = "query visionHotRank { visionHotRank { result items { name hotValue } } }"


class Kuaishou(DiscoveryAdapter):
    platform = "kuaishou"

    def __init__(self, client=None) -> None:
        super().__init__(client)
        self._bootstrapped = False

    def trends(self) -> DiscoveryPage:
        if not self._bootstrapped:
            self.client.request(self.platform, BASE + "/", accept="text/html")
            self._bootstrapped = True
        payload = self.client.json(self.platform, BASE + "/graphql", json_body={
            "operationName": "visionHotRank", "variables": {}, "query": HOT_QUERY,
        })
        require_success(object_at(payload, "data", "visionHotRank"), "result", (1,))
        return DiscoveryPage(tuple(TrendItem(
            self.platform, text_at(row, "name"), i, optional_int(row.get("hotValue")),
            BASE + "/search/video?searchKey=" + quote(text_at(row, "name"), safe=""),
        ) for i, row in enumerate(rows_at(payload, "data", "visionHotRank", "items"), 1)))
