"""2026-10-20 (使用者：自動每日抓取財經新聞): the day's financial headlines from public RSS feeds."""
import json
from datetime import datetime

import pytest

from quant_platform.research import market_news

FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title>Tai Shi Rises</title><link>https://example.test/a</link><pubDate>Mon, 19 Oct 2026 06:30:00 GMT</pubDate>
<description>&lt;p&gt;The index rose &lt;b&gt;1%&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>Rates Hold</title><link>https://example.test/b</link><pubDate>Tue, 20 Oct 2026 01:00:00 GMT</pubDate></item>
<item><title></title><link>https://example.test/c</link></item>
</channel></rss>"""


def test_a_feed_parses_into_dated_headlines_with_plain_summaries():
    items = market_news.parse("cna_finance", FEED)
    assert [item["title"] for item in items] == ["Tai Shi Rises", "Rates Hold"]           # no title: left out
    assert items[0]["published"] == "2026-10-19T14:30+08:00" and items[0]["summary"] == "The index rose 1%"
    assert items[0]["source"] == "中央社・財經"
    with pytest.raises(ValueError):
        market_news.parse("x", b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><rss></rss>')


def test_reads_merge_by_link_into_the_day_each_headline_was_published(tmp_path):
    def get(url):
        if "feedburner" in url:
            return FEED
        raise OSError("down")
    now = datetime(2026, 10, 20, 12, 0, tzinfo=market_news.TAIPEI)
    found = market_news.collect(tmp_path, now, get=get)
    assert found["feeds"] == 1 and found["added"] == 2 and found["failed"] == len(market_news.FEEDS) - 1
    again = market_news.collect(tmp_path, now, get=get)
    assert again["added"] == 0                                                 # the same links: kept once
    day = json.loads(market_news.path_for(tmp_path, "2026-10-19").read_text(encoding="utf-8"))
    assert [item["title"] for item in day] == ["Tai Shi Rises"]
    assert [item["title"] for item in market_news.latest(tmp_path)] == ["Rates Hold", "Tai Shi Rises"]
