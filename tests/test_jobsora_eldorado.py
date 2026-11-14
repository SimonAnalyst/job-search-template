import json
import urllib.error
import unittest
from email.message import Message
from unittest.mock import patch

from jobsearch import jobsora_eldorado, sources


JOBSORA_HTML = '''
<html><body>
  <article class="c-job-card" data-id="fixture-17" data-href="/wakat-17">
    <h2 class="c-job-title"><a href="/wakat-17">Operator</a></h2>
    <div class="c-job-company"><span class="c-job-company__name">Demo Logistics</span></div>
    <div class="c-job-location"><span class="c-job-location__city">Warszawa</span></div>
    <div class="description">Treść prywatna, której nie należy kopiować.</div>
  </article>
  <a rel="next" href="?start=20">Dalej</a>
</body></html>
'''


class _Response:
    def __init__(self, payload, *, status=200, session_id=None, content_type="application/json"):
        self.status = status
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id

    def read(self, limit=-1):
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _rpc_result(result_id, result, *, session_id="session-fixture"):
    return _Response({"jsonrpc": "2.0", "id": result_id, "result": result}, session_id=session_id)


def _job(title="Operator", url="https://czyjesteldorado.pl/praca/470999-old-slug?ctx=private-query"):
    return {
        "title": title,
        "company": "Demo Logistics",
        "cities": ["Warszawa"],
        "keywords": ["operator"],
        "url": url,
        "description": "Nie zapisuj prywatnej treści.",
    }


def _mcp_responses(tool_result, *, tool_id=2):
    initialize = _rpc_result(1, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fixture"}})
    initialized = _Response(b"", status=202)
    call = _rpc_result(tool_id, {"structuredContent": tool_result, "content": []})
    return [initialize, initialized, call]


class JobsoraEldoradoTests(unittest.TestCase):
    def test_unknown_source_is_rejected(self):
        with self.assertRaises(sources.SourceError):
            jobsora_eldorado.fetch("unknown", keywords=["operator"], location="", pages=1, timeout=2, options={})

    def test_jobsora_search_url_uses_public_path_without_query(self):
        url = jobsora_eldorado._jobsora_search_url("Data Analyst", "Łódź")
        self.assertEqual(url, "https://pl.jobsora.com/praca/%C5%82%C3%B3d%C5%BA/q-data-analyst")
        self.assertEqual(jobsora_eldorado.urllib.parse.urlsplit(url).query, "")

    def test_jobsora_parser_returns_only_allowlisted_fields(self):
        rows, next_url = jobsora_eldorado._parse_jobsora_page(
            JOBSORA_HTML, "https://pl.jobsora.com/praca/q-operator"
        )
        self.assertEqual(len(rows), 1)
        offer = rows[0]
        self.assertEqual(offer["title"], "Operator")
        self.assertEqual(offer["company"], "Demo Logistics")
        self.assertEqual(offer["location"], "Warszawa")
        self.assertEqual(offer["url"], "https://pl.jobsora.com/wakat-17")
        self.assertEqual(set(offer), {"id", "title", "company", "location", "url", "source", "_match_text"})
        self.assertNotIn("Treść prywatna", repr(offer))
        self.assertEqual(next_url, "https://pl.jobsora.com/praca/q-operator?start=20")

    def test_jobsora_search_terms_are_independent_and_partial_errors_are_visible(self):
        with patch.object(jobsora_eldorado, "_fetch_jobsora_html", side_effect=[JOBSORA_HTML, sources.SourceError("odpowiedź HTTP 429")]) as fetch_page:
            batch = jobsora_eldorado.fetch(
                "jobsora", keywords=["operator", "warehouse", "accountant"], location="", pages=1, timeout=2, options={}
            )
        self.assertEqual(fetch_page.call_count, 2)
        self.assertEqual(batch.name, "jobsora")
        self.assertEqual(len(batch.offers), 1)
        self.assertIn("hasło 2", batch.error)
        self.assertIn("dalsze hasła pominięte", batch.error)

    def test_jobsora_404_city_path_falls_back_once_and_filters_locally(self):
        local_html = JOBSORA_HTML.replace("Warszawa", "Łódź")
        with patch.object(
            jobsora_eldorado,
            "_fetch_jobsora_html",
            side_effect=[sources.SourceError("odpowiedź HTTP 404"), local_html],
        ) as fetch_page:
            batch = jobsora_eldorado.fetch(
                "jobsora", keywords=["operator"], location="Łódź", pages=1, timeout=2, options={}
            )
        self.assertEqual(fetch_page.call_count, 2)
        self.assertIn("/praca/%C5%82%C3%B3d%C5%BA/q-operator", fetch_page.call_args_list[0].args[0])
        self.assertTrue(fetch_page.call_args_list[1].args[0].endswith("/praca/q-operator"))
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["location"], "Łódź")
        self.assertIn("filtr lokalny", batch.error)
        self.assertIn("wyniki mogą być ucięte", batch.error)

    def test_jobsora_city_fallback_continues_after_page_without_local_match(self):
        page_without_match = JOBSORA_HTML
        page_with_match = JOBSORA_HTML.replace("wakat-17", "wakat-18").replace("Warszawa", "Łódź")
        page_with_match = page_with_match.replace(
            '  <a rel="next" href="?start=20">Dalej</a>', ""
        )
        with patch.object(
            jobsora_eldorado,
            "_fetch_jobsora_html",
            side_effect=[sources.SourceError("odpowiedź HTTP 404"), page_without_match, page_with_match],
        ) as fetch_page:
            batch = jobsora_eldorado.fetch(
                "jobsora", keywords=["operator"], location="Łódź", pages=2, timeout=2, options={}
            )
        self.assertEqual(fetch_page.call_count, 3)
        self.assertTrue(fetch_page.call_args_list[2].args[0].endswith("?start=20"))
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["location"], "Łódź")
        self.assertTrue(batch.offers[0]["url"].endswith("/wakat-18"))

    def test_jobsora_invalid_keyword_slug_does_not_abort_sibling_terms(self):
        accountant_html = JOBSORA_HTML.replace("wakat-17", "wakat-18").replace("Operator", "Accountant")
        with patch.object(jobsora_eldorado, "_fetch_jobsora_html", side_effect=[JOBSORA_HTML, accountant_html]) as fetch_page:
            batch = jobsora_eldorado.fetch(
                "jobsora", keywords=["operator", "+", "accountant"], location="", pages=1, timeout=2, options={}
            )
        self.assertEqual(fetch_page.call_count, 2)
        self.assertEqual({offer["title"] for offer in batch.offers}, {"Operator", "Accountant"})
        self.assertIn("hasło 2", batch.error)

    def test_jobsora_offer_urls_are_restricted_and_ids_ignore_trailing_slash(self):
        page_url = "https://pl.jobsora.com/praca/q-operator"
        with_slash = jobsora_eldorado._jobsora_offer_url("/wakat-17/", page_url)
        without_slash = jobsora_eldorado._jobsora_offer_url("/wakat-17", page_url)
        self.assertEqual(with_slash, without_slash)
        self.assertEqual(jobsora_eldorado._jobsora_offer_id(with_slash), "17")
        self.assertIsNone(jobsora_eldorado._jobsora_offer_url("/praca/470999-old-slug", page_url))
        first, _ = jobsora_eldorado._parse_jobsora_page(
            JOBSORA_HTML,
            "https://pl.jobsora.com/praca/q-operator",
        )
        equivalent_html = JOBSORA_HTML.replace('data-href="/wakat-17"', 'data-href="/wakat-17/"')
        second, _ = jobsora_eldorado._parse_jobsora_page(
            equivalent_html,
            "https://pl.jobsora.com/praca/q-operator",
        )
        self.assertEqual(first[0]["id"], second[0]["id"])

    def test_jobsora_captcha_word_in_valid_title_and_script_is_not_a_challenge(self):
        html = JOBSORA_HTML.replace("Operator", "Captcha Engineer") + "<script>captcha challenge</script>"
        rows, _ = jobsora_eldorado._parse_jobsora_page(html, "https://pl.jobsora.com/praca/q-operator")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Captcha Engineer")

    def test_jobsora_visible_challenge_without_offer_cards_is_reported(self):
        with self.assertRaisesRegex(sources.SourceError, "blokadę lub weryfikację"):
            jobsora_eldorado._parse_jobsora_page(
                "<html><body><p>Verify you are human</p></body></html>",
                "https://pl.jobsora.com/praca/q-operator",
            )

    def test_jobsora_robots_disallow_prevents_listing_request(self):
        with patch.object(jobsora_eldorado, "_jobsora_robots_allows", return_value=False), patch.object(
            jobsora_eldorado.urllib.request, "build_opener"
        ) as build_opener:
            with self.assertRaisesRegex(sources.SourceError, "robots.txt"):
                jobsora_eldorado._fetch_jobsora_html("https://pl.jobsora.com/praca/q-operator", timeout=2)
        build_opener.assert_not_called()

    def test_jobsora_reports_unconfirmed_pagination_when_more_pages_are_requested(self):
        html_without_next = JOBSORA_HTML.replace('  <a rel="next" href="?start=20">Dalej</a>', "")
        with patch.object(jobsora_eldorado, "_fetch_jobsora_html", return_value=html_without_next):
            batch = jobsora_eldorado.fetch(
                "jobsora", keywords=["operator"], location="", pages=2, timeout=2, options={}
            )
        self.assertEqual(len(batch.offers), 1)
        self.assertIn("kolejnej strony", batch.error)

    def test_jobsora_next_page_stops_when_robots_disallow_it(self):
        with patch.object(jobsora_eldorado, "_fetch_jobsora_html", side_effect=[JOBSORA_HTML, sources.SourceError("robots.txt zabrania pobrania tej ścieżki")]) as fetch_page:
            batch = jobsora_eldorado.fetch(
                "jobsora", keywords=["operator"], location="", pages=2, timeout=2, options={}
            )
        self.assertEqual(len(batch.offers), 1)
        self.assertIsNotNone(batch.error)
        self.assertIn("robots", batch.error.casefold())
        self.assertGreaterEqual(fetch_page.call_count, 1)

    def test_eldorado_mcp_maps_only_public_fields_and_warns_when_results_are_truncated(self):
        opener = _Opener(_mcp_responses({"jobs": [_job()], "shown_results": 1, "total_results": 120}))
        with patch.object(jobsora_eldorado.urllib.request, "build_opener", return_value=opener):
            batch = jobsora_eldorado.fetch(
                "czyjesteldorado", keywords=["operator"], location="", pages=3, timeout=2, options={}
            )
        self.assertEqual(len(batch.offers), 1)
        offer = batch.offers[0]
        self.assertEqual(offer["source"], "czyjesteldorado")
        self.assertEqual(offer["url"], "https://czyjesteldorado.pl/praca/470999-old-slug")
        self.assertEqual(offer["location"], "Warszawa")
        self.assertEqual(set(offer), {"id", "title", "company", "location", "url", "source", "_match_text"})
        self.assertNotIn("Treść prywatna", repr(offer))
        self.assertIn("120", batch.error)
        self.assertEqual(len(opener.calls), 3)
        search_request = opener.calls[2][0]
        payload = json.loads(search_request.data)
        self.assertEqual(payload["method"], "tools/call")
        self.assertEqual(payload["params"]["arguments"], {"phrase": "operator"})

    def test_eldorado_multiple_terms_are_isolated(self):
        initialize = _rpc_result(1, {"protocolVersion": "2024-11-05"})
        initialized = _Response(b"", status=202)
        failed = _Response({"jsonrpc": "2.0", "id": 2, "error": {"code": -32603, "message": "upstream failed"}})
        success = _rpc_result(3, {"structuredContent": {"jobs": [_job()], "shown_results": 1, "total_results": 1}, "content": []})
        opener = _Opener([initialize, initialized, failed, success])
        with patch.object(jobsora_eldorado.urllib.request, "build_opener", return_value=opener):
            batch = jobsora_eldorado.fetch(
                "czyjesteldorado", keywords=["first", "operator"], location="", pages=1, timeout=2, options={}
            )
        self.assertEqual(len(batch.offers), 1)
        self.assertIn("hasło 1", batch.error)
        self.assertNotIn("first", batch.error)
        self.assertEqual(len(opener.calls), 4)

    def test_eldorado_stops_remaining_terms_after_http_429_and_keeps_prior_results(self):
        initialize = _rpc_result(1, {"protocolVersion": "2024-11-05"})
        initialized = _Response(b"", status=202)
        successful_search = _rpc_result(
            2,
            {"structuredContent": {"jobs": [_job()], "shown_results": 1, "total_results": 1}, "content": []},
        )
        blocked = urllib.error.HTTPError("https://czyjesteldorado.pl/_mcp", 429, "Too Many Requests", Message(), None)
        opener = _Opener([initialize, initialized, successful_search, blocked])
        with patch.object(jobsora_eldorado.urllib.request, "build_opener", return_value=opener):
            batch = jobsora_eldorado.fetch(
                "czyjesteldorado", keywords=["operator", "warehouse", "accountant"],
                location="", pages=1, timeout=2, options={},
            )
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(len(opener.calls), 4)
        self.assertIn("HTTP 429", batch.error)
        self.assertIn("dalsze hasła pominięte", batch.error)

    def test_eldorado_stable_id_comes_from_numeric_path_id(self):
        old = "https://czyjesteldorado.pl/praca/470999-old-slug"
        new = "https://czyjesteldorado.pl/praca/470999-new-slug/"
        self.assertEqual(jobsora_eldorado._eldorado_offer_id(old), "470999")
        self.assertEqual(jobsora_eldorado._eldorado_offer_id(new), "470999")
        self.assertEqual(
            jobsora_eldorado._safe_eldorado_url(new),
            "https://czyjesteldorado.pl/praca/470999-new-slug",
        )
        old_rows, _ = jobsora_eldorado._eldorado_rows([_job(url=old)])
        new_rows, _ = jobsora_eldorado._eldorado_rows([_job(url=new)])
        self.assertEqual(list(old_rows), list(new_rows))

    def test_eldorado_sse_skips_progress_event_and_reads_multiline_response(self):
        initialize = _rpc_result(1, {"protocolVersion": "2024-11-05"})
        initialized = _Response(b"", status=202)
        result = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"structuredContent": {"jobs": [_job()], "shown_results": 1, "total_results": 1}, "content": []},
        }
        progress = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
        response_lines = json.dumps(result, indent=2).splitlines()
        sse = "event: progress\ndata: " + progress + "\n\n"
        sse += "event: message\n" + "\n".join("data: " + line for line in response_lines) + "\n\n"
        opener = _Opener([initialize, initialized, _Response(sse.encode("utf-8"), content_type="text/event-stream")])
        with patch.object(jobsora_eldorado.urllib.request, "build_opener", return_value=opener):
            batch = jobsora_eldorado.fetch(
                "czyjesteldorado", keywords=["operator"], location="", pages=1, timeout=2, options={}
            )
        self.assertEqual(len(batch.offers), 1)
        self.assertEqual(batch.offers[0]["id"], "czyjesteldorado:public:470999")

    def test_eldorado_rejects_external_offer_urls(self):
        opener = _Opener(_mcp_responses({"jobs": [_job(url="https://attacker.example/praca/fixture")], "shown_results": 1, "total_results": 1}))
        with patch.object(jobsora_eldorado.urllib.request, "build_opener", return_value=opener):
            batch = jobsora_eldorado.fetch(
                "czyjesteldorado", keywords=["operator"], location="", pages=1, timeout=2, options={}
            )
        self.assertEqual(batch.offers, [])
        self.assertIsNotNone(batch.error)
        self.assertIn("pominięto", batch.error)


if __name__ == "__main__":
    unittest.main()
