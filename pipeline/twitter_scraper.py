#!/usr/bin/env python3
"""
twitter_scraper.py — 基于 cookies 直接调用 X GraphQL API 抓取推文

用法：
  python3 pipeline/twitter_scraper.py --accounts karpathy,_akhaliq --limit 5
  python3 pipeline/twitter_scraper.py --search "AI LLM" --limit 10

返回 JSON：{"items": [...], "errors": [...]}
"""

import json, re, sys, argparse, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path

COOKIES_PATH = Path(__file__).parent.parent / "x_cookies.json"
BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# GraphQL endpoint IDs（定期可能变，但已稳定较长时间）
GQL = {
    "UserByScreenName": "Yka-W8dz7RaEuQNkroPkYw",
    "UserTweets":       "V7H0Ap3_Hh2FyS75OCDO3Q",
    "SearchTimeline":   "gkjsKepM6gl_HmFWoWKfgg",
}

# ── 基础 HTTP ─────────────────────────────────────────────────────────────────

def _load_cookies():
    if not COOKIES_PATH.exists():
        raise FileNotFoundError(f"X cookies 不存在: {COOKIES_PATH}\n请先导出 cookies 到该文件")
    with open(COOKIES_PATH) as f:
        cookies = json.load(f)
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    ct0 = next((c['value'] for c in cookies if c['name'] == 'ct0'), "")
    return cookie_str, ct0


def _gql_get(endpoint_name, variables, features=None):
    cookie_str, ct0 = _load_cookies()
    eid = GQL[endpoint_name]
    params = {"variables": json.dumps(variables, separators=(',', ':'))}
    if features:
        params["features"] = json.dumps(features, separators=(',', ':'))
    url = f"https://api.x.com/graphql/{eid}/{endpoint_name}?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": UA,
        "Authorization": BEARER,
        "Cookie": cookie_str,
        "X-Csrf-Token": ct0,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# ── 解析工具 ─────────────────────────────────────────────────────────────────

def _parse_tweet(raw):
    """从 GraphQL result 节点提取标准化推文字典。"""
    try:
        result = raw.get("result") or raw
        # 处理 Tweet/TweetWithVisibilityResults 两种类型
        if result.get("__typename") == "TweetWithVisibilityResults":
            result = result["tweet"]
        legacy = result.get("legacy", {})
        user_legacy = result.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})

        text = legacy.get("full_text", "")
        # 去掉末尾的 t.co 短链（通常是媒体附件链接）
        text = re.sub(r'\s*https://t\.co/\S+$', '', text).strip()

        urls = [u.get("expanded_url", "") for u in legacy.get("entities", {}).get("urls", [])]
        media_url = None
        for m in legacy.get("extended_entities", {}).get("media", []):
            if m.get("type") == "photo":
                media_url = m.get("media_url_https")
                break

        return {
            "id": legacy.get("id_str"),
            "text": text,
            "author": user_legacy.get("screen_name", ""),
            "author_name": user_legacy.get("name", ""),
            "created_at": legacy.get("created_at", ""),
            "likes": legacy.get("favorite_count", 0),
            "retweets": legacy.get("retweet_count", 0),
            "url": f"https://x.com/{user_legacy.get('screen_name', '')}/status/{legacy.get('id_str', '')}",
            "source_urls": [u for u in urls if u],
            "media_url": media_url,
        }
    except Exception:
        return None


def _iter_timeline_entries(data):
    """从 UserTweets / SearchTimeline 响应中递归提取 tweet 节点。"""
    def _walk(obj):
        if isinstance(obj, dict):
            t = obj.get("__typename", "")
            if t in ("Tweet", "TweetWithVisibilityResults"):
                yield obj
            else:
                for v in obj.values():
                    yield from _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                yield from _walk(item)
    yield from _walk(data)


# ── 公开 API ─────────────────────────────────────────────────────────────────

def get_user_tweets(screen_name: str, limit: int = 10) -> list[dict]:
    """抓取指定账号最新推文。"""
    features = {
        "rweb_lists_timeline_redesign_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "tweetypie_unmention_optimization_enabled": True,
        "responsive_web_edit_tweet_api_enabled": True,
        "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
        "view_counts_everywhere_api_enabled": True,
        "longform_notetweets_consumption_enabled": True,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": False,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": False,
        "responsive_web_enhance_cards_enabled": False,
    }
    # 先获取 user_id
    user_data = _gql_get("UserByScreenName",
                         {"screen_name": screen_name},
                         {"hidden_profile_subscriptions_enabled": True})
    result = user_data["data"]["user"]["result"]
    # rest_id 直接是数字字符串；id 是 base64 编码的 "User:xxxxx"
    if "rest_id" in result:
        user_id = result["rest_id"]
    else:
        import base64
        user_id = base64.b64decode(result["id"]).decode().split(":")[-1]

    data = _gql_get("UserTweets",
                    {"userId": user_id, "count": min(limit * 2, 40),
                     "includePromotedContent": False, "withVoice": True, "withV2Timeline": True},
                    features)

    results = []
    seen = set()
    for node in _iter_timeline_entries(data):
        tweet = _parse_tweet(node)
        if tweet and tweet["id"] not in seen:
            # 跳过转推
            if not tweet["text"].startswith("RT @"):
                seen.add(tweet["id"])
                results.append(tweet)
                if len(results) >= limit:
                    break
    return results


def search_tweets(query: str, limit: int = 20) -> list[dict]:
    """搜索推文（最近 7 天）。"""
    features = {
        "rweb_lists_timeline_redesign_enabled": True,
        "responsive_web_graphql_exclude_directive_enabled": True,
        "verified_phone_label_enabled": False,
        "creator_subscriptions_tweet_preview_api_enabled": True,
        "responsive_web_graphql_timeline_navigation_enabled": True,
        "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
        "longform_notetweets_consumption_enabled": True,
        "responsive_web_twitter_article_tweet_consumption_enabled": False,
        "tweet_awards_web_tipping_enabled": False,
        "freedom_of_speech_not_reach_fetch_enabled": True,
        "standardized_nudges_misinfo": True,
        "longform_notetweets_rich_text_read_enabled": True,
        "longform_notetweets_inline_media_enabled": False,
        "responsive_web_enhance_cards_enabled": False,
    }
    data = _gql_get("SearchTimeline",
                    {"rawQuery": query, "count": min(limit * 2, 40),
                     "querySource": "typed_query", "product": "Latest"},
                    features)

    results = []
    seen = set()
    for node in _iter_timeline_entries(data):
        tweet = _parse_tweet(node)
        if tweet and tweet["id"] not in seen and not tweet["text"].startswith("RT @"):
            seen.add(tweet["id"])
            results.append(tweet)
            if len(results) >= limit:
                break
    return results


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

# 默认关注的 AI 账号（抓最新原创推文作为新闻信号）
DEFAULT_ACCOUNTS = [
    "_akhaliq",       # 每日论文聚合，信号极高
    "karpathy",       # Andrej Karpathy
    "OpenAI",         # OpenAI 官方
    "AnthropicAI",    # Anthropic 官方
    "GoogleDeepMind", # DeepMind 官方
    "huggingface",    # HuggingFace
    "ethanmollick",   # 沃顿教授，AI 实用主义
    "sama",           # Sam Altman
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="X 推文抓取器")
    parser.add_argument("--accounts", help="逗号分隔的账号列表", default=",".join(DEFAULT_ACCOUNTS))
    parser.add_argument("--search", help="搜索关键词（与 --accounts 互斥）")
    parser.add_argument("--limit", type=int, default=3, help="每个账号/搜索返回推文数")
    args = parser.parse_args()

    items, errors = [], []

    if args.search:
        try:
            tweets = search_tweets(args.search, limit=args.limit)
            for t in tweets:
                items.append({
                    "title": t["text"][:80],
                    "url": t["url"],
                    "source": f"@{t['author']}",
                    "content": t["text"],
                    "published": t["created_at"],
                    "media_url": t["media_url"],
                    "source_urls": t["source_urls"],
                    "engagement": t["likes"] + t["retweets"] * 3,
                })
        except Exception as e:
            errors.append({"source": f"search:{args.search}", "error": str(e)})
    else:
        for account in args.accounts.split(","):
            account = account.strip()
            try:
                tweets = get_user_tweets(account, limit=args.limit)
                for t in tweets:
                    items.append({
                        "title": t["text"][:80],
                        "url": t["url"],
                        "source": f"@{t['author']}",
                        "content": t["text"],
                        "published": t["created_at"],
                        "media_url": t["media_url"],
                        "source_urls": t["source_urls"],
                        "engagement": t["likes"] + t["retweets"] * 3,
                    })
                time.sleep(0.5)   # 避免触发限流
            except Exception as e:
                errors.append({"source": f"@{account}", "error": str(e)})

    # 按互动量降序排列
    items.sort(key=lambda x: x.get("engagement", 0), reverse=True)

    print(json.dumps({"items": items, "errors": errors}, ensure_ascii=False, indent=2))
