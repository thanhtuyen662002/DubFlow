"""Verified unsigned hot words and autocomplete; lists use the host browser."""

from urllib.parse import quote

from ._base import DiscoveryAdapter, require_success, rows_at, text_at
from .models import DiscoveryPage, TrendItem, optional_int


BASE = "https://www.douyin.com"
COMMON = {"device_platform": "webapp", "aid": 6383, "channel": "channel_pc_web"}


def _trend(word: str, rank: int, heat: object = None) -> TrendItem:
    return TrendItem("douyin", word, rank, optional_int(heat),
                     BASE + "/search/" + quote(word, safe=""))


class Douyin(DiscoveryAdapter):
    platform = "douyin"

    def trends(self) -> DiscoveryPage:
        payload = self.client.json(self.platform, BASE + "/aweme/v1/web/hot/search/list/",
                                   params={**COMMON, "detail_list": 1, "source": 6,
                                           "main_billboard_count": 5})
        require_success(payload, "status_code")
        return DiscoveryPage(tuple(_trend(text_at(row, "word"), optional_int(row.get("position")) or i,
                                          row.get("hot_value"))
                                   for i, row in enumerate(rows_at(payload, "data", "word_list"), 1)))

    def suggest(self, query: str) -> DiscoveryPage:
        payload = self.client.json(self.platform, BASE + "/aweme/v1/web/search/sug/",
                                   params={**COMMON, "keyword": query, "source": "aweme_video_web"})
        require_success(payload, "status_code")
        return DiscoveryPage(tuple(_trend(text_at(row, "content"), i)
                                   for i, row in enumerate(rows_at(payload, "sug_list"), 1)))
