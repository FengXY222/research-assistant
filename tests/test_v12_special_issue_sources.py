"""Fixture-driven multi-source adapter contracts for special-issue discovery."""

from __future__ import annotations

from datetime import datetime

from utils.special_issue_sources import (
    AggregatorDiscoverySource,
    GeoDeadlinesSource,
    JournalCfpDdlSource,
    ElsevierCallsSource,
    FrontiersResearchTopicsSource,
    MdpiSpecialIssuesSource,
    ResearchCollectionRadarSource,
    SpringerCollectionsSource,
    TaylorFrancisCallsSource,
    WileyCallsSource,
    default_special_issue_sources,
)


HTML_FIXTURE = """
<html><head><script type="application/ld+json">
{"@type":"CollectionPage","itemListElement":[
 {"@type":"CreativeWork","name":"Soil Carbon Mapping Collection",
  "description":"Remote sensing and digital soil mapping.",
  "url":"https://publisher.example/soil-carbon",
  "dateExpires":"2027-06-30",
  "isPartOf":{"name":"Geoderma"}}
]}
</script></head><body></body></html>
"""


def test_production_sources_disable_elsevier_official_and_keep_third_party_discovery() -> None:
    sources = default_special_issue_sources()
    assert not any(isinstance(source, ElsevierCallsSource) for source in sources)
    assert any(isinstance(source, GeoDeadlinesSource) for source in sources)
    assert any(isinstance(source, ResearchCollectionRadarSource) for source in sources)
    assert any(isinstance(source, JournalCfpDdlSource) for source in sources)


def test_all_official_source_adapters_emit_normalizable_records_from_structured_data() -> None:
    since = datetime(2026, 8, 30)
    for source_type in (
        FrontiersResearchTopicsSource,
        ElsevierCallsSource,
        SpringerCollectionsSource,
        WileyCallsSource,
        MdpiSpecialIssuesSource,
    ):
        source = (
            source_type(
                fetcher=lambda _url: HTML_FIXTURE,
                urls=["https://onlinelibrary.wiley.com/page/journal/12345678/homepage/call_for_papers.html"],
            )
            if source_type is WileyCallsSource
            else source_type(fetcher=lambda _url: HTML_FIXTURE)
        )
        rows = source.fetch(since=since)
        assert len(rows) == 1, source_type.__name__
        assert rows[0]["title"] == "Soil Carbon Mapping Collection"
        assert rows[0]["deadline"] == "2027-06-30"
        assert rows[0]["source"] == source.source_id
        assert rows[0]["is_aggregator"] is False


def test_aggregator_rows_are_marked_unverified_and_preserve_discovery_url() -> None:
    source = AggregatorDiscoverySource(fetcher=lambda _url: HTML_FIXTURE, urls=["https://feed.example/calls"])
    rows = source.fetch(since=datetime(2026, 8, 30))

    assert rows[0]["is_aggregator"] is True
    assert rows[0]["discovery_url"] == "https://feed.example/calls"
    assert rows[0]["source"] == "aggregator"


def test_one_failed_aggregator_endpoint_does_not_abort_other_endpoints() -> None:
    def fetch(url: str) -> str:
        if "broken" in url:
            raise OSError("offline")
        return HTML_FIXTURE

    progress: list[str] = []
    source = AggregatorDiscoverySource(
        fetcher=fetch,
        urls=["https://broken.example", "https://working.example"],
    )
    rows = source.fetch(since=datetime(2026, 8, 30), progress=lambda message, _value=0: progress.append(message))

    assert len(rows) == 1
    assert any("失败" in message for message in progress)


def test_frontiers_listing_fetches_only_open_relevant_topic_details() -> None:
    listing = """
    <article class="CardResearchTopic"><a href="https://www.frontiersin.org/research-topics/1/soil-carbon">
    <p>Submission open</p><h2 class="CardResearchTopic__title">Soil carbon mapping</h2></a></article>
    <article class="CardResearchTopic"><a href="https://www.frontiersin.org/research-topics/2/closed">
    <p>Submission closed</p><h2 class="CardResearchTopic__title">Closed soil topic</h2></a></article>
    """
    detail = """
    <title>Frontiers | Soil carbon mapping</title>
    <a href="https://www.frontiersin.org/journals/soil-science" aria-label="Frontiers in Soil Science">Frontiers in Soil Science</a>
    <p>Manuscript Submission Deadline 30 June 2027</p><p>This Research Topic is currently accepting articles</p>
    <h3 class="RTOverviewBackground__title">Background</h3><div>Digital soil mapping and remote sensing.</div><div class="RTOverviewKeywords"></div>
    """

    def fetch(url: str) -> str:
        return detail if "/research-topics/1/" in url else listing

    source = FrontiersResearchTopicsSource(fetcher=fetch, urls=["https://frontiers.example/topics"])
    rows = source.fetch(since=datetime(2026, 8, 30), keywords=["soil carbon"])
    assert len(rows) == 1
    assert rows[0]["title"] == "Soil carbon mapping"
    assert rows[0]["journal"] == "Frontiers in Soil Science"
    assert rows[0]["deadline"] == "30 June 2027"


def test_mdpi_parser_keeps_only_title_links_not_editor_or_section_links() -> None:
    html = """
    <div class="generic-item article-item"><div class="article-content">
      <a class="title-link" href="/journal/land/special_issues/ABC">Digital Soil Mapping</a>
      <div class="authors"><a href="https://sciprofiles.com/editor">Editor Name</a></div>
      submission deadline <strong>30 Jun 2027</strong> | <span>Submission Open</span>
      <div><em>Keywords:</em> soil carbon; remote sensing</div>
      <div>(This special issue belongs to the Section <a href="/section">Land, Soil and Water</a>)</div>
    </div></div>
    """
    source = MdpiSpecialIssuesSource(fetcher=lambda _url: html, urls=["https://www.mdpi.com/journal/land/special_issues"])
    rows = source.fetch(since=datetime(2026, 8, 30))
    assert len(rows) == 1
    assert rows[0]["title"] == "Digital Soil Mapping"
    assert rows[0]["journal"] == "Land"
    assert "Editor Name" not in [row["title"] for row in rows]


def test_live_style_aggregator_parser_rejects_conference_and_keeps_explicit_special_issue() -> None:
    html = """
    <article class="node-teaser"><h2 class="node-title"><a href="/node/1">Workshop on Soil</a></h2>
      <div class="field-name-field-cfp-due-date"><span class="date-display-single">Saturday, July 31, 2027</span></div>
      <p>Conference workshop.</p></article>
    <article class="node-teaser"><h2 class="node-title"><a href="/node/2">Journal special issue on soil carbon</a></h2>
      <div class="field-name-field-cfp-due-date"><span class="date-display-single">Saturday, July 31, 2027</span></div>
      <p>Special issue accepting research articles.</p></article>
    """
    source = AggregatorDiscoverySource(fetcher=lambda _url: html, urls=["https://feed.example/journals"])
    rows = source.fetch(since=datetime(2026, 8, 30))
    assert len(rows) == 1
    assert rows[0]["title"] == "Journal special issue on soil carbon"
    assert rows[0]["deadline"] == "July 31, 2027"


def test_taylor_francis_uses_official_wordpress_api_and_preserves_full_scope() -> None:
    payload = """
    [{
      "id": 63314,
      "modified": "2026-06-10T13:09:35",
      "link": "https://think.taylorandfrancis.com/special_issues/soil-carbon/",
      "title": {"rendered": "Soil Carbon Mapping for Sustainable Land Management"},
      "meta": {"meta-page-expiry-date": "2027-02-28"},
      "special_issues": {
        "_special_issues_deadline": ["26 February 2027"],
        "_special_issues_journal_title": ["Soil Science and Plant Nutrition"],
        "_special_issues_copy": ["<p>We welcome digital soil mapping and carbon sequestration studies.</p>"],
        "_special_issues_submissions_instructions": ["<p>Select the special issue during submission.</p>"],
        "_special_issues_submissions_submit": ["https://mc.manuscriptcentral.com/sspn"],
        "_open_access": ["0"]
      }
    }]
    """
    requested: list[str] = []

    def fetch(url: str) -> str:
        requested.append(url)
        return payload

    source = TaylorFrancisCallsSource(fetcher=fetch)
    rows = source.fetch(since=datetime(2026, 8, 30), keywords=["soil carbon", "remote sensing"])

    assert len(rows) == 1
    assert "wp-json/wp/v2/special_issues" in requested[0]
    assert "search=soil+carbon" in requested[0]
    assert rows[0]["title"] == "Soil Carbon Mapping for Sustainable Land Management"
    assert rows[0]["journal"] == "Soil Science and Plant Nutrition"
    assert rows[0]["deadline"] == "26 February 2027"
    assert "digital soil mapping" in rows[0]["scope_text"]
    assert rows[0]["publisher"] == "Taylor & Francis"
    assert rows[0]["is_aggregator"] is False


def test_taylor_francis_drops_weak_single_token_matches() -> None:
    payload = """
    [{
      "id": 63314,
      "link": "https://think.taylorandfrancis.com/special_issues/soil-carbon/",
      "title": {"rendered": "Soil Carbon Mapping for Sustainable Land Management"},
      "special_issues": {
        "_special_issues_deadline": ["26 February 2027"],
        "_special_issues_journal_title": ["Soil Science and Plant Nutrition"],
        "_special_issues_copy": ["Digital soil mapping and soil organic carbon."],
        "_open_access": ["0"]
      }
    }, {
      "id": 63315,
      "link": "https://think.taylorandfrancis.com/special_issues/digital-economy/",
      "title": {"rendered": "The Digital Economy and Consumer Marketing"},
      "special_issues": {
        "_special_issues_deadline": ["30 March 2027"],
        "_special_issues_journal_title": ["Marketing Review"],
        "_special_issues_copy": ["Digital consumer behaviour."],
        "_open_access": ["0"]
      }
    }, {
      "id": 63316,
      "link": "https://think.taylorandfrancis.com/special_issues/social-work/",
      "title": {"rendered": "Social Work in a Digital Society"},
      "special_issues": {
        "_special_issues_deadline": ["30 April 2027"],
        "_special_issues_journal_title": ["Journal of Social Work"],
        "_special_issues_copy": ["Social services and digital inclusion."],
        "_open_access": ["0"]
      }
    }]
    """

    rows = TaylorFrancisCallsSource(fetcher=lambda _url: payload).fetch(
        since=datetime(2026, 8, 30),
        keywords=["digital soil mapping", "SOC", "soil organic carbon"],
    )

    assert [row["title"] for row in rows] == ["Soil Carbon Mapping for Sustainable Land Management"]


def test_elsevier_blocked_source_uses_reader_fallback() -> None:
    reader = """
    Title: Browse Calls for Papers | ScienceDirect.com
    URL Source: http://www.sciencedirect.com/browse/calls-for-papers
    Markdown Content:
    Browse 2790 calls for papers for special issues
    Digital soil mapping and soil organic carbon monitoring
    Guest editors: A. Researcher, B. Scientist
    Geoderma - Impact Factor 6.1 - CiteScore 12.4
    Submission deadline: 30 June 2027
    Cancer biomarkers for precision medicine
    Guest editor: C. Researcher
    Cancer Letters - Impact Factor 8.1 - CiteScore 15.0
    Submission deadline: 31 July 2027
    """

    def fetch(url: str) -> str:
        if "r.jina.ai" in url:
            return reader
        raise OSError("ScienceDirect blocked direct access")

    source = ElsevierCallsSource(fetcher=fetch)
    rows = source.fetch(since=datetime(2026, 8, 30), keywords=["soil carbon"])

    assert [row["title"] for row in rows] == ["Digital soil mapping and soil organic carbon monitoring"]
    assert rows.status == "partial"
    assert rows[0]["publisher"] == "Elsevier"
    assert rows[0]["discovery_url"] == ElsevierCallsSource.reader_url
    assert "blocked direct access" in rows.errors[0]["error"]


def test_wiley_discovers_relevant_journal_then_uses_current_official_call_path() -> None:
    requested: list[str] = []

    def fetch(url: str) -> str:
        requested.append(url)
        if "/works?" in url:
            return '{"group_by":[{"key":"https://openalex.org/S123","key_display_name":"Soil Research","count":12}]}'
        if "/sources?" in url:
            return '{"results":[{"id":"https://openalex.org/S123","display_name":"Soil Research","type":"journal","host_organization_name":"Wiley","homepage_url":"https://onlinelibrary.wiley.com/journal/12345678","issn_l":"1234-5678"}]}'
        if "/page/journal/12345678/homepage/call_for_papers.html" in url:
            return HTML_FIXTURE
        raise AssertionError(url)

    rows = WileyCallsSource(fetcher=fetch).fetch(
        since=datetime(2026, 8, 30), keywords=["soil carbon"]
    )

    assert len(rows) == 1
    assert any("/page/journal/12345678/homepage/call_for_papers.html" in url for url in requested)
    assert rows[0]["publisher"] == "Wiley"


def test_wiley_discovery_caps_openalex_candidates_and_journal_batch() -> None:
    requested: list[str] = []
    groups = [
        {"key": f"https://openalex.org/S{index}", "key_display_name": f"Journal {index}", "count": 100-index}
        for index in range(60)
    ]

    def fetch(url: str) -> str:
        import json
        from urllib.parse import parse_qs, urlsplit

        requested.append(url)
        if "/works?" in url:
            return json.dumps({"group_by": groups})
        if "/sources?" in url:
            selected = parse_qs(urlsplit(url).query)["filter"][0].split(":", 1)[1].split("|")
            assert len(selected) == WileyCallsSource.journal_candidate_limit
            return json.dumps(
                {
                    "results": [
                        {
                            "id": f"https://openalex.org/{source_id}",
                            "display_name": source_id,
                            "type": "journal",
                            "host_organization_name": "Wiley",
                            "homepage_url": f"https://onlinelibrary.wiley.com/journal/{source_id[1:].zfill(8)}",
                        }
                        for source_id in selected
                    ]
                }
            )
        raise AssertionError(url)

    journals = WileyCallsSource(fetcher=fetch)._openalex_journals(["soil carbon"])

    assert len(journals) == WileyCallsSource.journal_batch_limit
    assert sum("/sources?" in url for url in requested) == 1


def test_wiley_stops_after_two_journals_with_all_routes_unavailable() -> None:
    requested: list[str] = []

    def fetch(url: str) -> str:
        import json

        requested.append(url)
        if "/works?" in url:
            return json.dumps(
                {
                    "group_by": [
                        {"key": f"https://openalex.org/S{index}", "key_display_name": f"Journal {index}", "count": 20-index}
                        for index in range(1, 6)
                    ]
                }
            )
        if "/sources?" in url:
            return json.dumps(
                {
                    "results": [
                        {
                            "id": f"https://openalex.org/S{index}",
                            "display_name": f"Journal {index}",
                            "type": "journal",
                            "host_organization_name": "Wiley",
                            "homepage_url": f"https://onlinelibrary.wiley.com/journal/{index:08d}",
                        }
                        for index in range(1, 6)
                    ]
                }
            )
        raise OSError("publisher route unavailable")

    result = WileyCallsSource(fetcher=fetch).fetch(
        since=datetime(2026, 8, 30), keywords=["soil carbon"]
    )

    publisher_requests = [url for url in requested if "onlinelibrary.wiley.com" in url]
    assert result == []
    assert len(publisher_requests) == (
        WileyCallsSource.consecutive_failure_limit * WileyCallsSource.route_limit * 2
    )
    assert any(error["stage"] == "circuit_breaker" for error in result.errors)


def test_elsevier_official_discovery_keeps_only_keyword_relevant_current_calls() -> None:
    import json

    payload = "window.INITIAL_STATE = " + json.dumps({"cfpList": [
        {"contentId": "soil", "url": "soil-carbon", "title": "Digital soil mapping and soil organic carbon monitoring", "journal": {"displayName": "Geoderma"}, "submissionDeadline": "30 June 2027"},
        {"contentId": "cancer", "url": "biomarkers", "title": "Cancer biomarkers for precision medicine", "journal": {"displayName": "Cancer Letters"}, "submissionDeadline": "31 July 2027"},
    ]})
    rows = ElsevierCallsSource(fetcher=lambda _url: payload).fetch(since=datetime(2026, 8, 30), keywords=["soil carbon"])
    assert len(rows) == 1
    assert rows[0]["title"] == "Digital soil mapping and soil organic carbon monitoring"
    assert rows[0]["journal"] == "Geoderma"
    assert rows[0]["deadline"] == "30 June 2027"
    assert rows[0]["publisher"] == "Elsevier"
    assert rows[0]["is_aggregator"] is False


def test_springer_source_routes_openalex_journals_and_enriches_collection_deadlines() -> None:
    listing = """
    <script type="application/ld+json" data-test="springer-journal-collections-listing-structured-data">
    {"@type":"CollectionPage","isPartOf":[{"@type":"Periodical","name":"Soil Systems Research"}],
     "mainEntity":{"@type":"ItemList","itemListElement":[
       {"@type":"ListItem","name":"Digital Soil Mapping and Carbon Stocks",
        "url":"https://link.springer.com/collections/soilcarbon",
        "description":"Remote sensing of soil organic carbon."}]}}
    </script>
    """
    detail = """
    <script type="application/ld+json" data-test="collection-page-schema">
    {"@type":"CollectionPage","headline":"Digital Soil Mapping and Carbon Stocks",
     "url":"https://link.springer.com/collections/soilcarbon",
     "publisher":{"name":"Springer Nature"},
     "description":"Full scope for remote sensing and soil organic carbon mapping.",
     "isPartOf":[{"@type":"Periodical","name":"Soil Systems Research"}]}
    </script>
    <span data-test="submission-deadline">Submission deadline
      <p class="date">30 June 2027</p></span>
    """

    def fetch(url: str) -> str:
        if "/works?" in url:
            return '{"group_by":[{"key":"https://openalex.org/S123","key_display_name":"Soil Systems Research","count":42}]}'
        if "/sources?" in url:
            return '{"results":[{"id":"https://openalex.org/S123","display_name":"Soil Systems Research","type":"journal","host_organization_name":"Springer Nature","homepage_url":"https://link.springer.com/journal/12053","issn_l":"1234-5678"}]}'
        if "/journal/12053/collections" in url:
            return listing
        if "/collections/soilcarbon" in url:
            return detail
        raise AssertionError(url)

    source = SpringerCollectionsSource(fetcher=fetch)
    rows = source.fetch(since=datetime(2026, 8, 30), keywords=["soil carbon"])

    assert len(rows) == 1
    assert rows[0]["title"] == "Digital Soil Mapping and Carbon Stocks"
    assert rows[0]["journal"] == "Soil Systems Research"
    assert rows[0]["deadline"] == "30 June 2027"
    assert rows[0]["issn"] == "1234-5678"
    assert rows[0]["publisher"] == "Springer Nature"
    assert rows[0]["is_aggregator"] is False
