from __future__ import annotations
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from jobsearch import core, sources
from jobsearch.dashboard import render_dashboard
from jobsearch.emailer import EmailError, send_new_jobs
from jobsearch.cli import main


def sample_offer(job_id="lever:board:1", title="Data Analyst"):
    stamp = "2026-11-13T08:00:00Z"
    return {"id":job_id,"title":title,"company":"Example","location":"Warsaw",
            "url":"https://jobs.example.test/1","source":"lever",
            "first_seen":stamp,"last_seen":stamp}


def config(email=False):
    return {
        "search":{"country":"pl","location":"","keywords":["analyst","engineer"],
                  "exclude_keywords":[]},
        "collection":{"request_timeout":10,"max_pages":1,"history_days":180},
        "sources":{"lever":{"enabled":True,"boards":["example"]}},
        "email":{"enabled":email,"send_empty":False},
    }


class SourceTests(unittest.TestCase):
    def test_adzuna_queries_each_keyword_and_deduplicates(self):
        calls = []
        def fake(url, timeout):
            keyword = parse_qs(urlsplit(url).query)["what"][0]
            calls.append(keyword)
            ids = {"analyst":[1, 3], "engineer":[2, 3]}[keyword]
            return {"results":[
                {"id":job_id,"title":f"{keyword} role","company":{"display_name":"Example"},
                 "location":{"display_name":"Warsaw"},"redirect_url":f"http://jobs.example.test/{job_id}",
                 "description":"Relevant work","salary_min":12345,"salary_max":67890}
                for job_id in ids
            ]}
        with patch("jobsearch.sources._request_json", side_effect=fake):
            batch = sources.fetch_adzuna(country="pl",keywords=["analyst","engineer"],
                location="",pages=1,timeout=5,app_id="id",app_key="secret")
        self.assertEqual(calls, ["analyst","engineer"])
        self.assertEqual(len(batch.offers), 3)
        self.assertFalse(batch.error)
        self.assertTrue(all("salary_min" not in row and "salary_max" not in row for row in batch.offers))
        self.assertTrue(all("12345" not in json.dumps(row) for row in batch.offers))
        self.assertTrue(all(row["url"].startswith("http://") for row in batch.offers))

    def test_adzuna_partial_keyword_failure_keeps_other_keyword(self):
        def fake(url, timeout):
            keyword = parse_qs(urlsplit(url).query)["what"][0]
            if keyword == "engineer":
                raise sources.SourceError("brak połączenia")
            return {"results":[{"id":1,"title":"Analyst","company":{"display_name":"Example"},
                "location":{"display_name":"Warsaw"},"redirect_url":"https://jobs.example.test/1",
                "description":"analyst"}]}
        with patch("jobsearch.sources._request_json", side_effect=fake):
            batch = sources.fetch_adzuna(country="pl",keywords=["analyst","engineer"],
                location="",pages=1,timeout=5,app_id="id",app_key="secret")
        self.assertEqual(len(batch.offers),1)
        self.assertIn("częściowa awaria",batch.error)
        self.assertIn("hasło 2",batch.error)
        self.assertNotIn("secret",batch.error)

    def test_lever_partial_board_failure_keeps_success_and_supports_eu(self):
        calls=[]
        def fake(url, timeout):
            calls.append(url)
            if "bad" in url:
                raise sources.SourceError("brak połączenia")
            return [{"id":"1","text":"Analyst","categories":{"location":"Warsaw"},
                     "hostedUrl":"https://jobs.example.test/1","descriptionPlain":"analyst"}]
        with patch("jobsearch.sources._request_json", side_effect=fake):
            batch=sources.fetch_lever(boards=["good","bad"],timeout=5,region="eu")
        self.assertEqual(len(batch.offers),1)
        self.assertIn("api.eu.lever.co",calls[0])
        self.assertIn("częściowa awaria",batch.error)

    def test_greenhouse_partial_board_failure_keeps_success(self):
        def fake(url, timeout):
            if "bad" in url:
                raise sources.SourceError("brak połączenia")
            return {"jobs":[{"id":1,"title":"Engineer","location":{"name":"Warsaw"},
                "absolute_url":"https://jobs.example.test/1","content":"engineer"}]}
        with patch("jobsearch.sources._request_json", side_effect=fake):
            batch=sources.fetch_greenhouse(boards=["good","bad"],timeout=5)
        self.assertEqual(len(batch.offers),1)
        self.assertIn("częściowa awaria",batch.error)

    def test_url_requires_secure_scheme_and_drops_tracking_or_salary(self):
        self.assertIsNone(sources.clean_url("javascript:alert(1)"))
        self.assertIsNone(sources.clean_url("https://user:pass@example.test/jobs"))
        self.assertIsNone(sources.clean_url("http://example.test/jobs"))
        self.assertEqual(sources.clean_url("https://example.test/jobs?id=42&utm_source=mail&ref=abc&salary_min=100"),
                         "https://example.test/jobs?id=42")
        self.assertEqual(sources.clean_url("http://example.test/jobs?id=42",allow_http=True),
                         "http://example.test/jobs?id=42")
        self.assertIsNone(sources.clean_url("http://user:pass@example.test/jobs",allow_http=True))

    def test_missing_adzuna_key_does_not_leak_secret(self):
        with self.assertRaises(sources.SourceError) as err:
            sources.fetch_adzuna(country="pl",keywords=["analyst"],location="",pages=1,
                timeout=5,app_id="",app_key="NEVER-LOG-THIS")
        self.assertNotIn("NEVER-LOG-THIS", str(err.exception))


class CollectionTests(unittest.TestCase):
    def test_run_deduplicates_on_second_run_and_keeps_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"jobs.json"; new=root/"new.json"; summary=root/"summary.json"
            fake=[{"id":"lever:example:1","title":"Analyst","company":"Example",
                   "location":"Warsaw","url":"https://jobs.example.test/1","source":"lever",
                   "_match_text":"analyst finance"}]
            with patch("jobsearch.core.sources.fetch_lever",return_value=fake):
                first=core.run_collection(config(),state,new,summary,env={},
                    now=datetime(2026,11,13,tzinfo=timezone.utc))
            first_job=json.loads(new.read_text())[0]
            self.assertEqual(first["new"],1)
            self.assertEqual(set(first_job),set(core.OFFER_KEYS))
            with patch("jobsearch.core.sources.fetch_lever",return_value=fake):
                second=core.run_collection(config(),state,new,summary,env={},
                    now=datetime(2026,11,14,tzinfo=timezone.utc))
            second_job=json.loads(state.read_text())["offers"][0]
            self.assertEqual(second["new"],0)
            self.assertEqual(second_job["first_seen"],first_job["first_seen"])
            self.assertEqual(second_job["last_seen"],"2026-11-14T00:00:00Z")

    def test_adzuna_http_redirect_is_preserved_in_state_and_dashboard(self):
        cfg=config()
        cfg["search"]["keywords"]=["analyst"]
        fake=[{"id":"adzuna:pl:1","title":"Analyst","company":"Example","location":"Warsaw",
            "url":"http://jobs.example.test/1","source":"adzuna","_match_text":"analyst"}]
        with tempfile.TemporaryDirectory() as tmp, patch("jobsearch.core.sources.fetch_adzuna",
                return_value=sources.SourceBatch("adzuna",fake)):
            root=Path(tmp);cfg["sources"]={"adzuna":{"enabled":True}}
            core.run_collection(cfg,root/"jobs.json",root/"new.json",root/"summary.json",env={})
            saved=json.loads((root/"jobs.json").read_text())["offers"][0]
            self.assertEqual(saved["url"],"http://jobs.example.test/1")
            dashboard=render_dashboard([saved],"2026-11-13T00:00:00Z")
            self.assertIn("http://jobs.example.test/1",dashboard)

    def test_location_filter_uses_ats_location_not_description(self):
        cfg=config();cfg["search"]["location"]="Warsaw"
        fake=[{"id":"lever:example:1","title":"Analyst","company":"Example",
               "location":"Berlin","url":"https://jobs.example.test/1","source":"lever",
               "_match_text":"analyst Berlin Warsaw is mentioned in the description"}]
        with tempfile.TemporaryDirectory() as tmp, patch("jobsearch.core.sources.fetch_lever",return_value=fake):
            root=Path(tmp)
            result=core.run_collection(cfg,root/"jobs.json",root/"new.json",root/"summary.json",env={})
            self.assertEqual(result["fetched"],0)

    def test_partial_adapter_warning_retains_successful_offers(self):
        cfg=config()
        batch=sources.SourceBatch("lever",[{"id":"lever:example:1","title":"Analyst",
            "company":"Example","location":"Warsaw","url":"https://jobs.example.test/1",
            "source":"lever","_match_text":"analyst"}],"częściowa awaria: tablica 2")
        with tempfile.TemporaryDirectory() as tmp, patch("jobsearch.core.sources.fetch_lever",return_value=batch):
            root=Path(tmp)
            result=core.run_collection(cfg,root/"jobs.json",root/"new.json",root/"summary.json",env={})
            self.assertEqual(result["source_status"][0]["status"],"warning")
            self.assertEqual(result["fetched"],1)

    def test_all_sources_fail_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"jobs.json"; new=root/"new.json"; summary=root/"summary.json"
            state.write_text(json.dumps({"version":1,"last_run":"old","offers":[sample_offer()]}))
            before=state.read_bytes()
            with patch("jobsearch.core.sources.fetch_lever",side_effect=sources.SourceError("timeout")):
                with self.assertRaises(core.CollectionError):
                    core.run_collection(config(),state,new,summary,env={})
            self.assertEqual(state.read_bytes(),before)
            self.assertFalse(new.exists())
            self.assertFalse(summary.exists())

    def test_partial_source_failure_keeps_successful_results(self):
        cfg=config()
        cfg["sources"]["greenhouse"]={"enabled":True,"boards":["sample"]}
        fake=[{"id":"lever:example:1","title":"Engineer","company":"Example",
               "location":"Warsaw","url":"https://jobs.example.test/1","source":"lever",
               "_match_text":"engineer"}]
        with tempfile.TemporaryDirectory() as tmp, \
             patch("jobsearch.core.sources.fetch_lever",return_value=fake), \
             patch("jobsearch.core.sources.fetch_greenhouse",side_effect=sources.SourceError("timeout")):
            root=Path(tmp)
            result=core.run_collection(cfg,root/"jobs.json",root/"new.json",root/"summary.json",env={})
            self.assertEqual(result["new"],1)
            self.assertEqual([x["status"] for x in result["source_status"]],["ok","error"])

    def test_pending_email_survives_repeated_run_until_cleared(self):
        cfg=config(email=True)
        fake=[{"id":"lever:example:1","title":"Analyst","company":"Example",
               "location":"Warsaw","url":"https://jobs.example.test/1","source":"lever",
               "_match_text":"analyst"}]
        with tempfile.TemporaryDirectory() as tmp, patch("jobsearch.core.sources.fetch_lever",return_value=fake):
            root=Path(tmp); state=root/"jobs.json"; new=root/"new.json"; summary=root/"summary.json"
            core.run_collection(cfg,state,new,summary,env={})
            core.run_collection(cfg,state,new,summary,env={})
            self.assertEqual(len(json.loads(new.read_text())),1)
            self.assertEqual(len(json.loads(state.read_text())["pending_email"]),1)
            core.clear_pending_email(state)
            self.assertEqual(json.loads(state.read_text())["pending_email"],[])

    def test_email_failure_does_not_clear_pending_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); state=root/"jobs.json"; new=root/"new.json"; cfg_path=root/"config.toml"
            state.write_text(json.dumps({"version":1,"last_run":"now","offers":[sample_offer()],
                "pending_email":[sample_offer()["id"]],"pending_email_offers":[sample_offer()]}))
            new.write_text(json.dumps([sample_offer()]))
            cfg_path.write_text('[search]\nkeywords=["analyst"]\n[email]\nenabled=true\n')
            before=state.read_bytes()
            with patch("jobsearch.cli.send_new_jobs",side_effect=EmailError("temporary failure")):
                code=main(["email","--config",str(cfg_path),"--state",str(state),"--new-jobs",str(new)])
            self.assertEqual(code,1)
            self.assertEqual(state.read_bytes(),before)

    def test_smtp_refused_recipients_is_failure_without_secret_disclosure(self):
        fake_client=MagicMock()
        fake_client.__enter__.return_value=fake_client
        fake_client.send_message.return_value={"refused@example.test":(550,b"no")}
        env={"SMTP_HOST":"smtp.example.test","SMTP_PORT":"465","SMTP_SECURITY":"ssl",
             "SMTP_USERNAME":"user","SMTP_PASSWORD":"SECRET-PASSWORD",
             "SMTP_FROM":"sender@example.test","SMTP_TO":"refused@example.test"}
        with patch("jobsearch.emailer.smtplib.SMTP_SSL",return_value=fake_client):
            with self.assertRaises(EmailError) as err:
                send_new_jobs([sample_offer()],env)
        self.assertNotIn("SECRET-PASSWORD",str(err.exception))
        self.assertNotIn("refused@example.test",str(err.exception))

    def test_config_requires_keywords(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"config.toml"
            path.write_text('[search]\nkeywords=[]\n')
            with self.assertRaises(core.ConfigError):
                core.load_config(path)

    def test_dashboard_sanitizes_xss_and_adds_adzuna_attribution(self):
        offer=sample_offer(title='</script><img src=x onerror=alert(1)> Analyst')
        offer["source"]="adzuna"
        offer["url"]="http://jobs.example.test/1?salary_min=123&utm_source=mail"
        page=render_dashboard([offer],"2026-11-13T00:00:00Z")
        self.assertNotIn("</script><img",page)
        self.assertIn("\\u003c/script\\u003e",page)
        self.assertIn('textContent',page)
        self.assertIn("offer.source===\"adzuna\"",page)
        self.assertIn("https://www.adzuna.pl/",page)
        self.assertIn("min-width:116px;min-height:23px",page)
        self.assertNotIn("salary_min",page)
        self.assertNotIn("123",page)



    def test_adzuna_and_greenhouse_reject_wrong_json_shape(self):
        with patch("jobsearch.sources._request_json",return_value=[]):
            with self.assertRaises(sources.SourceError):
                sources.fetch_adzuna(country="pl",keywords=["analyst"],location="",pages=1,
                    timeout=5,app_id="id",app_key="secret")
        with patch("jobsearch.sources._request_json",return_value=[]):
            with self.assertRaises(sources.SourceError):
                sources.fetch_greenhouse(boards=["example"],timeout=5)

    def test_common_salary_formats_are_removed_from_title(self):
        examples=[
            ("Analyst 6 400–15 000 PLN netto/mies.","6 400","PLN","15 000"),
            ("Developer €55k per hour","55k","€","hour"),
            ("Engineer $100-$120/hour","100","120","$"),
            ("Consultant USD 80,000 to 110,000 per year","80,000","110,000","USD"),
            ("Analyst 6–10 tys. zł brutto","6","10","tys.","zł","brutto"),
            ("Manager od 8 tys. zł brutto","od ","8","tys.","zł","brutto"),
            ("Engineer 150 netto/h","150","netto","/h"),
        ]
        for title, *forbidden in examples:
            row=sources._normalized("adzuna","pl",raw_id="1",title=title,company="Example",
                location="Warsaw",url="http://jobs.example.test/1",allow_http=True)
            self.assertIsNotNone(row)
            self.assertIn("[wynagrodzenie pominięte]",row["title"])
            for token in forbidden:
                self.assertNotIn(token,row["title"])
        unchanged=sources._normalized("adzuna","pl",raw_id="2",title="Analyst with 3 years experience",
            company="Example",location="Warsaw",url="http://jobs.example.test/2",allow_http=True)
        self.assertEqual(unchanged["title"],"Analyst with 3 years experience")

    def test_adzuna_email_has_per_offer_attribution_and_escapes_html(self):
        client=MagicMock()
        client.__enter__.return_value=client
        client.send_message.return_value={}
        offers=[
            {"title":"<img src=x onerror=alert(1)> 6 400 PLN","company":"Example",
             "location":"Warsaw","url":"http://jobs.example.test/1","source":"adzuna"},
            {"title":"Engineer","company":"Example","location":"Remote",
             "url":"https://jobs.example.test/2","source":"adzuna"},
        ]
        env={"SMTP_HOST":"smtp.example.test","SMTP_PORT":"465","SMTP_SECURITY":"ssl",
             "SMTP_USERNAME":"user","SMTP_PASSWORD":"secret","SMTP_FROM":"sender@example.test",
             "SMTP_TO":"receiver@example.test"}
        with patch("jobsearch.emailer.smtplib.SMTP_SSL",return_value=client):
            send_new_jobs(offers,env)
        message=client.send_message.call_args.args[0]
        html_part=message.get_body(preferencelist=("html",)).get_content()
        plain_part=message.get_body(preferencelist=("plain",)).get_content()
        self.assertEqual(html_part.count(">Adzuna</a>"),2)
        self.assertEqual(html_part.count('href="https://www.adzuna.pl/"'),2)
        self.assertIn("width:116px",html_part)
        self.assertNotIn("<img src=x",html_part)
        self.assertIn("Adzuna: https://www.adzuna.pl/",plain_part)


if __name__ == "__main__":
    unittest.main()
