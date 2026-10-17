import copy
import itertools
import json
import threading
import unittest
import urllib.error
import urllib.request

from blindspot_lab.lab.adaptive import AdaptiveLab, adaptive_context
from blindspot_lab.lab.content import digest, runtime_fingerprint
from blindspot_lab.lab.errors import LabError
from blindspot_lab.lab.models import MockModel
from blindspot_lab.examples.adaptive_host import AdaptiveHost
from blindspot_lab.examples.adaptive_server import AcceptanceServer


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.lab = AdaptiveLab()
        self.host = AdaptiveHost(adaptive_context())
        self.serial = 0

    def act(self, action, **params):
        self.serial += 1
        return self.host.execute(self.lab,action,f"req{self.serial}",self.host.read()["revision"],**params)

    def submit(self, answer):
        q = self.host.public_read(self.lab)["lab_state"]["question"]
        return self.act("submit",question_id=q["question_id"],answer=answer)

    def first(self, answers=("A","A","A")):
        self.act("start")
        for answer in answers:
            result = self.submit(answer)
        return result

    def assert_error(self, code, callback):
        with self.assertRaises(LabError) as caught:
            callback()
        self.assertEqual(caught.exception.code,code)

    def test_failure_changes_strategy_fresh_question_and_second_verification_updates(self):
        result = self.first()
        self.assertEqual(result["lab_state"]["summary"]["result_status"],"needs_practice")
        before = result["lab_state"]["adaptation"]["strategy"]
        qids = result["learning_state"]["core"]["selected_question_ids"][:]
        result = self.act("continue")
        qid = result["lab_state"]["question"]["question_id"]
        self.assertNotIn(qid,qids)
        correct = self.lab.questions[qid]["correct_option_id"]
        result = self.submit(correct)
        self.assertEqual(result["lab_state"]["summary"]["result_status"],"tentative_transfer")
        self.assertEqual(result["lab_state"]["adaptation"]["strategy"],"transfer")
        self.assertNotEqual(result["lab_state"]["adaptation"]["strategy"],before)
        self.assertIn(result["learning_state"]["followups"][-1]["answer_id"],result["lab_state"]["summary"]["evidence_ids"])

    def test_guess_does_not_regrade_and_new_evidence_can_resolve_it(self):
        self.first(("B","A","B"))
        result = self.act("feedback",feedback="guessed")
        self.assertTrue(result["lab_state"]["summary"]["verification_correct"])
        self.assertEqual(result["lab_state"]["summary"]["result_status"],"needs_confirmation")
        self.act("continue")
        qid = self.host.public_read(self.lab)["lab_state"]["question"]["question_id"]
        result = self.submit(self.lab.questions[qid]["correct_option_id"])
        self.assertEqual(result["lab_state"]["summary"]["result_status"],"tentative_transfer")

    def test_feedback_is_not_a_pass_or_an_extra_round(self):
        result = self.first()
        original = copy.deepcopy(result["learning_state"]["core"]["answers"])
        for kind,strategy in (("unclear","steps"),("too_easy","transfer"),("new_example","analogy"),("guessed","counterexample")):
            result = self.act("feedback",feedback=kind)
            self.assertEqual(result["learning_state"]["core"]["answers"],original)
            self.assertEqual(result["lab_state"]["summary"]["result_status"],"needs_practice")
            self.assertEqual(result["lab_state"]["adaptation"]["strategy"],strategy)
            self.assertEqual(result["lab_state"]["cycle"]["round"],1)
            self.assertIn(result["learning_state"]["feedback_records"][-1]["id"],result["lab_state"]["explanation"]["evidence_ids"])

    def test_early_feedback_does_not_teach_and_is_used_after_distinguishing(self):
        self.act("start")
        self.assert_error("INVALID_INPUT",lambda:self.act("feedback",feedback="guessed"))
        self.submit("A")
        result = self.act("feedback",feedback="new_example")
        self.assertIsNone(result["lab_state"]["explanation"])
        self.assertFalse(any(e["type"]=="material" for e in result["lab_state"]["evidence"]))
        result = self.submit("A")
        self.assertEqual(result["lab_state"]["adaptation"]["strategy"],"analogy")
        self.assertIn("下雨",result["lab_state"]["explanation"]["text"])
        result = self.submit("B")
        self.assertEqual(result["lab_state"]["summary"]["result_status"],"tentative_transfer")

    def test_time_pause_resume_retains_history_and_reserves_verification(self):
        result = self.first()
        answers = copy.deepcopy(result["learning_state"]["core"]["answers"])
        result = self.act("time",minutes=4)
        self.assertEqual(result["lab_state"]["cycle"]["stop_reason"],"time_budget")
        self.assertEqual(result["plan"]["tasks"],[])
        self.assert_error("CONTINUE_UNAVAILABLE",lambda:self.act("continue"))
        result = self.act("time",minutes=5)
        self.assertEqual(result["plan"]["tasks"],[])
        self.assertFalse(result["lab_state"]["cycle"]["can_continue"])
        result = self.act("time",minutes=6)
        self.assertEqual([t["kind"] for t in result["plan"]["tasks"]],["learn","verify"])
        self.assertEqual(result["plan"]["total_minutes"],3)
        self.assertTrue(result["lab_state"]["cycle"]["can_continue"])
        self.assertEqual(result["learning_state"]["core"]["answers"],answers)
        self.act("continue")
        result = self.act("time",minutes=4)
        self.assertFalse(result["lab_state"]["cycle"]["can_answer"])
        self.assert_error("TIME_BUDGET_EXHAUSTED",lambda:self.submit("A"))
        result = self.act("time",minutes=60)
        self.assertTrue(result["lab_state"]["cycle"]["can_answer"])
        self.submit("A")

    def test_correct_and_failed_plans_are_different(self):
        wrong = self.first()["plan"]
        self.host = AdaptiveHost(adaptive_context("other"))
        correct = self.first(("B","A","B"))["plan"]
        self.assertNotEqual([t["task"] for t in wrong["tasks"]],[t["task"] for t in correct["tasks"]])

    def test_too_easy_changes_next_question_difficulty_and_budget_never_overflows(self):
        self.first()
        self.act("feedback",feedback="too_easy")
        result = self.act("continue")
        qid = result["lab_state"]["question"]["question_id"]
        self.assertEqual(self.lab.questions[qid]["difficulty"],2)
        for minutes in (1,2,3,4,5,6,10,30,60):
            result = self.act("time",minutes=minutes)
            self.assertLessEqual(result["plan"]["total_minutes"],max(0,minutes-result["learning_state"]["spent_minutes"]))
            self.assertTrue(all(t["minutes"]>0 for t in result["plan"]["tasks"]))

    def test_all_first_routes_then_two_rounds_are_bounded_and_unique(self):
        for choices in itertools.product("ABCD",repeat=3):
            with self.subTest(choices=choices):
                self.host = AdaptiveHost(adaptive_context())
                result = self.first(choices)
                qids = set(result["learning_state"]["core"]["selected_question_ids"])
                for _ in range(2):
                    result = self.act("continue")
                    qid = result["lab_state"]["question"]["question_id"]
                    self.assertNotIn(qid,qids)
                    qids.add(qid)
                    result = self.submit("D")
                self.assertEqual(result["lab_state"]["cycle"]["stop_reason"],"round_limit")
                self.assertFalse(result["lab_state"]["cycle"]["can_continue"])
                self.assert_error("CONTINUE_UNAVAILABLE",lambda:self.act("continue"))

    def test_budget_less_than_initial_cost_pauses_without_losing_question(self):
        self.act("start")
        result=self.act("time",minutes=1)
        self.assertEqual([t["task"] for t in result["plan"]["tasks"]],["完成首题，记录当前理解"])
        self.assertIn("完成区分题，核对暂时判断",result["plan"]["deferred"])
        self.submit("A")
        self.assert_error("TIME_BUDGET_EXHAUSTED",lambda:self.submit("A"))
        result = self.act("time",minutes=10)
        self.assertEqual(result["lab_state"]["question"]["question_id"],"q_distinguish_01")

    def test_bank_exhaustion_and_stop_have_explicit_reasons(self):
        self.lab.questions = {}
        result = self.first()
        self.assertEqual(result["lab_state"]["cycle"]["stop_reason"],"bank_exhausted")
        result = self.act("stop")
        self.assertEqual(result["lab_state"]["cycle"]["stop_reason"],"user_stop")
        self.assert_error("SESSION_STOPPED",lambda:self.act("feedback",feedback="unclear"))

    def test_every_action_is_idempotent_and_cas_protected(self):
        self.first()
        for action,params in (("feedback",{"feedback":"guessed"}),("time",{"minutes":30}),("continue",{})):
            rev = self.host.read()["revision"]
            rid = "duplicate_"+action
            result = self.host.execute(self.lab,action,rid,rev,**params)
            self.assertEqual(self.host.execute(self.lab,action,rid,rev,**params),result)
            self.assert_error("STALE_REVISION",lambda:self.host.execute(self.lab,"time","stale"+action,rev,minutes=40))
        self.assert_error("REQUEST_CONFLICT",lambda:self.host.execute(self.lab,"time","duplicate_time",4,minutes=10))

    def test_model_failure_does_not_save_partial_feedback_or_continuation(self):
        self.first()
        class Failing(MockModel):
            def generate(self,request,timeout_seconds):
                raise RuntimeError("do not expose provider secrets")
        self.lab.core.runners["mock"].model = Failing()
        for action,params in (("feedback",{"feedback":"unclear"}),("continue",{})):
            before = self.host.export_json()
            self.assert_error("MODEL_UNAVAILABLE",lambda:self.act(action,**params))
            self.assertEqual(before,self.host.export_json())

    def test_concurrent_time_edit_does_not_get_overwritten_by_model_result(self):
        self.first()
        entered,released = threading.Event(),threading.Event()
        class Blocking(MockModel):
            def generate(self,request,timeout_seconds):
                entered.set()
                released.wait(3)
                return super().generate(request,timeout_seconds)
        self.lab.core.runners["mock"].model = Blocking()
        errors = []
        rev = self.host.read()["revision"]
        def work():
            try:self.host.execute(self.lab,"feedback","slow",rev,feedback="unclear")
            except LabError as exc:errors.append(exc.code)
        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(entered.wait(2))
        self.act("time",minutes=15)
        released.set();thread.join(3)
        self.assertEqual(errors,["STALE_REVISION"])
        self.assertEqual(self.host.read()["learning_state"]["feedback_records"],[])
        self.assertEqual(self.host.read()["profile"]["daily_minutes"],15)

    def test_restore_resume_scope_and_fingerprints(self):
        self.first()
        self.act("continue")
        restored = AdaptiveHost.restore_json(self.host.export_json())
        self.assertEqual(self.host.public_read(self.lab),restored.public_read(self.lab))
        changed = restored.read();changed["target"]["school_id"]="new"
        self.assert_error("SCOPE_CHANGED",lambda:self.lab.start_lab(changed))
        self.assertIn("fixtures/logic_adaptive.json",runtime_fingerprint())

    def test_restored_adaptive_explanation_must_still_cite_real_evidence(self):
        self.first()
        self.act("feedback",feedback="unclear")
        c = self.host.read()
        s = c["learning_state"]
        s["teaching"]["explanation"]["evidence_ids"].append("invented")
        s.pop("integrity")
        s["integrity"] = digest(s)
        self.assert_error("EVIDENCE_INVALID",lambda:self.lab.start_lab(c))

    def test_event_sequence_is_not_a_question_number_and_invalid_ordinal_repairs(self):
        self.first()
        class WrongOrdinalOnce(MockModel):
            def __init__(self):self.seen=[]
            def generate(self,request,timeout_seconds):
                self.seen.append(copy.deepcopy(request))
                value=super().generate(request,timeout_seconds)
                if len(self.seen)==1:value["explanation"]["text"]="第8题表明还需核对。"
                return value
        model=WrongOrdinalOnce()
        self.lab.core.runners["mock"].model=model
        result=self.act("feedback",feedback="unclear")
        self.assertEqual(len(model.seen),2)
        payload=json.loads(model.seen[0]["messages"][1]["content"])
        self.assertNotIn('"sequence"',json.dumps(payload))
        self.assertEqual([a["answer_number"] for a in payload["observed_answers"]],[1,2,3])
        self.assertNotIn("第8题",result["lab_state"]["explanation"]["text"])

    def test_current_first_verification_survives_exact_one_minute_budget(self):
        self.act("start");self.submit("A");self.submit("A")
        result=self.act("time",minutes=3)
        self.assertTrue(result["lab_state"]["cycle"]["can_answer"])
        self.assertEqual([(t["kind"],t["minutes"]) for t in result["plan"]["tasks"]],[("verify",1)])
        self.assertFalse(any("验证" in title for title in result["plan"]["deferred"]))
        self.submit("B")

    def test_current_judgment_change_matches_followup_and_feedback(self):
        self.first();self.act("continue")
        qid=self.host.public_read(self.lab)["lab_state"]["question"]["question_id"]
        result=self.submit(self.lab.questions[qid]["correct_option_id"])
        change=result["lab_state"]["judgment_change"]
        self.assertEqual(change["after"],result["lab_state"]["hypotheses"])
        self.assertEqual(change["trigger_answer_id"],result["learning_state"]["followups"][-1]["answer_id"])
        result=self.act("feedback",feedback="guessed")
        change=result["lab_state"]["judgment_change"]
        self.assertEqual(change["after"],result["lab_state"]["hypotheses"])
        self.assertEqual(change["trigger_feedback_id"],result["learning_state"]["feedback_records"][-1]["id"])
        feedback_id=result["learning_state"]["feedback_records"][-1]["id"]
        self.assertIn(feedback_id,result["lab_state"]["summary"]["evidence_ids"])
        self.assertTrue(result["lab_state"]["summary"]["verification_correct"])
        self.act("continue")
        qid=self.host.public_read(self.lab)["lab_state"]["question"]["question_id"]
        result=self.submit(self.lab.questions[qid]["correct_option_id"])
        self.assertEqual(result["lab_state"]["summary"]["result_status"],"tentative_transfer")
        self.assertNotIn(feedback_id,result["lab_state"]["summary"]["evidence_ids"])
        self.assertTrue(any(e["id"]==feedback_id for e in result["lab_state"]["evidence"]))

    def test_null_and_missing_time_default_to_sixty_for_save_and_read(self):
        for profile in (None,{}, {"daily_minutes":None}):
            c=adaptive_context();c["profile"]=profile
            self.host=AdaptiveHost(c)
            result=self.act("start")
            self.assertEqual(result["plan"]["daily_minutes"],60)
            self.assertEqual(self.host.public_read(self.lab)["plan"]["daily_minutes"],60)
            self.submit("A")


class AcceptanceHTTPTests(unittest.TestCase):
    def test_origin_whitelist_cookie_and_public_projection(self):
        server = AcceptanceServer(0)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            jar = urllib.request.HTTPCookieProcessor()
            browser = urllib.request.build_opener(jar)
            base = server.origin
            with browser.open(base+"/api/state") as response:
                state = json.load(response)
                self.assertIsNotNone(response.headers.get("Set-Cookie"))
            body = json.dumps({"action":"start","request_id":"http1","expected_revision":0}).encode()
            request = urllib.request.Request(base+"/api/action",body,headers={"Content-Type":"application/json","Origin":base})
            with browser.open(request) as response:result = json.load(response)
            self.assertEqual(result["revision"],1)
            self.assertNotIn("learning_state",result)
            self.assertNotIn("correct_option_id",json.dumps(result))
            request = urllib.request.Request(base+"/api/action",body,headers={"Content-Type":"application/json","Origin":"https://foreign.invalid"})
            with self.assertRaises(urllib.error.HTTPError) as caught:browser.open(request)
            self.assertEqual(caught.exception.code,403)
            with self.assertRaises(urllib.error.HTTPError) as caught:browser.open(base+"/lab/aiping.py")
            self.assertEqual(caught.exception.code,404)
        finally:
            server.shutdown();server.server_close();thread.join(2)


if __name__ == "__main__":
    unittest.main()
