"""Operacje zapisu klienta YouTracka (PT-72, część 1): założenie zgłoszenia, powiązanie, komentarz.

Testy z zewnątrz klienta na atrapie REST API (fake_youtrack.py, 127.0.0.1) — nigdy prawdziwy serwer;
klucze zmyślone (DEMO-n).
"""
import io
import logging
import unittest

from . import coordfix as F
from .fake_youtrack import FakeYouTrack
from tide_daemon.coordination import youtrack as yt

TOKEN = F.TOKEN_SENTINEL


class Ops(unittest.TestCase):
    def setUp(self):
        self.fake = FakeYouTrack(TOKEN)
        self.addCleanup(self.fake.stop)
        self.url = self.fake.url

    def create(self, card="karta-1", number=7, summary="Tytuł", body="Opis z kartki", token=TOKEN, **kw):
        return yt.create_issue(self.url, "DEMO", summary, body, card, number, token, **kw)

    # --- założenie ---------------------------------------------------------------------------

    def test_create_makes_one_issue_with_marker_in_description(self):
        issue = self.create()
        self.assertEqual(issue["id"], "DEMO-1")
        self.assertTrue(issue["created"])
        self.assertEqual(issue["url"], f"{self.url}/issue/DEMO-1")
        stored = self.fake.issues["DEMO-1"]
        self.assertEqual(stored["summary"], "Tytuł")
        self.assertIn("Opis z kartki", stored["description"])
        self.assertEqual(yt.marker_of(stored["description"]), ("karta-1", 7))

    def test_create_twice_returns_existing_issue(self):
        first = self.create()
        again = self.create()
        self.assertEqual(again["id"], first["id"])
        self.assertFalse(again["created"])
        self.assertEqual(len(self.fake.issues), 1)

    def test_other_number_or_card_is_a_different_issue(self):
        self.create(number=7)
        self.create(number=70)         # 7 nie jest przedrostkiem 70
        self.create(card="karta-10", number=7)
        self.create(card="karta-1-b", number=7)
        self.assertEqual(len(self.fake.issues), 4)

    def test_lost_reply_then_retry_gives_no_duplicate(self):
        self.fake.lose_replies = 1
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.create()
        self.assertTrue(ctx.exception.transient)
        self.assertEqual(len(self.fake.issues), 1)       # serwer założył zgłoszenie
        again = self.create()
        self.assertEqual(again["id"], "DEMO-1")
        self.assertEqual(len(self.fake.issues), 1)

    def test_marker_survives_human_editing_description(self):
        self.create()
        issue = self.fake.issues["DEMO-1"]
        issue["description"] = "## Przepisane przez człowieka\n\nnowa treść\n\n" + issue["description"].split("\n\n")[-1] \
            + "\n\ndopisek na końcu"
        self.assertEqual(self.create()["id"], "DEMO-1")
        self.assertEqual(len(self.fake.issues), 1)

    def test_marker_text_in_other_issue_is_not_a_match(self):
        self.fake.add("DEMO-1", description="rozmowa o znaczniku tide-ref bez właściwego numeru")
        self.assertEqual(self.create()["id"], "DEMO-2")

    def test_marker_cannot_be_forged_by_card_name(self):
        for bad in ("a b", "a\nb", "a)", ""):
            with self.assertRaises(yt.YouTrackError) as ctx:
                self.create(card=bad)
            self.assertFalse(ctx.exception.transient)
        self.assertEqual(self.fake.calls, [])

    # --- błędy -------------------------------------------------------------------------------

    def test_unavailable_server_is_transient(self):
        self.fake.status = 503
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.create()
        self.assertTrue(ctx.exception.transient)
        self.assertFalse(ctx.exception.permanent)

    def test_closed_port_is_transient(self):
        self.fake.stop()
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.create(timeout=1.0)
        self.assertTrue(ctx.exception.transient)

    def test_validation_error_is_permanent(self):
        self.fake.projects = {"OTHER"}
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.create()
        self.assertEqual(ctx.exception.status, 400)
        self.assertTrue(ctx.exception.permanent)
        self.assertFalse(ctx.exception.transient)

    def test_empty_summary_is_permanent_without_a_request(self):
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.create(summary="  ")
        self.assertTrue(ctx.exception.permanent)
        self.assertEqual(self.fake.calls, [])

    def test_wrong_token_is_transient_not_a_rejection(self):
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.create(token="wrong-token")
        self.assertEqual(ctx.exception.status, 401)
        self.assertTrue(ctx.exception.transient)

    def test_missing_token_never_sends(self):
        with self.assertRaises(yt.YouTrackError):
            self.create(token="")
        self.assertEqual(self.fake.calls, [])

    def test_token_only_in_authorization_header_never_in_errors_or_logs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        old = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(lambda: (root.removeHandler(handler), root.setLevel(old)))
        messages = []
        for status in (503, 400, 401):
            self.fake.status = status
            for call in (lambda: self.create(), lambda: yt.link_issues(self.url, "DEMO-1", "DEMO-2", TOKEN),
                         lambda: yt.add_comment(self.url, "DEMO-1", "tekst", "karta-1", "PT-1", "merged", TOKEN)):
                with self.assertRaises(yt.YouTrackError) as ctx:
                    call()
                messages.append(str(ctx.exception) + repr(ctx.exception) + str(ctx.exception.__cause__))
        self.fake.status = 200
        self.create()
        self.assertTrue(all(TOKEN not in m for m in messages))
        self.assertNotIn(TOKEN, stream.getvalue())
        for _method, path, auth, body in self.fake.calls:
            self.assertEqual(auth, f"Bearer {TOKEN}")
            self.assertNotIn(TOKEN, path + body)

    # --- powiązanie --------------------------------------------------------------------------

    def test_link_issues_relates_by_default_and_is_repeatable(self):
        self.fake.add("DEMO-1")
        self.fake.add("DEMO-2")
        result = yt.link_issues(self.url, "DEMO-2", "DEMO-1", TOKEN)
        self.assertEqual(result, {"id": "DEMO-2", "target": "DEMO-1", "linkType": "relates to"})
        yt.link_issues(self.url, "DEMO-2", "DEMO-1", TOKEN)
        self.assertEqual(self.fake.links, [("DEMO-2", "relates to DEMO-1")])

    def test_link_issues_subtask_type(self):
        self.fake.add("DEMO-1")
        self.fake.add("DEMO-2")
        yt.link_issues(self.url, "DEMO-2", "DEMO-1", TOKEN, link_type="subtask of")
        self.assertEqual(self.fake.links, [("DEMO-2", "subtask of DEMO-1")])

    def test_link_rejects_bad_keys_and_types_without_a_request(self):
        for args, kw in ((("DEMO-2", "nie klucz"), {}), (("x", "DEMO-1"), {}),
                         (("DEMO-2", "DEMO-1"), {"link_type": "relates to; delete"})):
            with self.assertRaises(yt.YouTrackError) as ctx:
                yt.link_issues(self.url, *args, TOKEN, **kw)
            self.assertTrue(ctx.exception.permanent)
        self.assertEqual(self.fake.calls, [])

    def test_link_unknown_issue_is_permanent(self):
        self.fake.add("DEMO-1")
        with self.assertRaises(yt.YouTrackError) as ctx:
            yt.link_issues(self.url, "DEMO-1", "DEMO-9", TOKEN)
        self.assertTrue(ctx.exception.permanent)

    # --- komentarz ---------------------------------------------------------------------------

    def comment(self, text="Zrobione na gałęzi karty", ticket="PT-1", event="merged", card="karta-1"):
        return yt.add_comment(self.url, "DEMO-1", text, card, ticket, event, TOKEN)

    def test_comment_added_once(self):
        self.fake.add("DEMO-1")
        first = self.comment()
        again = self.comment()
        self.assertTrue(first["created"])
        self.assertFalse(again["created"])
        self.assertEqual(again["commentId"], first["commentId"])
        self.assertEqual(len(self.fake.comments["DEMO-1"]), 1)
        self.assertIn("Zrobione na gałęzi karty", self.fake.comments["DEMO-1"][0]["text"])

    def test_comment_lost_reply_retry_gives_one_comment(self):
        self.fake.add("DEMO-1")
        self.fake.lose_replies = 1
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.comment()
        self.assertTrue(ctx.exception.transient)
        self.comment()
        self.assertEqual(len(self.fake.comments["DEMO-1"]), 1)

    def test_comment_other_ticket_event_or_card_is_separate(self):
        self.fake.add("DEMO-1")
        self.comment()
        self.comment(ticket="PT-10")
        self.comment(event="rejected")
        self.comment(card="karta-2")
        self.assertEqual(len(self.fake.comments["DEMO-1"]), 4)

    def test_comment_on_unavailable_server_is_transient(self):
        self.fake.add("DEMO-1")
        self.fake.status = 503
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.comment()
        self.assertTrue(ctx.exception.transient)

    def test_comment_empty_text_is_permanent(self):
        self.fake.add("DEMO-1")
        with self.assertRaises(yt.YouTrackError) as ctx:
            self.comment(text=" ")
        self.assertTrue(ctx.exception.permanent)
        self.assertEqual(self.fake.calls, [])


class Markers(unittest.TestCase):
    def test_marker_roundtrip_and_tolerance(self):
        text = yt.issue_marker("karta-2026.10_x", 12)
        self.assertEqual(yt.marker_of("coś\n" + text + "\nkoniec"), ("karta-2026.10_x", 12))
        self.assertIsNone(yt.marker_of("brak znacznika"))

    def test_marker_not_visible_when_rendered(self):
        self.assertTrue(yt.issue_marker("k", 1).startswith("[//]: #"))


if __name__ == "__main__":
    unittest.main()
