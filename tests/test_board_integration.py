import unittest
from unittest.mock import patch
from jobsearch import core, sources

class BoardIntegrationTests(unittest.TestCase):
    def config(self):
        return {
            "search": {"country": "pl", "location": "", "keywords": ["operator"], "exclude_keywords": []},
            "collection": {"request_timeout": 20, "max_pages": 2, "history_days": 30},
            "sources": {"justjoinit": {"enabled": True}, "solidjobs": {"enabled": True, "divisions": ["Logistics"]}},
            "email": {"enabled": False},
        }

    @patch("jobsearch.dynamic_boards.fetch")
    def test_selected_boards_are_called_filtered_and_partial_failure_reported(self, fetch):
        row = {"id": "justjoinit:board:1", "title": "Operator", "company": "Test", "location": "", "url": "https://example.org/job", "source": "justjoinit", "_match_text": "operator"}
        def fetch_board(name, **kwargs):
            if name == "solidjobs":
                raise sources.SourceError("niedostępne")
            return sources.SourceBatch(name, [row])
        fetch.side_effect = fetch_board
        offers, statuses = core.collect(self.config(), env={})
        self.assertEqual([offer["source"] for offer in offers], ["justjoinit"])
        self.assertEqual({x["source"]: x["status"] for x in statuses}, {"justjoinit": "ok", "solidjobs": "error"})
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args.kwargs["options"]["divisions"], ["Logistics"])
        self.assertNotIn("_match_text", offers[0])


    @patch("jobsearch.html_boards.fetch")
    def test_html_source_is_selected_and_warning_reaches_summary(self, fetch):
        config = self.config()
        config["sources"] = {"theprotocol": {"enabled": True}}
        row = {"id": "theprotocol:1", "title": "Operator", "company": "Test", "location": "", "url": "https://theprotocol.it/praca/a,oferta,1", "source": "theprotocol", "_match_text": "operator"}
        fetch.return_value = sources.SourceBatch("theprotocol", [row], "brak lokalizacji")
        offers, statuses = core.collect(config, env={})
        self.assertEqual(len(offers), 1)
        self.assertEqual(statuses[0]["status"], "warning")
        self.assertIn("brak lokalizacji", statuses[0]["message"])
        self.assertNotIn("_match_text", offers[0])


    @patch("jobsearch.nfj_bulldog.fetch")
    def test_nofluffjobs_dispatches_to_automatic_adapter(self, fetch):
        config = self.config()
        config["sources"] = {"nofluffjobs": {"enabled": True}}
        row = {"id": "nofluffjobs:1", "title": "Operator", "company": "Test", "location": "", "url": "https://nofluffjobs.com/pl/job/example", "source": "nofluffjobs", "_match_text": "operator"}
        fetch.return_value = sources.SourceBatch("nofluffjobs", [row])
        offers, statuses = core.collect(config, env={})
        self.assertEqual(len(offers), 1)
        self.assertEqual(statuses[0]["status"], "ok")
        self.assertEqual(fetch.call_args.args[0], "nofluffjobs")


    @patch("jobsearch.jobsora_eldorado.fetch")
    def test_jobsora_and_eldorado_dispatch_to_automatic_adapter(self, fetch):
        config = self.config()
        config["sources"] = {"jobsora": {"enabled": True}, "czyjesteldorado": {"enabled": True}}
        def board(name, **kwargs):
            row = {"id": name + ":1", "title": "Operator", "company": "Demo", "location": "", "url": "https://example.org/job", "source": name, "_match_text": "operator"}
            return sources.SourceBatch(name, [row])
        fetch.side_effect = board
        offers, statuses = core.collect(config, env={})
        self.assertEqual({row["source"] for row in offers}, {"jobsora", "czyjesteldorado"})
        self.assertEqual({x["status"] for x in statuses}, {"ok"})
        self.assertEqual(fetch.call_count, 2)


    @patch("jobsearch.nfj_bulldog.fetch")
    def test_bulldogjob_dispatches_to_automatic_adapter(self, fetch):
        config = self.config()
        config["sources"] = {"bulldogjob": {"enabled": True}}
        row = {"id": "bulldogjob:1", "title": "Operator", "company": "Demo", "location": "", "url": "https://bulldogjob.com/companies/jobs/1", "source": "bulldogjob", "_match_text": "operator"}
        fetch.return_value = sources.SourceBatch("bulldogjob", [row])
        offers, statuses = core.collect(config, env={})
        self.assertEqual(len(offers), 1)
        self.assertEqual(statuses[0]["status"], "ok")
        self.assertEqual(fetch.call_args.args[0], "bulldogjob")


    @patch("jobsearch.nfj_bulldog.fetch")
    def test_location_filter_matches_polish_city_with_and_without_diacritics(self, fetch):
        config = self.config()
        config["search"]["location"] = "Kraków"
        config["sources"] = {"bulldogjob": {"enabled": True}}
        row = {"id": "bulldogjob:1", "title": "Operator", "company": "Demo", "location": "Krakow", "url": "https://bulldogjob.com/companies/jobs/1", "source": "bulldogjob", "_match_text": "operator"}
        fetch.return_value = sources.SourceBatch("bulldogjob", [row])
        offers, statuses = core.collect(config, env={})
        self.assertEqual(len(offers), 1)
        self.assertEqual(statuses[0]["status"], "ok")

if __name__ == "__main__":
    unittest.main()
