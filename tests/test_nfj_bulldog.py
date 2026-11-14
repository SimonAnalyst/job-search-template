import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from jobsearch import dynamic_boards, nfj_bulldog, sources


ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"


def nofluff_page(*, identifier="nf-1", title="Data Analyst 80 PLN / h", url=None):
    url = url or f"https://nofluffjobs.com/pl/job/{identifier}"
    payload = {
        "@type": "JobPosting",
        "identifier": {"value": identifier},
        "title": title,
        "hiringOrganization": {"name": "Example Company"},
        "jobLocation": {"address": {"addressLocality": "Warszawa"}},
        "url": url,
        "description": "Python reporting and data quality work",
    }
    return '<script type="application/ld+json">' + json.dumps(payload) + "</script>"


def nofluff_state_page(*postings):
    payload = {"search": {"postings": list(postings), "totalCount": len(postings)}}
    return '<script id="serverApp-state" type="application/json">' + json.dumps(payload) + "</script>"

def bulldog_page(*jobs, skills_filter=None):
    page_props = {"jobs": list(jobs)}
    if skills_filter is not None:
        page_props["slugState"] = {"filters": {"skills": skills_filter}}
    payload = {"props": {"pageProps": page_props}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"


class NoFluffBulldogTests(unittest.TestCase):
    def test_nofluff_state_queries_each_keyword_and_populates_company_location_and_skills(self):
        page = nofluff_state_page({
            "id": "private-id", "reference": "public-ref", "name": "Example Company",
            "title": "Data Analyst 80 PLN / h", "url": "data-analyst-slug",
            "location": {"places": [{"city": "Warszawa", "url": "/pl/city/warszawa"}], "fullyRemote": False},
            "technology": ["Python", "WidgetPro"], "category": "Data",
            "salary": {"from": 80, "currency": "PLN"},
        })
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, page, page]) as request:
            result = nfj_bulldog.fetch(
                "nofluffjobs", keywords=["data", "analyst"], location="Warszawa",
                pages=1, timeout=5, options={},
            )

        self.assertEqual(request.call_count, 3)
        self.assertTrue(request.call_args_list[0].args[0].endswith("/robots.txt"))
        urls = [call.args[0] for call in request.call_args_list[1:]]
        self.assertEqual([parse_qs(urlsplit(url).query)["criteria"] for url in urls], [["data"], ["analyst"]])
        self.assertEqual(result.name, "nofluffjobs")
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer["title"], "Data Analyst [wynagrodzenie pominięte]")
        self.assertEqual(offer["company"], "Example Company")
        self.assertEqual(offer["location"], "Warszawa")
        self.assertEqual(offer["url"], "https://nofluffjobs.com/pl/job/data-analyst-slug")
        self.assertEqual(set(offer), {"id", "title", "company", "location", "url", "source", "_match_text"})
        self.assertNotIn("salary", str(offer).casefold())
        self.assertNotIn("80 PLN", str(offer))
        self.assertNotIn("description", offer)
        self.assertIn("python", offer["_match_text"])
        self.assertIn("widgetpro", offer["_match_text"])
        self.assertIn("lokalnie", (result.error or "").casefold())

    def test_bulldog_reads_next_data_and_builds_same_host_offer_url(self):
        page = bulldog_page({
            "id": "123-job-22",
            "position": "Platform Engineer",
            "company": {"name": "Example Works"},
            "city": "Kraków",
            "technologyTags": ["Python", "Linux"],
            "denominatedSalaryLong": "120 PLN per hour",
        })
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, page]) as request:
            result = nfj_bulldog.fetch(
                "bulldogjob", keywords=["Python"], location="", pages=1, timeout=5, options={},
            )

        self.assertEqual(request.call_count, 2)
        self.assertIn("/companies/jobs/s/skills,Python", request.call_args_list[1].args[0])
        self.assertEqual(result.name, "bulldogjob")
        self.assertEqual(len(result.offers), 1)
        offer = result.offers[0]
        self.assertEqual(offer["url"], "https://bulldogjob.com/companies/jobs/123-job-22")
        self.assertEqual(offer["company"], "Example Works")
        self.assertEqual(offer["location"], "Kraków")
        self.assertIn("python", offer["_match_text"])
        self.assertEqual(set(offer), {"id", "title", "company", "location", "url", "source", "_match_text"})
        self.assertNotIn("salary", str(offer).casefold())

    def test_bulldog_adds_keyword_for_exact_active_skill_filter(self):
        page = bulldog_page({
            "id": "456-filtered", "position": "Platform Engineer",
            "company": {"name": "Example Works"}, "city": "Warsaw",
            "technologyTags": ["Advanced tooling"],
        }, skills_filter=[{"value": " Python ", "label": "Python"}])
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, page]):
            result = nfj_bulldog.fetch(
                "bulldogjob", keywords=["python"], location="", pages=1, timeout=5, options={},
            )
        self.assertEqual(len(result.offers), 1)
        self.assertIn("python", result.offers[0]["_match_text"])

    def test_bulldog_does_not_add_keyword_for_mismatched_or_missing_skill_filter(self):
        job = {
            "id": "789-java", "position": "Platform Engineer",
            "company": {"name": "Example Works"}, "city": "Warsaw",
            "technologyTags": ["Java"],
        }
        for skills_filter in ([{"value": "java", "label": "Java"}], None):
            with self.subTest(skills_filter=skills_filter):
                page = bulldog_page(job, skills_filter=skills_filter)
                with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, page]):
                    result = nfj_bulldog.fetch(
                        "bulldogjob", keywords=["python"], location="", pages=1, timeout=5, options={},
                    )
                self.assertEqual(len(result.offers), 1)
                self.assertNotIn("python", result.offers[0]["_match_text"])
    def test_recommendation_jobs_do_not_receive_verified_skill_keyword_bonus(self):
        payload = {"props": {"pageProps": {
            "recommendations": {"jobs": [{
                "id": "987-recommended", "position": "Platform Engineer",
                "company": {"name": "Example Works"}, "city": "Warsaw",
                "technologyTags": ["Advanced tooling"],
            }]},
            "slugState": {"filters": {"skills": [{"value": "python", "label": "Python"}]}},
        }}}
        page = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, page]):
            result = nfj_bulldog.fetch(
                "bulldogjob", keywords=["python"], location="", pages=1, timeout=5, options={},
            )
        self.assertEqual(len(result.offers), 1)
        self.assertNotIn("python", result.offers[0]["_match_text"])
    def test_partial_keyword_failure_keeps_successful_results_and_reports_429(self):
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, nofluff_page(), sources.SourceError("odpowiedź HTTP 429")]) as request:
            result = nfj_bulldog.fetch(
                "nofluffjobs", keywords=["analyst", "engineer", "tester"], location="", pages=1, timeout=5, options={},
            )

        self.assertEqual(len(result.offers), 1)
        self.assertIn("429", result.error or "")
        query_urls = [call.args[0] for call in request.call_args_list[1:]]
        self.assertEqual([parse_qs(urlsplit(url).query)["criteria"][0] for url in query_urls], ["analyst", "engineer"])

    def test_known_empty_states_do_not_fall_back_to_unrelated_anchors(self):
        nofluff_html = nofluff_state_page() + '<a href="https://nofluffjobs.com/pl/job/unrelated"><h3>Unrelated role</h3></a>'
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, nofluff_html]):
            nofluff_result = nfj_bulldog.fetch(
                "nofluffjobs", keywords=["missing-role"], location="", pages=1, timeout=5, options={},
            )
        self.assertEqual(nofluff_result.offers, [])
        self.assertIsNone(nofluff_result.error)

        bulldog_html = bulldog_page() + (
            '<a href="https://bulldogjob.com/companies/jobs/123-unrelated"><h2>Unrelated role</h2></a>'
            '<a href="https://bulldogjob.com/companies/jobs/s/skills,Python"><h2>Filter link</h2></a>'
        )
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, bulldog_html]):
            bulldog_result = nfj_bulldog.fetch(
                "bulldogjob", keywords=["missing-role"], location="", pages=1, timeout=5, options={},
            )
        self.assertEqual(bulldog_result.offers, [])
        self.assertIsNone(bulldog_result.error)

    def test_bulldog_uses_confirmed_path_pagination(self):
        self.assertEqual(
            nfj_bulldog._listing_url("bulldogjob", "python", 2),
            "https://bulldogjob.com/companies/jobs/s/skills,python/page,2",
        )

    def test_missing_location_is_reported_for_local_city_filter(self):
        page = bulldog_page({
            "id": "321-no-city", "position": "Platform Engineer",
            "company": {"name": "Example Works"}, "technologyTags": ["Python"],
        })
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, page]):
            result = nfj_bulldog.fetch(
                "bulldogjob", keywords=["Python"], location="Warszawa", pages=1, timeout=5, options={},
            )
        self.assertEqual(len(result.offers), 1)
        self.assertIn("brak rozpoznanej lokalizacji", result.error or "")
        self.assertIn("lokalnie", (result.error or "").casefold())
    def test_robots_disallow_prevents_listing_request(self):
        with patch.object(nfj_bulldog, "_text_request", return_value="User-agent: *\nDisallow: /pl\n") as request:
            with self.assertRaisesRegex(sources.SourceError, "robots"):
                nfj_bulldog.fetch(
                    "nofluffjobs", keywords=["analyst"], location="", pages=1, timeout=5, options={},
                )
        request.assert_called_once()

    def test_robots_http_block_is_preserved_and_not_retried_for_each_keyword(self):
        with patch.object(nfj_bulldog, "_text_request", side_effect=sources.SourceError("odpowiedź HTTP 403")) as request:
            with self.assertRaisesRegex(sources.SourceError, "403"):
                nfj_bulldog.fetch(
                    "nofluffjobs", keywords=["analyst", "engineer", "tester"], location="", pages=1, timeout=5, options={},
                )
        request.assert_called_once()
    def test_human_verification_page_is_reported_without_retry(self):
        challenge = "<html><title>Security check</title><body>Verify you are human</body></html>"
        with patch.object(nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, challenge]) as request:
            with self.assertRaisesRegex(sources.SourceError, "weryfikacji"):
                nfj_bulldog.fetch(
                    "bulldogjob", keywords=["Python"], location="", pages=1, timeout=5, options={},
                )
        self.assertEqual(request.call_count, 2)

    def test_external_or_incomplete_offer_url_is_rejected(self):
        for url in ("https://example.org/job/other", "https://nofluffjobs.com/pl/job/"):
            with self.subTest(url=url), patch.object(
                nfj_bulldog, "_text_request", side_effect=[ROBOTS_ALLOW, nofluff_page(url=url)],
            ):
                with self.assertRaises(sources.SourceError):
                    nfj_bulldog.fetch(
                        "nofluffjobs", keywords=["analyst"], location="", pages=1, timeout=5, options={},
                    )

    def test_bad_source_and_limits_are_rejected_before_network(self):
        with patch.object(nfj_bulldog, "_text_request") as request:
            with self.assertRaises(sources.SourceError):
                nfj_bulldog.fetch("unknown", keywords=["x"], location="", pages=1, timeout=5, options={})
            with self.assertRaises(sources.SourceError):
                nfj_bulldog.fetch("nofluffjobs", keywords=["x"], location="", pages=11, timeout=5, options={})
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()











