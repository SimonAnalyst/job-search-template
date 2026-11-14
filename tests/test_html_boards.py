import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from jobsearch import html_boards, sources


class HtmlBoardTests(unittest.TestCase):
    def test_unknown_board_is_rejected(self):
        with self.assertRaises(sources.SourceError):
            html_boards.fetch("unknown", keywords=["test"], location="", pages=1, timeout=3, options={})

    def test_pracuj_jsonld_extracts_only_allowlisted_fields(self):
        page = '''<html><script type="application/ld+json">{"@type":"JobPosting","identifier":"job-9","title":"Office assistant","hiringOrganization":{"name":"Demo Co"},"jobLocation":{"address":{"addressLocality":"Krakow"}},"url":"https://www.pracuj.pl/praca/office-assistant,oferta,job-9","description":"Private description"}</script></html>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertIn("limit", (batch.error or "").casefold())
        self.assertEqual(len(batch.offers), 1)
        offer = batch.offers[0]
        self.assertEqual(offer["title"], "Office assistant")
        self.assertEqual(offer["company"], "Demo Co")
        self.assertEqual(offer["location"], "Krakow")
        self.assertNotIn("description", offer)
        self.assertNotIn("Private description", str(offer))
        self.assertEqual(set(offer), {"id", "title", "company", "location", "url", "source", "_match_text"})

    def test_jsonld_and_html_card_for_same_offer_merge_missing_fields(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"job-9","title":"Office assistant","url":"https://www.pracuj.pl/praca/office-assistant,oferta,job-9"}</script><article><a data-test="link-offer-title" href="https://www.pracuj.pl/praca/office-assistant,oferta,job-9"><h2 data-test="offer-title">Office assistant</h2></a><h3 data-test="text-company-name">Demo Co</h3><h4 data-test="text-region">Krakow</h4></article>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["company"], "Demo Co")
        self.assertEqual(batch.offers[0]["location"], "Krakow")

    def test_pracuj_and_protocol_offer_urls_require_nonempty_offer_id(self):
        for name, url in (
            ("pracuj", "https://www.pracuj.pl/praca/offer-offerta-match"),
            ("pracuj", "https://www.pracuj.pl/praca/title,oferta,"),
            ("theprotocol", "https://theprotocol.it/praca/title,oferta,"),
        ):
            page = f'''<script type="application/ld+json">{{"@type":"JobPosting","title":"Office manager","url":"{url}"}}</script>'''
            with self.subTest(source=name, url=url), patch.object(html_boards, "_fetch_html", return_value=page):
                with self.assertRaisesRegex(sources.SourceError, "URL|adres|ofert"):
                    html_boards.fetch(name, keywords=["office"], location="", pages=1, timeout=3, options={})

    def test_jsonld_multiple_locations_are_combined_uniquely(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office assistant","url":"https://www.pracuj.pl/praca/office-assistant,oferta,job-9","jobLocation":[{"address":{"addressLocality":"Warszawa","addressRegion":"Mazowieckie"}},{"address":{"addressLocality":"Warszawa","addressRegion":"Mazowieckie"}},{"address":{"addressLocality":"Krakow","addressRegion":"Małopolskie"}}]}</script>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        location = batch.offers[0]["location"]
        self.assertIn("Warszawa", location)
        self.assertIn("Krakow", location)
        self.assertEqual(location.casefold().count("warszawa"), 1)

    def test_captcha_word_in_valid_job_title_is_not_a_block(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Captcha test engineer","url":"https://jobhunt.pl/oferta/captcha-test-engineer"}</script>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("jobhunt", keywords=["captcha"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["title"], "Captcha test engineer")

    def test_pracuj_server_rendered_offer_tile_extracts_title_company_and_url(self):
        page = '''<article><a data-test="link-offer-title" href="https://www.pracuj.pl/praca/office-assistant,oferta,job-9"><h2 data-test="offer-title">Office assistant</h2></a><h3 data-test="text-company-name">Demo Co</h3><h4 data-test="text-region">Krakow</h4></article>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["title"], "Office assistant")
        self.assertEqual(batch.offers[0]["company"], "Demo Co")
        self.assertEqual(batch.offers[0]["location"], "Krakow")
        self.assertEqual(batch.offers[0]["url"], "https://www.pracuj.pl/praca/office-assistant,oferta,job-9")

    def test_pracuj_supports_link_offer_as_card_link(self):
        page = '''<article><h2 data-test="offer-title">Office assistant</h2><a data-test="link-offer" href="/praca/office-assistant,oferta,job-9">Szczegóły</a><h3 data-test="text-company-name">Demo Co</h3><h4 data-test="text-region">Krakow</h4></article>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["url"], "https://www.pracuj.pl/praca/office-assistant,oferta,job-9")

    def test_two_pracuj_cards_keep_links_opened_before_each_title(self):
        page = '''<div class="offer-tile_root"><a data-test="link-offer-title" href="/praca/alpha,oferta,A">Details</a><h2 data-test="offer-title">Offer A</h2><h3 data-test="text-company-name">Company A</h3><h4 data-test="text-region">Warszawa</h4></div><div class="offer-tile_root"><a data-test="link-offer-title" href="/praca/beta,oferta,B">Details</a><h2 data-test="offer-title">Offer B</h2><h3 data-test="text-company-name">Company B</h3><h4 data-test="text-region">Krakow</h4></div>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        by_title = {row["title"]: row["url"] for row in batch.offers}
        self.assertEqual(by_title, {
            "Offer A": "https://www.pracuj.pl/praca/alpha,oferta,A",
            "Offer B": "https://www.pracuj.pl/praca/beta,oferta,B",
        })

    def test_two_pracuj_cards_keep_links_opened_after_each_title(self):
        page = '''<div class="offer-tile_root"><h2 data-test="offer-title">Offer A</h2><a data-test="link-offer-title" href="/praca/alpha,oferta,A">Details</a><h3 data-test="text-company-name">Company A</h3></div><div class="offer-tile_root"><h2 data-test="offer-title">Offer B</h2><a data-test="link-offer" href="/praca/beta,oferta,B">Details</a><h3 data-test="text-company-name">Company B</h3></div>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        by_title = {row["title"]: row["url"] for row in batch.offers}
        self.assertEqual(by_title, {
            "Offer A": "https://www.pracuj.pl/praca/alpha,oferta,A",
            "Offer B": "https://www.pracuj.pl/praca/beta,oferta,B",
        })

    def test_two_article_cards_bind_post_heading_links_to_their_own_titles(self):
        page = '''<article><h2 data-test="offer-title">Office A</h2><a data-test="link-offer-title" href="/praca/office-a,oferta,A">Details A</a><h4 data-test="text-region">Warszawa</h4></article><article><h2 data-test="offer-title">Office B</h2><a data-test="link-offer-title" href="/praca/office-b,oferta,B">Details B</a><h4 data-test="text-region">Krakow</h4></article>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        by_title = {row["title"]: row["url"] for row in batch.offers}
        self.assertEqual(by_title, {
            "Office A": "https://www.pracuj.pl/praca/office-a,oferta,A",
            "Office B": "https://www.pracuj.pl/praca/office-b,oferta,B",
        })

    def test_nested_offer_tile_body_does_not_start_a_new_pracuj_card(self):
        page = '''<div class="offer-tile_root"><div class="offer-tile_body"><h2 data-test="offer-title">Office A</h2></div><a data-test="link-offer-title" href="/praca/office-a,oferta,A">Details A</a><h4 data-test="text-region">Warszawa</h4></div><div class="offer-tile_root"><div class="offer-tile_body"><h2 data-test="offer-title">Office B</h2></div><a data-test="link-offer" href="/praca/office-b,oferta,B">Details B</a><h4 data-test="text-region">Krakow</h4></div>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        by_title = {row["title"]: row["url"] for row in batch.offers}
        self.assertEqual(by_title, {
            "Office A": "https://www.pracuj.pl/praca/office-a,oferta,A",
            "Office B": "https://www.pracuj.pl/praca/office-b,oferta,B",
        })

    def test_pracuj_and_protocol_portal_ids_ignore_search_id_across_encodings_and_runs(self):
        fixtures = {
            "pracuj": [
                '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"unstable-a","title":"Office role","url":"https://www.pracuj.pl/praca/office-role,oferta,portal-1?searchId=first"}</script>''',
                '''<article><h2 data-test="offer-title">Office role</h2><a data-test="link-offer-title" href="/praca/office-role,oferta,portal-1?searchId=second">Details</a></article>''',
            ],
            "theprotocol": [
                '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"unstable-b","title":"Office role","url":"https://theprotocol.it/praca/office-role,oferta,portal-2?searchId=first"}</script>''',
                '''<a href="/praca/office-role,oferta,portal-2?searchId=second"><h2>Office role</h2></a>''',
            ],
        }
        for name, pages in fixtures.items():
            ids = []
            for page in pages:
                with self.subTest(source=name, page=page), patch.object(html_boards, "_fetch_html", return_value=page):
                    batch = html_boards.fetch(name, keywords=["office"], location="", pages=1, timeout=3, options={})
                self.assertEqual(len(batch.offers), 1)
                ids.append(batch.offers[0]["id"])
            self.assertEqual(ids[0], ids[1])

    def test_query_terms_are_url_encoded_and_searched_independently(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office manager","url":"https://jobhunt.pl/oferta/office-manager"}</script>'''
        with patch.object(html_boards, "_fetch_html", return_value=page) as mock_fetch:
            batch = html_boards.fetch("jobhunt", keywords=["office manager", "koordynator"], location="Warszawa", pages=1, timeout=3, options={})
        self.assertEqual(mock_fetch.call_count, 2)
        first_url = mock_fetch.call_args_list[0].args[0]
        self.assertEqual(parse_qs(urlsplit(first_url).query)["q"], ["office manager"])
        self.assertEqual(parse_qs(urlsplit(first_url).query)["city"], ["Warszawa"])
        self.assertEqual(parse_qs(urlsplit(mock_fetch.call_args_list[1].args[0]).query)["q"], ["koordynator"])
        self.assertEqual(batch.offers[0]["source"], "jobhunt")

    def test_jobhunt_server_rendered_offer_h3_inside_offer_link(self):
        page = '''<article><a href="/oferta/office-manager-123"><h3>Office manager</h3></a><p>Demo Co</p></article>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("jobhunt", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["title"], "Office manager")
        self.assertEqual(batch.offers[0]["url"], "https://jobhunt.pl/oferta/office-manager-123")

    def test_jobhunt_uses_specific_visible_offer_link_text_without_heading(self):
        page = '''<a href="/oferta/office-manager-123">Office manager</a>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("jobhunt", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["title"], "Office manager")

    def test_jobhunt_generic_offer_link_label_is_not_used_as_title(self):
        page = '''<a href="/oferta/office-manager-123">Zobacz ofertę</a>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            with self.assertRaisesRegex(sources.SourceError, "tytuł|etykiet|niepewn"):
                html_boards.fetch("jobhunt", keywords=["office"], location="", pages=1, timeout=3, options={})

    def test_plain_script_text_does_not_trigger_captcha_detection(self):
        page = '''<script>window.config = "captcha provider";</script><script type="application/ld+json">{"@type":"JobPosting","title":"Office manager","url":"https://jobhunt.pl/oferta/office-manager-123"}</script>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("jobhunt", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)

    def test_jsonld_offer_candidates_with_wrong_host_or_path_are_errors(self):
        pages = [
            '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office manager","url":"https://evil.example/praca/offer"}</script>''',
            '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office manager","url":"https://jobhunt.pl/praca/office-manager"}</script>''',
        ]
        for page in pages:
            with self.subTest(page=page), patch.object(html_boards, "_fetch_html", return_value=page):
                with self.assertRaisesRegex(sources.SourceError, "URL|adres|ofert"):
                    html_boards.fetch("jobhunt", keywords=["office"], location="", pages=1, timeout=3, options={})

    def test_theprotocol_search_page_extracts_title_and_offer_link_from_heading_anchor(self):
        page = '''<a href="https://theprotocol.it/praca/office-manager,oferta,tp-1"><div><h2>Office manager</h2><span>Demo Co</span></div></a>'''
        with patch.object(html_boards, "_fetch_html", return_value=page) as mock_fetch:
            batch = html_boards.fetch("theprotocol", keywords=["office manager"], location="", pages=1, timeout=3, options={})
        self.assertEqual(batch.error, None)
        self.assertEqual(batch.offers[0]["title"], "Office manager")
        self.assertEqual(batch.offers[0]["url"], "https://theprotocol.it/praca/office-manager,oferta,tp-1")
        self.assertEqual(batch.offers[0]["location"], "")
        self.assertIn("office", mock_fetch.call_args.args[0])

    def test_missing_location_is_reported_when_location_filter_was_requested(self):
        page = '''<a href="https://theprotocol.it/praca/office-manager,oferta,tp-1"><h2>Office manager</h2></a>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("theprotocol", keywords=["office"], location="Warszawa", pages=1, timeout=3, options={})
        self.assertEqual(batch.offers[0]["location"], "")
        self.assertIsNotNone(batch.error)

    def test_duplicate_postings_across_keywords_are_deduplicated(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"job-1","title":"Office assistant","url":"https://www.pracuj.pl/praca/office-assistant,oferta,job-1"}</script>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office", "assistant"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)

    def test_partial_network_failure_keeps_successful_query(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office assistant","url":"https://jobhunt.pl/oferta/job-1"}</script>'''
        def response(url, timeout, *, source):
            if "second" in url:
                raise sources.SourceError("odpowiedź HTTP 403")
            return page
        with patch.object(html_boards, "_fetch_html", side_effect=response):
            batch = html_boards.fetch("jobhunt", keywords=["first", "second"], location="", pages=1, timeout=3, options={})
        self.assertEqual(len(batch.offers), 1)
        self.assertIsNotNone(batch.error)

    def test_all_pages_failing_is_error(self):
        with patch.object(html_boards, "_fetch_html", side_effect=sources.SourceError("odpowiedź HTTP 403")):
            with self.assertRaises(sources.SourceError):
                html_boards.fetch("pracuj", keywords=["assistant"], location="", pages=1, timeout=3, options={})

    def test_block_page_is_not_accepted_as_zero_offers(self):
        with patch.object(html_boards, "_fetch_html", return_value="<html><title>Access denied</title><h1>Access denied</h1></html>"):
            with self.assertRaises(sources.SourceError):
                html_boards.fetch("pracuj", keywords=["assistant"], location="", pages=1, timeout=3, options={})

    def test_unrecognized_html_is_not_accepted_as_zero_offers(self):
        with patch.object(html_boards, "_fetch_html", return_value="<html><title>Welcome</title><p>Some page</p></html>"):
            with self.assertRaisesRegex(sources.SourceError, "nieobsługiwany HTML"):
                html_boards.fetch("pracuj", keywords=["assistant"], location="", pages=1, timeout=3, options={})

    def test_explicit_no_results_message_is_a_valid_empty_search(self):
        page = "<html><p>Nie znaleźliśmy ofert pracy.</p></html>"
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["assistant"], location="", pages=1, timeout=3, options={})
        self.assertEqual(batch.offers, [])

    def test_robots_denial_stops_before_page_request(self):
        with patch.object(html_boards, "_robots_allows", return_value=False), \
             patch.object(html_boards.urllib.request, "build_opener") as make_opener:
            with self.assertRaisesRegex(sources.SourceError, "robots.txt"):
                html_boards._fetch_html("https://www.pracuj.pl/praca/test", 3, source="pracuj")
        make_opener.assert_not_called()

    def test_robots_wildcard_rule_is_honored(self):
        robots = "User-agent: *\nDisallow: /*?tracking=*\nDisallow: /private*\n"
        with patch.object(html_boards, "_robots_text", return_value=robots):
            self.assertFalse(html_boards._robots_allows("https://www.pracuj.pl/private/job", 3))
            self.assertFalse(html_boards._robots_allows("https://www.pracuj.pl/praca/job?tracking=1", 3))
            self.assertTrue(html_boards._robots_allows("https://www.pracuj.pl/praca/job", 3))

    def test_robots_utf8_bom_is_removed_before_parsing(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://www.pracuj.pl/robots.txt"
        response.read.return_value = "\ufeffUser-agent: *\nDisallow: /private\n".encode("utf-8")
        opener = MagicMock()
        opener.open.return_value = response
        html_boards._robots_text.cache_clear()
        try:
            with patch.object(html_boards.urllib.request, "build_opener", return_value=opener):
                self.assertFalse(html_boards._robots_allows("https://www.pracuj.pl/private/job", 3))
        finally:
            html_boards._robots_text.cache_clear()

    def test_malformed_offer_json_is_not_accepted_as_empty(self):
        with patch.object(html_boards, "_fetch_html", return_value='<script type="application/ld+json">{</script>'):
            with self.assertRaises(sources.SourceError):
                html_boards.fetch("pracuj", keywords=["assistant"], location="", pages=1, timeout=3, options={})

    def test_redirect_link_with_credentials_is_discarded(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office manager","url":"https://user:pass@www.pracuj.pl/praca/secret"}</script>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            with self.assertRaisesRegex(sources.SourceError, "URL|adres|ofert"):
                html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})

    def test_pages_follow_only_explicit_next_links(self):
        first = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office manager","url":"https://www.pracuj.pl/praca/office-manager,oferta,1"}</script><a rel="next" href="/praca?page=2">Next</a>'''
        second = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office assistant","url":"https://www.pracuj.pl/praca/office-assistant,oferta,2"}</script>'''
        with patch.object(html_boards, "_fetch_html", side_effect=[first, second]) as mock_fetch:
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=2, timeout=3, options={})
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(len(batch.offers), 2)
        self.assertTrue(mock_fetch.call_args_list[1].args[0].endswith("/praca?page=2"))

    def test_pracuj_uses_public_page_number_parameter(self):
        first = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office manager","url":"https://www.pracuj.pl/praca/office-manager,oferta,1"}</script>'''
        second = '''<script type="application/ld+json">{"@type":"JobPosting","title":"Office assistant","url":"https://www.pracuj.pl/praca/office-assistant,oferta,2"}</script>'''
        with patch.object(html_boards, "_fetch_html", side_effect=[first, second]) as mock_fetch:
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=2, timeout=3, options={})
        self.assertEqual(len(batch.offers), 2)
        second_url = urlsplit(mock_fetch.call_args_list[1].args[0])
        self.assertEqual(parse_qs(second_url.query)["pn"], ["2"])

    def test_pagination_stops_on_repeated_page_id_set_and_warns(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"same-id","title":"Office manager","url":"https://www.pracuj.pl/praca/office-manager,oferta,same-id"}</script><a rel="next" href="/praca?page=2">Next</a>'''
        with patch.object(html_boards, "_fetch_html", side_effect=[page, page]) as mock_fetch:
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=5, timeout=3, options={})
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(len(batch.offers), 1)
        self.assertIn("paginacja zatrzymana", (batch.error or "").casefold())

    def test_pagination_stops_on_confirmed_empty_page(self):
        first = '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"job-1","title":"Office manager","url":"https://www.pracuj.pl/praca/office-manager,oferta,job-1"}</script><a rel="next" href="/praca?page=2">Next</a>'''
        second = "<p>Nie znaleźliśmy ofert pracy.</p><a rel=\"next\" href=\"/praca?page=3\">Next</a>"
        with patch.object(html_boards, "_fetch_html", side_effect=[first, second]) as mock_fetch:
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=5, timeout=3, options={})
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(len(batch.offers), 1)

    def test_pagination_limit_is_reported(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"job-1","title":"Office manager","url":"https://www.pracuj.pl/praca/office-manager,oferta,job-1"}</script><a rel="next" href="/praca?page=2">Next</a>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["office"], location="", pages=1, timeout=3, options={})
        self.assertIn("limit", (batch.error or "").casefold())

    def test_location_warning_survives_multiple_pagination_warnings(self):
        page = '''<script type="application/ld+json">{"@type":"JobPosting","identifier":"job-1","title":"Office manager","url":"https://www.pracuj.pl/praca/office-manager,oferta,job-1"}</script>'''
        with patch.object(html_boards, "_fetch_html", return_value=page):
            batch = html_boards.fetch("pracuj", keywords=["one", "two", "three", "four", "five"], location="Warszawa", pages=1, timeout=3, options={})
        self.assertIn("lokalizacja nie jest dostępna", (batch.error or "").casefold())


if __name__ == "__main__":
    unittest.main()
