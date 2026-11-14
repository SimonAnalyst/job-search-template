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

if __name__ == "__main__":
    unittest.main()
