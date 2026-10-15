import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/verdantflare-video/scripts"))
import higgsfield as admin


class HandoverGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        (self.state / "credentials.json").write_text(json.dumps({"access_token":"synthetic", "refresh_token":"synthetic"}))
        (self.state / "config.json").write_text('{}')
        self.service = {"metadata":{"uid":"original-service", "resourceVersion":"1", "annotations":{"other":"keep"}}}
        self.deployment = {"metadata":{}, "spec":{"replicas":0,"selector":{"matchLabels":{"app":"gateway"}}}, "status":{"replicas":0}}
        self.pods = {"items":[]}
        self.imports = 0
        self.receipt = None
        self.lost_reply = False
        self.transition_wins = False

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, args, **kwargs):
        result = lambda value: SimpleNamespace(returncode=0, stdout=json.dumps(copy.deepcopy(value)).encode())
        if "exec" in args:
            if "--receipt" in args:
                self.assertEqual(kwargs["input"], b"")
                return result(self.receipt)
            self.assertIn(admin.FENCE_KEY, self.service["metadata"]["annotations"])
            self.assertTrue((self.state / "handover-pending.json").exists())
            payload = json.loads(kwargs["input"])
            self.assertEqual(payload["credentials"]["access_token"], "synthetic")
            self.imports += 1
            pending = json.loads((self.state / "handover-pending.json").read_text())
            self.receipt = {"imported":True,"revision":pending["revision"]}
            if self.lost_reply:
                raise admin.subprocess.TimeoutExpired("kubectl",60)
            return result(self.receipt)
        if "get" in args:
            kind = args[args.index("get")+1]
            return result({"service":self.service,"deployment":self.deployment,"pods":self.pods}[kind])
        if "patch" in args:
            operations = json.loads(args[args.index("--patch")+1])
            if self.transition_wins:
                self.transition_wins = False
                self.service["metadata"]["resourceVersion"] = "2"
                self.service["metadata"]["annotations"][admin.FENCE_KEY] = "real-upgrade-operation"
            for op in operations:
                if op["op"] == "test" and self.service["metadata"][op["path"].split('/')[-1]] != op["value"]:
                    return SimpleNamespace(returncode=1,stdout=b'')
            self.service["metadata"]["annotations"] = operations[-1]["value"]
            self.service["metadata"]["resourceVersion"] = str(int(self.service["metadata"]["resourceVersion"])+1)
            return result(self.service)
        raise AssertionError("unexpected command")

    def context(self):
        return patch.multiple(admin, STATE=self.state), patch.object(admin.shutil,"which",return_value="kubectl"), patch.object(admin.subprocess,"run",side_effect=self.execute)

    def handover(self):
        a,b,c=self.context()
        with a,b,c:return admin.handover("import-pod","gateway","workspace_test")

    def reconcile(self):
        a,b,c=self.context()
        with a,b,c:return admin.reconcile("import-pod","gateway")

    def test_success_keeps_unrelated_annotations_and_releases_own_fence(self):
        self.assertTrue(self.handover()["handed_over"])
        self.assertEqual(self.imports,1)
        self.assertEqual(self.service["metadata"]["annotations"],{"other":"keep"})
        self.assertTrue((self.state/"server-owned.json").exists())
        self.assertFalse((self.state/"handover-pending.json").exists())

    def test_upgrade_marker_rejects_before_sending_credentials(self):
        self.service["metadata"]["annotations"][admin.FENCE_KEY]="upgrade"
        with self.assertRaisesRegex(admin.AdminError,"application_transition_active"):self.handover()
        self.assertEqual(self.imports,0)
        self.assertFalse((self.state/"handover-pending.json").exists())

    def test_running_or_terminating_writer_rejects(self):
        self.deployment["spec"]["replicas"]=1
        with self.assertRaisesRegex(admin.AdminError,"gateway_must_be_stopped"):self.handover()
        self.deployment["spec"]["replicas"]=0
        self.pods={"items":[{"metadata":{"name":"old-writer","deletionTimestamp":"terminating"}}]}
        with self.assertRaisesRegex(admin.AdminError,"credential_writer_not_drained"):self.handover()
        self.assertEqual(self.imports,0)

    def test_upgrade_wins_cas_no_import_and_reconcile_never_removes_its_marker(self):
        self.transition_wins=True
        with self.assertRaises(admin.AdminError):self.handover()
        self.assertEqual(self.imports,0)
        self.assertFalse(json.loads((self.state/"handover-pending.json").read_text())["import_started"])
        self.assertTrue(self.reconcile()["not_imported"])
        self.assertEqual(self.service["metadata"]["annotations"][admin.FENCE_KEY],"real-upgrade-operation")
        self.assertFalse((self.state/"server-owned.json").exists())

    def test_response_lost_retains_fence_and_reconciles_same_revision_without_reimport(self):
        self.lost_reply=True
        with self.assertRaises(admin.subprocess.TimeoutExpired):self.handover()
        self.assertIn(admin.FENCE_KEY,self.service["metadata"]["annotations"])
        with patch.object(admin,"STATE",self.state), self.assertRaisesRegex(admin.AdminError,"credentials_owned_by_server"):
            admin.run_cli(["account","status"],"http://127.0.0.1:19081")
        with patch.object(admin,"STATE",self.state), self.assertRaisesRegex(admin.AdminError,"credential_handover_unconfirmed"):
            admin.run_cli(["auth","login"],"http://127.0.0.1:19081",login=True)
        self.assertTrue(self.reconcile()["local_refresh_disabled"])
        self.assertEqual(self.imports,1)
        self.assertNotIn(admin.FENCE_KEY,self.service["metadata"]["annotations"])

    def test_replaced_service_never_releases_or_clears_pending(self):
        self.lost_reply=True
        with self.assertRaises(admin.subprocess.TimeoutExpired):self.handover()
        self.service["metadata"]["uid"]="replacement-service"
        with self.assertRaisesRegex(admin.AdminError,"handover_fence_conflict"):self.reconcile()
        self.assertTrue((self.state/"handover-pending.json").exists())
        self.assertIn(admin.FENCE_KEY,self.service["metadata"]["annotations"])


if __name__ == "__main__":unittest.main()
