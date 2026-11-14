import unittest
from urllib.error import HTTPError
from urllib.request import Request
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from jobsearch import dynamic_boards, sources


class DynamicBoardTests(unittest.TestCase):
    def test_solidjobs_uses_documented_contract_and_keeps_description_only_in_match_text(self):
        payloads = [
            {"pageIndex": 0, "totalPages": 2, "jobs": [{"jobOfferKey": "it-1", "title": "Associate", "company": "Firma A", "locations": ["Kraków", "Warszawa"], "url": "https://solid.jobs/offer/it-1", "description": "Warehouse inventory work", "skills": [{"name": "inventory"}]}]},
            {"pageIndex": 1, "totalPages": 2, "jobs": [{"jobOfferKey": "it-2", "title": "Dispatcher", "company": "Firma B", "locations": ["Kraków"], "url": "https://solid.jobs/offer/it-2"}]},
            {"pageIndex": 0, "totalPages": 0, "jobs": []},
            {"pageIndex": 0, "totalPages": 1, "jobs": [{"jobOfferKey": "sales-1", "title": "Customer Advisor", "company": "Firma C", "locations": ["Kraków"], "url": "https://solid.jobs/offer/sales-1", "description": "Warehouse supplier support"}]},
            {"pageIndex": 0, "totalPages": 0, "jobs": []},
        ]
        with patch.object(dynamic_boards, "_json_request", side_effect=payloads) as request:
            result = dynamic_boards.fetch("solidjobs", keywords=["warehouse", "dispatcher"], location="Kraków", pages=2, timeout=2,
                                          options={"divisions": ["IT", "Sales"], "campaign": "job-search-template"})
        self.assertEqual(request.call_count, 5)
        self.assertEqual({item["id"] for item in result.offers}, {"solidjobs:IT:it-1", "solidjobs:IT:it-2", "solidjobs:Sales:sales-1"})
        self.assertEqual(result.offers[0]["location"], "Kraków, Warszawa")
        self.assertIn("warehouse inventory work", result.offers[0]["_match_text"])
        self.assertIn("inventory", result.offers[0]["_match_text"])
        self.assertNotIn("description", result.offers[0])
        self.assertNotIn("skills", result.offers[0])
        self.assertEqual(result.name, "solidjobs")
        queries = [parse_qs(urlsplit(call.args[0]).query) for call in request.call_args_list]
        self.assertEqual([q["search.searchTerm"][0] for q in queries], ["warehouse", "warehouse", "dispatcher", "warehouse", "dispatcher"])
        self.assertTrue(all(q["search.cities"] == ["Kraków"] for q in queries))
        self.assertTrue(all("city" not in q for q in queries))
        self.assertEqual([q["pageIndex"][0] for q in queries], ["0", "1", "0", "0", "0"])

    def test_solidjobs_reports_page_limit_and_invalid_metadata(self):
        first_page = {"pageIndex": 0, "totalPages": 2, "jobs": [{"jobOfferKey": "first", "title": "Operator", "url": "https://solid.jobs/offer/first"}]}
        with patch.object(dynamic_boards, "_json_request", return_value=first_page):
            result = dynamic_boards.fetch("solidjobs", keywords=["operator"], location="", pages=1, timeout=2,
                                          options={"divisions": ["Logistics"]})
        self.assertIsNotNone(result.error)
        self.assertIn("limit", result.error.casefold())
        with patch.object(dynamic_boards, "_json_request", return_value={"pageIndex": 0, "jobs": []}):
            with self.assertRaises(sources.SourceError):
                dynamic_boards.fetch("solidjobs", keywords=["operator"], location="", pages=1, timeout=2,
                                      options={"divisions": ["Logistics"]})

    def test_justjoinit_uses_public_listing_and_embedded_offer_fields(self):
        page_html = r'<script>{"body":"Warehouse operator","companyName":"Firma A","slug":"role-1","city":"Gdynia","description":"Warehouse stock checks","skills":[{"name":"inventory"}]} {"body":"Dispatcher","city":"Lodz","companyName":"Firma B","slug":"role-2","description":"Traffic planning"}</script>'
        with patch.object(dynamic_boards, "_text_request", side_effect=["User-agent: *\nAllow: /\n", page_html]) as request:
            result = dynamic_boards.fetch("justjoinit", keywords=["warehouse"], location="", pages=1, timeout=2,
                                          options={"listing_url": "https://justjoin.it/job-offers/all-locations"})
        self.assertEqual(request.call_count, 2)
        self.assertTrue(request.call_args_list[0].args[0].endswith("/robots.txt"))
        listing_url = request.call_args_list[1].args[0]
        self.assertEqual(parse_qs(urlsplit(listing_url).query)["keyword"], ["warehouse"])
        self.assertEqual(len(result.offers), 2)
        self.assertEqual(result.offers[0]["source"], "justjoinit")
        self.assertEqual(result.offers[0]["title"], "Warehouse operator")
        self.assertEqual(result.offers[0]["location"], "Gdynia")
        self.assertIn("warehouse stock checks", result.offers[0]["_match_text"])
        self.assertIn("inventory", result.offers[0]["_match_text"])
        self.assertEqual(result.offers[0]["url"], "https://justjoin.it/job-offer/role-1")
        self.assertNotIn("description", result.offers[0])

    def test_html_sources_query_each_alternative_keyword_separately(self):
        page_html = r'<script>{"body":"Warehouse driver","companyName":"Firma","slug":"role-1","city":"Gdynia"}</script>'
        cases = [
            ("justjoinit", "https://justjoin.it/job-offers/all-locations", "keyword"),
            ("rocketjobs", "https://rocketjobs.pl/oferty-pracy/wszystkie-lokalizacje", "keyword"),
        ]
        for name, listing_url, parameter in cases:
            with self.subTest(source=name):
                with patch.object(dynamic_boards, "_text_request", side_effect=["User-agent: *\nAllow: /\n", page_html, page_html]) as request:
                    result = dynamic_boards.fetch(name, keywords=["warehouse", "driver"], location="", pages=3, timeout=2,
                                         options={"listing_url": listing_url})
                self.assertEqual(request.call_count, 3)
                search_urls = [call.args[0] for call in request.call_args_list[1:]]
                terms = [parse_qs(urlsplit(url).query)[parameter] for url in search_urls]
                self.assertEqual(terms, [["warehouse"], ["driver"]])
                if name == "rocketjobs":
                    self.assertTrue(result.error)
                    self.assertIn("pagin", result.error.casefold())
                    self.assertTrue(all("page" not in parse_qs(urlsplit(url).query) for url in search_urls))

    def test_html_sources_report_local_location_filter_without_city_query(self):
        page_html = r'<script>{"body":"Warehouse operator","companyName":"Firma","slug":"role-1","city":"Gdynia"}</script>'
        for name, listing_url in [
            ("justjoinit", "https://justjoin.it/job-offers/all-locations"),
            ("rocketjobs", "https://rocketjobs.pl/oferty-pracy/wszystkie-lokalizacje"),
        ]:
            with self.subTest(source=name):
                with patch.object(dynamic_boards, "_text_request", side_effect=["User-agent: *\nAllow: /\n", page_html]) as request:
                    result = dynamic_boards.fetch(name, keywords=["operator"], location="Gdynia", pages=1, timeout=2,
                                                  options={"listing_url": listing_url})
                listing_url_called = request.call_args_list[1].args[0]
                self.assertNotIn("city", parse_qs(urlsplit(listing_url_called).query))
                self.assertIsNotNone(result.error)
                self.assertIn("lokalnie", result.error.casefold())

    def test_robots_policy_respects_wildcards_and_listing_urls_stay_on_known_paths(self):
        rules = dynamic_boards._parse_robots("User-agent: *\nDisallow: /oferty-pracy/*,*\nAllow: /oferty-pracy/wszystkie-lokalizacje\n")
        self.assertFalse(dynamic_boards._robots_can_fetch(rules, "https://rocketjobs.pl/oferty-pracy/bi-data,remote"))
        self.assertTrue(dynamic_boards._robots_can_fetch(rules, "https://rocketjobs.pl/oferty-pracy/wszystkie-lokalizacje"))
        with self.assertRaises(sources.SourceError):
            dynamic_boards.fetch("rocketjobs", keywords=["operator"], location="", pages=1, timeout=2,
                                 options={"listing_url": "https://rocketjobs.pl/oferty-pracy/custom"})

    def test_robots_comments_and_blank_lines_do_not_end_a_group(self):
        rules = dynamic_boards._parse_robots(
            "User-agent: *\n\n# komentarz\nDisallow: /private\n"
        )
        self.assertFalse(dynamic_boards._robots_can_fetch(rules, "https://rocketjobs.pl/private"))
        self.assertTrue(dynamic_boards._robots_can_fetch(rules, "https://rocketjobs.pl/public"))
        grouped = dynamic_boards._parse_robots(
            "User-agent: BotA\nDisallow: /\n\nUser-agent: BotB\nAllow: /public\n"
        )
        self.assertEqual(grouped, [
            (["bota"], [("disallow", "/")]),
            (["botb"], [("allow", "/public")]),
        ])

    def test_html_provider_parses_jsonld_and_rejects_unknown_markup(self):
        html = '<html><script type="application/ld+json">{"@type":"JobPosting","title":"Operator","hiringOrganization":{"name":"Firma"},"jobLocation":{"address":{"addressLocality":"Gdynia"}},"url":"https://rocketjobs.pl/oferta/1"}</script></html>'
        options = {"listing_url": "https://rocketjobs.pl/oferty-pracy/wszystkie-lokalizacje"}
        with patch.object(dynamic_boards, "_text_request", side_effect=["User-agent: *\nAllow: /\n", html]):
            result = dynamic_boards.fetch("rocketjobs", keywords=["operator"], location="", pages=1, timeout=2,
                                          options=options)
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0]["title"], "Operator")
        embedded = r'<script>{"body":"Warehouse operator","companyName":"Firma","slug":"role-1","city":"Gdynia","description":"warehouse work"}</script>'
        embedded_rows = dynamic_boards._embedded_rows("justjoinit", embedded)
        self.assertEqual(embedded_rows[0]["title"], "Warehouse operator")
        self.assertNotIn("description", embedded_rows[0])
        with patch.object(dynamic_boards, "_text_request", side_effect=["User-agent: *\nAllow: /\n", "<html>changed markup</html>"]):
            with self.assertRaises(sources.SourceError):
                dynamic_boards.fetch("rocketjobs", keywords=["operator"], location="", pages=1, timeout=2,
                                     options=options)

    def test_html_provider_fails_closed_when_robots_rules_deny_listing(self):
        with patch.object(dynamic_boards, "_text_request", return_value="User-agent: *\nDisallow: /oferty-pracy/\n") as request:
            with self.assertRaisesRegex(sources.SourceError, "robots.txt"):
                dynamic_boards.fetch("rocketjobs", keywords=["operator"], location="", pages=1, timeout=2,
                                     options={"listing_url": "https://rocketjobs.pl/oferty-pracy/wszystkie-lokalizacje"})
        self.assertEqual(request.call_count, 1)

    def test_html_fetch_blocks_redirects_without_contacting_redirect_target(self):
        class RedirectingOpener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 302, "Found", {}, None)

        with patch.object(dynamic_boards.urllib.request, "build_opener", return_value=RedirectingOpener()) as build_opener:
            with self.assertRaisesRegex(sources.SourceError, "przekierowanie zablokowane"):
                dynamic_boards._text_request("https://rocketjobs.pl/oferty-pracy/wszystkie-lokalizacje", 2)
        handler = build_opener.call_args.args[0]
        self.assertIsNone(handler.redirect_request(
            Request("https://rocketjobs.pl/"), None, 302, "Found", {}, "https://external.example/"
        ))

    def test_capgemini_candidate_api_is_supported_with_allowlisted_fields_only(self):
        payload = {"total": 1, "data": [{"id": "cg-1", "title": "Logistics coordinator", "companyName": "Capgemini", "location": "Krakow", "url": "https://www.capgemini.com/careers/jobs/cg-1"}]}
        with patch.object(dynamic_boards, "_json_request", return_value=payload) as request:
            result = dynamic_boards.fetch("capgemini", keywords=["logistics"], location="", pages=1, timeout=2,
                                          options={})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(parse_qs(urlsplit(request.call_args.args[0]).query)["page"], ["1"])
        self.assertEqual(len(result.offers), 1)
        self.assertEqual(result.offers[0]["source"], "capgemini")
        self.assertNotIn("description", result.offers[0])

    def test_capgemini_paginates_and_reports_configured_page_limit(self):
        payloads = [
            {"total": 128, "data": [{"id": "cg-1", "title": "Logistics coordinator", "location": "Krakow", "url": "https://www.capgemini.com/jobs/cg-1"}]},
            {"total": 128, "data": [{"id": "cg-2", "title": "Warehouse planner", "location": "Lodz", "url": "https://www.capgemini.com/jobs/cg-2"}]},
        ]
        with patch.object(dynamic_boards, "_json_request", side_effect=payloads) as request:
            result = dynamic_boards.fetch("capgemini", keywords=["logistics"], location="", pages=2, timeout=2,
                                          options={})
        self.assertEqual(request.call_count, 2)
        page_numbers = [parse_qs(urlsplit(call.args[0]).query)["page"][0] for call in request.call_args_list]
        self.assertEqual(page_numbers, ["1", "2"])
        self.assertEqual({offer["id"] for offer in result.offers}, {"capgemini:pl-pl:cg-1", "capgemini:pl-pl:cg-2"})
        self.assertIsNotNone(result.error)
        self.assertIn("limit", result.error.casefold())

    def test_unsupported_provider_is_rejected(self):
        with self.assertRaises(sources.SourceError):
            dynamic_boards.fetch("not-a-provider", keywords=["x"], location="", pages=1, timeout=2, options={})


if __name__ == "__main__":
    unittest.main()
