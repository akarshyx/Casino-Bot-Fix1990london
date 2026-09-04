import asyncio
import unittest
from unittest.mock import patch

import main


class AnimatedSessionSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_games = main.active_games
        self.old_tasks = main._animated_game_tasks
        main.active_games = {}
        main._animated_game_tasks = {}

    def tearDown(self):
        for tasks in main._animated_game_tasks.values():
            for task in tasks:
                task.cancel()
        main.active_games = self.old_games
        main._animated_game_tasks = self.old_tasks

    async def test_duplicate_callbacks_schedule_only_one_bot_turn(self):
        game = {
            "chat_id": 77,
            "state": main.EmojiGameState.WAITING_BOT_ROLL,
        }
        main.active_games["42"] = game
        calls = []

        async def fake_bot_roll(*args, **kwargs):
            calls.append((args, kwargs))

        with patch.object(main, "handle_bot_roll_turn_by_id", fake_bot_roll):
            first = main._schedule_bot_roll_turn(None, "42", 77)
            second = main._schedule_bot_roll_turn(None, "42", 77)
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            await asyncio.gather(first)

        self.assertEqual(len(calls), 1)

    def test_replacement_game_invalidates_old_task(self):
        old_game = {}
        main.active_games["42"] = old_game
        session_id, generation = main._ensure_animated_session(old_game)
        replacement = {}
        main.active_games["42"] = replacement

        self.assertFalse(
            main._animated_game_is_current(
                "42", old_game, session_id, generation
            )
        )

    async def test_stale_turn_does_not_send_a_new_visible_roll(self):
        game = {
            "chat_id": 77,
            "chat_type": "private",
            "dice_emoji": "🎲",
            "rolls": 1,
        }
        main.active_games["42"] = game
        session_id, generation = main._ensure_animated_session(game)
        main.active_games["42"] = {}

        with patch.object(main, "dealer_send_dice") as send_dice:
            with self.assertRaises(main.StaleAnimatedTurn):
                await main._send_bot_dice_values(
                    None,
                    game,
                    "42",
                    session_id=session_id,
                    generation=generation,
                )
        send_dice.assert_not_called()

    async def test_cancelled_current_turn_clears_persisted_busy_flag(self):
        game = {
            "chat_id": 77,
            "state": main.EmojiGameState.WAITING_BOT_ROLL,
            "bot_rolling_active": False,
        }
        main.active_games["42"] = game

        async def cancelled_roll(*args, **kwargs):
            raise asyncio.CancelledError

        with patch.object(main, "_send_bot_dice_values", cancelled_roll):
            with patch.object(main, "save_data"):
                with self.assertRaises(asyncio.CancelledError):
                    await main.handle_bot_roll_turn_by_id(
                        None,
                        "42",
                        77,
                    )

        self.assertFalse(game["bot_rolling_active"])


class DepositBindingSafetyTests(unittest.TestCase):
    def setUp(self):
        self.old_pending = main.nowpayments_pending_deposits
        self.old_processed = main.processed_payment_ids
        self.old_txids = main.processed_deposit_txids
        main.nowpayments_pending_deposits = {
            "pay-1": {
                "payment_id": "pay-1",
                "user_id": "123",
                "order_id": "dep_123_abc",
                "pay_address": "TExampleAddress",
                "coin": "USDTTRC20",
                "network": "TRON",
            }
        }
        main.processed_payment_ids = set()
        main.processed_deposit_txids = set()

    def tearDown(self):
        main.nowpayments_pending_deposits = self.old_pending
        main.processed_payment_ids = self.old_processed
        main.processed_deposit_txids = self.old_txids

    def _status(self, **changes):
        status = {
            "payment_id": "pay-1",
            "order_id": "dep_123_abc",
            "pay_address": "TExampleAddress",
            "pay_currency": "USDTTRC20",
            "network": "TRON",
        }
        status.update(changes)
        return status

    def test_matching_provider_fields_resolve_local_user(self):
        pending, error = main._validate_nowpayments_binding(
            "pay-1", self._status()
        )
        self.assertIsNotNone(pending)
        self.assertIsNone(error)
        self.assertEqual(pending["user_id"], "123")

    def test_mismatched_address_is_rejected(self):
        pending, error = main._validate_nowpayments_binding(
            "pay-1", self._status(pay_address="TWrongAddress")
        )
        self.assertIsNone(pending)
        self.assertEqual(error, "pay_address mismatch")

    def test_empty_id_cannot_enter_settlement(self):
        self.assertFalse(
            main._process_confirmed_deposit(
                user_id="123",
                usd_amount=10,
                credited_amount=9.5,
                fee_amount=0.5,
                pay_currency="USDT",
                payment_id="",
                source="test",
                txid="",
            )
        )

    def test_duplicate_payment_is_consumed_without_crediting(self):
        main.processed_payment_ids.add("pay-1")
        with patch.object(main, "ultra_secure_add_user_balance") as credit:
            self.assertTrue(
                main._process_confirmed_deposit(
                    user_id="123",
                    usd_amount=10,
                    credited_amount=9.5,
                    fee_amount=0.5,
                    pay_currency="USDT",
                    payment_id="pay-1",
                    source="test",
                )
            )
            credit.assert_not_called()

    def test_untrackable_payment_is_not_registered(self):
        with patch.object(main, "save_data") as save:
            self.assertFalse(
                main._track_nowpayments_pending_deposit(
                    {
                        "payment_id": "",
                        "pay_address": "TExampleAddress",
                    },
                    user_id="123",
                    crypto="usdttrc20",
                )
            )
        self.assertNotIn("", main.nowpayments_pending_deposits)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()