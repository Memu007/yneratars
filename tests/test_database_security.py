import json
import tempfile
import unittest
from pathlib import Path

from app.database import ConflictError, Database
from app.security import infer_risk, excluded_browser_actions

SETTINGS = {
    "provider": "google", "google_model": "gemini-3.5-flash",
    "openai_model": "gpt-test", "browser_model": "gemini-3.5-flash",
}


class DatabaseSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "tars.db")

    def tearDown(self):
        self.tmp.cleanup()

    def mission(self, **changes):
        payload = {"title": "Prueba", "description": "abrir una página pública", "worker": "CASE", "risk": "low"}
        payload.update(changes)
        return self.db.create_mission(payload, SETTINGS)

    def test_low_risk_queued(self):
        mission = self.mission()
        self.assertEqual(mission["status"], "queued")
        self.assertEqual(mission["tool"], "browser")

    def test_payment_is_blocked_even_if_low(self):
        mission = self.mission(description="comprar una notebook y pagar con tarjeta")
        self.assertEqual(mission["risk"], "blocked")
        self.assertEqual(mission["status"], "blocked")

    def test_login_requires_approval(self):
        mission = self.mission(description="iniciar sesión en mi cuenta")
        self.assertEqual(mission["status"], "awaiting_approval")
        approved = self.db.transition(mission["id"], "approve")
        self.assertEqual(approved["status"], "queued")

    def test_blocked_cannot_be_approved(self):
        mission = self.mission(description="cambiar contraseña")
        with self.assertRaises(ConflictError):
            self.db.transition(mission["id"], "approve")

    def test_only_one_claim(self):
        one = self.mission(title="Uno")
        two = self.mission(title="Dos")
        claimed = self.db.claim_next()
        self.assertEqual(claimed["id"], one["id"])
        self.assertIsNone(self.db.claim_next())
        self.db.finish(one["id"], True, "listo")
        claimed2 = self.db.claim_next()
        self.assertEqual(claimed2["id"], two["id"])

    def test_cannot_retry_completed(self):
        mission = self.mission()
        self.db.claim_next()
        self.db.finish(mission["id"], True, "ok")
        with self.assertRaises(ConflictError):
            self.db.transition(mission["id"], "retry")

    def test_stop_running_preserves_stopped(self):
        mission = self.mission()
        self.db.claim_next()
        stopped = self.db.transition(mission["id"], "stop")
        self.assertEqual(stopped["status"], "stopped")
        final = self.db.finish(mission["id"], True, "should not overwrite")
        self.assertEqual(final["status"], "stopped")

    def test_events_created(self):
        mission = self.mission()
        self.db.claim_next()
        self.db.finish(mission["id"], True, "ok")
        kinds = [x["kind"] for x in self.db.events(mission["id"])]
        self.assertEqual(kinds, ["created", "started", "completed"])

    def test_messages_order(self):
        self.db.add_message("c", "user", "uno")
        self.db.add_message("c", "assistant", "dos")
        self.assertEqual([x["content"] for x in self.db.messages("c")], ["uno", "dos"])

    def test_invalid_worker(self):
        with self.assertRaises(ValueError):
            self.mission(worker="OTRO")

    def test_risk_phrase(self):
        self.assertEqual(infer_risk("x", "subir archivo").risk, "high")

    def test_low_risk_browser_actions_allow_navigation_and_search(self):
        blocked = excluded_browser_actions("low")
        self.assertNotIn("click", blocked)
        self.assertNotIn("input", blocked)
        self.assertNotIn("select_dropdown", blocked)
        self.assertIn("upload_file", blocked)
        self.assertIn("send_keys", blocked)
        self.assertIn("evaluate", blocked)

    def test_high_risk_still_blocks_javascript_and_file_writes(self):
        blocked = excluded_browser_actions("high")
        self.assertNotIn("input", blocked)
        self.assertIn("evaluate", blocked)
        self.assertIn("write_file", blocked)

    def test_stopped_unapproved_sensitive_mission_still_requires_approval(self):
        mission = self.mission(description="iniciar sesión en mi cuenta")
        self.assertEqual(mission["status"], "awaiting_approval")
        self.db.transition(mission["id"], "stop")
        retried = self.db.transition(mission["id"], "retry")
        self.assertEqual(retried["status"], "awaiting_approval")
        self.assertIsNone(retried["approved_at"])

    def test_approved_sensitive_mission_can_retry_without_second_approval(self):
        mission = self.mission(description="iniciar sesión en mi cuenta")
        approved = self.db.transition(mission["id"], "approve")
        self.assertIsNotNone(approved["approved_at"])
        self.db.claim_next()
        self.db.finish(mission["id"], False, error="fallo")
        retried = self.db.transition(mission["id"], "retry")
        self.assertEqual(retried["status"], "queued")

    def test_recover_interrupted_mission_unblocks_queue(self):
        first = self.mission(title="Primera")
        second = self.mission(title="Segunda")
        self.assertEqual(self.db.claim_next()["id"], first["id"])
        recovered = self.db.recover_interrupted()
        self.assertEqual(recovered, [first["id"]])
        self.assertEqual(self.db.get_mission(first["id"])["status"], "failed")
        self.assertEqual(self.db.claim_next()["id"], second["id"])

    def test_accented_spanish_risk_terms(self):
        self.assertEqual(infer_risk("x", "Mandá un correo al cliente").risk, "high")
        self.assertEqual(infer_risk("x", "Cambiá la contraseña").risk, "blocked")

    def test_negated_sensitive_actions_do_not_raise_risk(self):
        self.assertEqual(infer_risk("Lectura", "leer example.com sin enviar formularios").risk, "low")
        self.assertEqual(infer_risk("Investigación", "no comprar ni pagar; sólo comparar precios").risk, "low")
        self.assertEqual(infer_risk("Acción", "leer primero y luego enviar un formulario").risk, "high")


if __name__ == "__main__":
    unittest.main()
