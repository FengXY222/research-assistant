"""Offline source contracts; all fixtures are synthetic and authored for these tests."""

import json
import hashlib
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from utils import special_issue_sources as sources


FIXTURES = Path(__file__).parent / "fixtures" / "special_issue_sources"
SINCE = datetime(2026, 9, 1)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_aggregator_link_is_discovery_only_and_original_dates_remain_unknown():
    payload = json.dumps({"@type": "CollectionPage", "identifier": "A-1", "name": "Soil Carbon Special Issue", "description": "Short snippet.", "url": "https://call-for-papers.sas.upenn.edu/node/42", "dateExpires": "2027-06-30"})
    result = sources.AggregatorDiscoverySource(fetcher=lambda url: payload).fetch(since=SINCE)
    row = result[0]
    assert row["official_url"] == ""
    assert row["discovery_url"] == "https://call-for-papers.sas.upenn.edu/node/42"
    assert row["source_record_id"] == "A-1"
    assert row["published_at"] == row["updated_at"] == ""
    assert row["fetched_at"]
    assert row["scope_status"] == "snippet"
    assert row["scope_is_complete"] is False
    assert row["provenance"][0]["source_id"] == "aggregator"


@pytest.mark.parametrize("source_type", [sources.FrontiersResearchTopicsSource, sources.ElsevierCallsSource, sources.SpringerCollectionsSource, sources.WileyCallsSource, sources.TaylorFrancisCallsSource, sources.MdpiSpecialIssuesSource, sources.AggregatorDiscoverySource])
def test_all_request_failures_are_not_successful_empty_results(source_type):
    def offline(url):
        raise OSError("offline fixture")
    result = source_type(fetcher=offline).fetch(since=SINCE, keywords=["soil carbon"])
    assert result == []
    assert result.status == "failed"
    assert result.successful_requests == 0
    assert result.errors and "offline fixture" in result.errors[0]["error"]


def test_partial_failure_preserves_results_and_distinguishes_successful_empty():
    def fetch(url):
        if "broken" in url:
            raise OSError("offline fixture")
        return '<script type="application/ld+json">{"name":"Soil Carbon Special Issue","dateExpires":"2027-06-30","url":"https://onlinelibrary.wiley.com/doi/toc/10.1111/soil"}</script>'
    result = sources.WileyCallsSource(fetcher=fetch, urls=["https://broken.example", "https://onlinelibrary.wiley.com/calls"]).fetch(since=SINCE)
    assert len(result) == 1
    assert result.status == "partial"
    empty = sources.TaylorFrancisCallsSource(fetcher=lambda url: "[]").fetch(since=SINCE)
    assert empty.status == "success" and empty == []


def test_access_challenge_tries_the_explicit_elsevier_reader_fallback():
    requested = []
    def fetch(url):
        requested.append(url)
        return "<html><title>Just a moment</title><p>Verify you are human</p></html>"
    result = sources.ElsevierCallsSource(fetcher=fetch).fetch(since=SINCE)
    assert result == []
    assert result.status == "failed"
    assert requested == [
        "https://www.sciencedirect.com/browse/calls-for-papers",
        sources.ElsevierCallsSource.reader_url,
    ]


def test_elsevier_reader_keeps_the_call_detail_url_and_does_not_absorb_neighbor_scope():
    reader = """
    Browse 2 calls for papers for special issues
    [First soil carbon call](https://www.sciencedirect.com/special-issue/101/soil-carbon)
    Guest editors: A. Researcher
    Geoderma - Impact Factor 6.1 - CiteScore 12.4
    Submission deadline: 30 June 2027
    [Unrelated cancer call](https://www.sciencedirect.com/special-issue/202/cancer)
    Guest editors: B. Researcher
    Cancer Letters - Impact Factor 8.1 - CiteScore 15.0
    Submission deadline: 31 July 2027
    """
    rows = sources.ElsevierCallsSource._reader_records(
        reader, "https://www.sciencedirect.com/browse/calls-for-papers"
    )
    assert [row["title"] for row in rows] == ["First soil carbon call", "Unrelated cancer call"]
    assert rows[0]["official_url"].endswith("/special-issue/101/soil-carbon")
    assert "cancer" not in rows[0]["scope_text"].casefold()


def test_wiley_uses_read_only_listing_when_all_direct_call_paths_are_blocked():
    requested = []

    def fetch(url):
        requested.append(url)
        if "/works?" in url:
            return '{"group_by":[{"key":"https://openalex.org/S123","key_display_name":"Soil Research","count":12}]}'
        if "/sources?" in url:
            return '{"results":[{"id":"https://openalex.org/S123","display_name":"Soil Research","type":"journal","host_organization_name":"Wiley","homepage_url":"https://onlinelibrary.wiley.com/journal/12345678","issn_l":"1234-5678"}]}'
        if "r.jina.ai" in url:
            return "[Soil carbon special issue](https://onlinelibrary.wiley.com/doi/toc/10.1111/soil)\nSubmission deadline: 30 June 2027"
        raise OSError("HTTP 403")

    rows = sources.WileyCallsSource(fetcher=fetch).fetch(since=SINCE, keywords=["soil carbon"])
    assert len(rows) == 1
    assert rows[0]["official_url"] == "https://onlinelibrary.wiley.com/doi/toc/10.1111/soil"
    assert rows[0]["is_aggregator"] is True
    assert any("r.jina.ai/http://onlinelibrary.wiley.com/" in url for url in requested)


def test_elsevier_preserves_source_identity_dates_and_identity_query_parameters():
    def fetch(url):
        return 'window.INITIAL_STATE = {"cfpList": []};' if "page=2" in url else fixture("elsevier.html")
    result = sources.ElsevierCallsSource(fetcher=fetch).fetch(since=SINCE)
    row = result[0]
    assert row["source_record_id"] == "EC-42"
    assert row["published_at"] == "2026-08-01"
    assert row["updated_at"] == "2026-09-01"
    assert "edition=2" in row["official_url"]
    assert row["scope_status"] == "snippet"


def test_springer_detail_uses_scope_section_and_original_dates():
    seed = {"title": "Soil Carbon", "official_url": "https://link.springer.com/collections/soil-carbon?edition=2", "scope_text": "Short snippet"}
    row = sources.SpringerCollectionsSource._detail_record(fixture("springer.html"), seed)
    assert "field observations" in row["scope_text"]
    assert "brief search description" not in row["scope_text"]
    assert all(word not in row["scope_text"] for word in ("bad_script", "bad_style", "Navigation", "Footer"))
    assert row["scope_is_complete"] is True
    assert row["scope_status"] == "full"
    assert row["published_at"] == "2026-08-02"
    assert row["updated_at"] == "2026-09-02"
    assert len(row["scope_paragraphs"]) >= 2


def test_taylor_francis_full_copy_preserves_timestamp_and_clean_paragraphs():
    row = sources.TaylorFrancisCallsSource(fetcher=lambda url: fixture("taylor_francis.json")).fetch(since=SINCE)[0]
    assert row["source_record_id"] == "63"
    assert row["published_at"] == "2026-08-03T10:20:00"
    assert row["updated_at"] == "2026-09-03T11:22:00"
    assert row["scope_status"] == "full"
    assert "bad_script" not in row["scope_text"]
    assert len(row["scope_paragraphs"]) == 3


def test_wiley_listing_follows_bounded_same_origin_pagination():
    requested = []
    def fetch(url):
        requested.append(url)
        if "page=2" in url:
            return fixture("wiley.html").replace("soil.carbon?edition=2", "soil.carbon?edition=3").replace('<a rel="next" href="?page=2">Next</a>', "")
        return fixture("wiley.html")
    rows = sources.WileyCallsSource(
        fetcher=fetch,
        urls=["https://onlinelibrary.wiley.com/page/journal/12345678/homepage/call_for_papers.html"],
    ).fetch(since=SINCE)
    assert len(rows) == 2
    assert any("page=2" in url for url in requested)
    assert all(row["scope_is_complete"] is False for row in rows)


def test_dedupe_preserves_distinct_identity_queries_and_case_sensitive_paths():
    rows = [{"title": "Same title", "official_url": url} for url in ["https://example.com/collection?id=1&utm_source=email", "https://example.com/collection?id=1", "https://example.com/collection?id=2", "https://example.com/Collection?id=1"]]
    assert len(sources.WileyCallsSource._dedupe(rows)) == 3


@pytest.mark.parametrize("url", ["https://nature.com.evil.example/journals/soil", "https://evil.example/journal/42", "javascript:https://nature.com/soil", "https://nature.com@evil.example/soil"])
def test_springer_route_does_not_trust_embedded_domain_or_journal_path(url):
    assert sources.SpringerCollectionsSource._route({"homepage_url": url}) is None


def test_invalid_api_json_is_failure_not_empty_success():
    result = sources.TaylorFrancisCallsSource(fetcher=lambda url: "<html>Maintenance</html>").fetch(since=SINCE)
    assert result.status == "failed"
    assert result.errors[0]["stage"] == "parse"


def test_empty_candidate_url_never_becomes_the_listing_url():
    payload = json.dumps({"name": "Soil Carbon Collection", "dateExpires": "2027-06-30"})
    row = sources.WileyCallsSource(
        fetcher=lambda url: payload,
        urls=["https://onlinelibrary.wiley.com/page/journal/12345678/homepage/call_for_papers.html"],
    ).fetch(since=SINCE)[0]
    assert row["official_url"] == ""


def test_elsevier_pagination_keeps_a_later_call_and_records_raw_response_digest():
    first = fixture("elsevier.html")
    second = first.replace("EC-42", "EC-43").replace("edition=2", "edition=3").replace('<a rel="next" href="?page=2">Next</a>', "")
    result = sources.ElsevierCallsSource(fetcher=lambda url: second if "page=2" in url else first).fetch(since=SINCE)
    assert {row["source_record_id"] for row in result} == {"EC-42", "EC-43"}
    assert result[0]["response_sha256"] == hashlib.sha256(first.encode()).hexdigest()
    assert result[0]["provenance"][0]["source_record_id"] == "EC-42"


@pytest.mark.parametrize("source_type", [sources.ElsevierCallsSource, sources.SpringerCollectionsSource, sources.WileyCallsSource])
def test_unrecognized_publisher_response_is_parse_failure(source_type):
    options = (
        {"urls": ["https://onlinelibrary.wiley.com/page/journal/12345678/homepage/call_for_papers.html"]}
        if source_type is sources.WileyCallsSource
        else {}
    )
    result = source_type(fetcher=lambda url: "<html><title>Maintenance</title></html>", **options).fetch(since=SINCE)
    assert result.status == "failed"
    assert result.errors[0]["stage"] == "parse"


def test_springer_invalid_openalex_result_does_not_block_official_discovery():
    def fetch(url):
        if "api.openalex.org" in url:
            return '{"unexpected":true}'
        return '<script type="application/ld+json">{"name":"Soil Carbon Collection","dateExpires":"2027-06-30","url":"https://link.springer.com/collections/soil-carbon"}</script>'
    result = sources.SpringerCollectionsSource(fetcher=fetch).fetch(since=SINCE, keywords=["soil carbon"])
    assert len(result) == 1
    assert result.status == "partial"
    assert any(item["stage"] == "parse" for item in result.errors)


def test_nested_scope_does_not_claim_full_after_only_the_first_nested_div():
    text = fixture("springer.html").replace('<section data-test="collection-description">', '<div data-test="collection-description"><div>First paragraph.</div>').replace("</section>", "<div>Last paragraph with submission requirements.</div></div>")
    row = sources.SpringerCollectionsSource._detail_record(text, {"official_url": "https://link.springer.com/collections/soil-carbon"})
    assert "Last paragraph with submission requirements." in row["scope_text"]
    assert row["scope_is_complete"] is True


def test_missing_deadline_is_retained_for_later_verification():
    text = fixture("springer.html").replace('<span data-test="submission-deadline">Submission deadline<p>30 June 2027</p></span>', "")
    row = sources.SpringerCollectionsSource._detail_record(text, {"official_url": "https://link.springer.com/collections/soil-carbon"})
    assert row and row["deadline"] == ""
    assert row["scope_status"] == "full"


def test_taylor_francis_missing_fee_evidence_does_not_invent_hybrid():
    payload = json.loads(fixture("taylor_francis.json"))
    payload[0]["special_issues"].pop("_open_access")
    row = sources.TaylorFrancisCallsSource(fetcher=lambda url: json.dumps(payload)).fetch(since=SINCE)[0]
    assert row["fee_mode"] == "unknown"


def test_taylor_francis_follows_api_pages_without_losing_earlier_results_on_error():
    first = json.loads(fixture("taylor_francis.json"))[0]
    records = [dict(first, id=index, link=f"https://think.taylorandfrancis.com/special_issues/{index}/") for index in range(20)]
    requested = []
    def fetch(url):
        requested.append(url)
        page = parse_qs(urlsplit(url).query).get("page", ["1"])[0]
        if page == "2":
            raise OSError("second page offline")
        return json.dumps(records)
    result = sources.TaylorFrancisCallsSource(fetcher=fetch).fetch(since=SINCE)
    assert len(result) == 20
    assert result.status == "partial"
    assert any("page=2" in url for url in requested)


def test_query_rotation_gives_late_research_branches_a_discovery_opportunity():
    keywords = [f"research branch {index}" for index in range(10)]
    requested = []
    source = sources.TaylorFrancisCallsSource(fetcher=lambda url: requested.append(url) or "[]")
    source.fetch(since=SINCE, keywords=keywords)
    source.fetch(since=SINCE, keywords=keywords)
    searches = {parse_qs(urlsplit(url).query).get("search", [""])[0] for url in requested}
    assert set(keywords) <= searches


def test_cross_origin_pagination_is_not_fetched_and_reports_gap():
    text = fixture("wiley.html").replace('href="?page=2"', 'href="https://untrusted.example/collect"')
    result = sources.WileyCallsSource(
        fetcher=lambda url: text,
        urls=["https://onlinelibrary.wiley.com/page/journal/12345678/homepage/call_for_papers.html"],
    ).fetch(since=SINCE)
    assert len(result) == 1
    assert result.status == "partial"
    assert result.errors[0]["stage"] == "pagination"


def test_aggregator_original_time_is_extracted_without_using_deadline_time():
    html = '<article class="node-teaser"><h2 class="node-title"><a href="/node/99">Soil special issue</a></h2><time property="dc:date dc:created" datetime="2026-08-01T09:00:00Z">Posted</time><div class="field-name-field-cfp-due-date"><span class="date-display-single">July 31, 2027</span></div><p>Journal special issue.</p></article>'
    row = sources.AggregatorDiscoverySource(fetcher=lambda url: html).fetch(since=SINCE)[0]
    assert row["published_at"] == "2026-08-01T09:00:00Z"
    assert row["updated_at"] == ""
    assert row["source_record_id"] == "99"


def test_cfp_shards_are_current_next_two_then_rolling_and_commit_individually():
    requested = []
    committed = []
    index = json.dumps(
        {
            "months": [
                {"name": "rolling", "file": "rolling.json"},
                {"name": "2026-12", "file": "2026-12.json"},
                {"name": "2026-11", "file": "2026-11.json"},
                {"name": "2026-10", "file": "2026-10.json"},
                {"name": "2026-09", "file": "2026-09.json"},
            ]
        }
    )

    def fetch(url):
        requested.append(url)
        if url.endswith("index.json"):
            return index
        month = Path(url).stem
        return json.dumps(
            [{"id": month, "t": f"Soil carbon {month}", "d": "2027-06-30", "x": "soil carbon"}]
        )

    result = sources.JournalCfpDdlSource(fetcher=fetch).fetch(
        since=SINCE,
        today=date(2026, 9, 17),
        keywords=["soil carbon"],
        on_shard=lambda **payload: committed.append(payload),
    )
    assert [value["shard_id"] for value in committed] == ["2026-09", "2026-10", "2026-11", "rolling"]
    assert len(result) == 4
    assert not any("2026-12.json" in url for url in requested)


def test_cfp_retry_skips_successful_shards_and_can_cancel_inside_one_shard():
    index = json.dumps(
        {"months": [
            {"name": "2026-09", "file": "2026-09.json"},
            {"name": "2026-10", "file": "2026-10.json"},
            {"name": "2026-11", "file": "2026-11.json"},
            {"name": "rolling", "file": "rolling.json"},
        ]}
    )
    requested = []
    checks = {"count": 0}

    def fetch(url):
        requested.append(url)
        if url.endswith("index.json"):
            return index
        return json.dumps(
            [
                {"id": "one", "t": "Soil carbon one", "d": "2027-06-30", "x": "soil carbon"},
                {"id": "two", "t": "Soil carbon two", "d": "2027-06-30", "x": "soil carbon"},
            ]
        )

    def cancelled():
        checks["count"] += 1
        return checks["count"] >= 4

    committed = []
    result = sources.JournalCfpDdlSource(fetcher=fetch).fetch(
        since=SINCE,
        today=date(2026, 9, 17),
        keywords=["soil carbon"],
        shard_checkpoints={"2026-09": {"status": "success"}, "2026-10": {"status": "failed"}, "2026-11": {"status": "success"}, "rolling": {"status": "success"}},
        retry_failed_only=True,
        cancelled=cancelled,
        on_shard=lambda **payload: committed.append(payload),
    )
    assert not any("2026-09.json" in url or "2026-11.json" in url or "rolling.json" in url for url in requested)
    assert any("2026-10.json" in url for url in requested)
    assert committed[-1]["status"] == "cancelled"
    assert result.status == "partial"
