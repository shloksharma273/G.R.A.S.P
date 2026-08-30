"""The result cache (FR-5) and the idempotency it delivers (Section 13, criterion 2)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from llm_disambiguator import disambiguate
from llm_disambiguator.cache import CACHE_VERSION, VerdictCache, content_key
from llm_disambiguator.model import Verdict

from . import fake_llm
from .test_disambiguator import edge


class ContentKeyTests(unittest.TestCase):
    def test_key_covers_model_primitive_state_and_description(self):
        base = content_key("m", "add_water", "pan_on_stove", "text")
        self.assertNotEqual(base, content_key("m2", "add_water", "pan_on_stove", "text"))
        self.assertNotEqual(base, content_key("m", "boil_water", "pan_on_stove", "text"))
        self.assertNotEqual(base, content_key("m", "add_water", "stove_on", "text"))
        self.assertNotEqual(base, content_key("m", "add_water", "pan_on_stove", "other"))

    def test_key_is_stable_across_reflowed_whitespace(self):
        self.assertEqual(
            content_key("m", "a", "b", "one  two\n three"),
            content_key("m", "a", "b", "one two three"),
        )

    def test_key_is_stable_across_runs(self):
        self.assertEqual(content_key("m", "a", "b", "c"), content_key("m", "a", "b", "c"))


class CacheFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "nested", "cache.json")

    def tearDown(self):
        for root, _dirs, files in os.walk(self.directory, topdown=False):
            for name in files:
                os.unlink(os.path.join(root, name))
            os.rmdir(root)

    def test_round_trips_a_verdict(self):
        cache = VerdictCache(self.path)
        cache.put("k", Verdict("requires", 0.9, "why", "llm", "m"))
        cache.save()

        reloaded = VerdictCache(self.path)
        verdict = reloaded.get("k")
        self.assertEqual(verdict.label, "requires")
        self.assertEqual(verdict.confidence, 0.9)
        self.assertEqual(verdict.model, "m")

    def test_creates_the_directory(self):
        cache = VerdictCache(self.path)
        cache.put("k", Verdict("produces", 0.8, "", "llm"))
        cache.save()
        self.assertTrue(os.path.exists(self.path))

    def test_missing_file_is_an_empty_cache(self):
        self.assertEqual(len(VerdictCache(os.path.join(self.directory, "absent.json"))), 0)

    def test_corrupt_file_degrades_to_empty_rather_than_failing(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")
        cache = VerdictCache(self.path)
        self.assertEqual(len(cache), 0)
        self.assertIsNone(cache.get("k"))

    def test_unreadable_entry_behaves_as_a_miss(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": CACHE_VERSION, "entries": {"k": {"label": "nonsense"}}}, handle)
        self.assertIsNone(VerdictCache(self.path).get("k"))

    def test_disabled_cache_is_a_no_op(self):
        cache = VerdictCache(self.path, enabled=False)
        cache.put("k", Verdict("requires", 1.0, "", "llm"))
        cache.save()
        self.assertFalse(os.path.exists(self.path))
        self.assertIsNone(cache.get("k"))

    def test_save_is_a_no_op_when_nothing_changed(self):
        VerdictCache(self.path).save()
        self.assertFalse(os.path.exists(self.path))


class IdempotencyTests(unittest.TestCase):
    """Section 13: a warm re-run issues zero calls and reproduces the output."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "cache.json")
        self.addCleanup(self._clean)
        # Silent text, so the pre-pass cannot short-circuit and the cache is
        # genuinely what prevents the second call.
        self.bundles = [
            edge(state=f"state_{n}", description=f"relates to state {n}", key=f"r{n}")
            for n in range(6)
        ]

    def _clean(self):
        for name in os.listdir(self.directory):
            os.unlink(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def _run(self, responder):
        config = fake_llm.config(LLM_CACHE="1", LLM_CACHE_PATH=self.path)
        provider = fake_llm.FakeProvider(config, responder)
        result = disambiguate(
            self.bundles, config, provider=provider, cache=VerdictCache(self.path, True)
        )
        return result, provider

    def test_cold_run_calls_then_warm_run_does_not(self):
        first, cold = self._run(fake_llm.always("produces"))
        self.assertGreater(cold.requests_made, 0)
        self.assertEqual(first.calls.cache_hits, 0)

        second, warm = self._run(fake_llm.failing("must not be called"))
        self.assertEqual(warm.requests_made, 0)
        self.assertEqual(second.calls.cache_hits, 6)
        self.assertEqual(len(second.stamped), 6)

    def test_warm_run_reproduces_the_same_labels(self):
        first, _ = self._run(fake_llm.always("requires", 0.91))
        second, _ = self._run(fake_llm.failing("must not be called"))

        def signature(result):
            return [(e.bundle.relation_key, e.edge_type, e.confidence) for e in result.stamped]

        self.assertEqual(signature(first), signature(second))

    def test_warm_run_marks_records_as_cached(self):
        self._run(fake_llm.always("produces"))
        second, _ = self._run(fake_llm.failing())
        self.assertTrue(all(edge_.from_cache for edge_ in second.stamped))

    def test_a_different_model_id_misses_the_cache(self):
        self._run(fake_llm.always("produces"))
        config = fake_llm.config(LLM_MODEL="test/model-2", LLM_CACHE_PATH=self.path)
        provider = fake_llm.FakeProvider(config, fake_llm.always("requires"))
        result = disambiguate(
            self.bundles, config, provider=provider, cache=VerdictCache(self.path, True)
        )
        self.assertGreater(provider.requests_made, 0)
        self.assertEqual(result.calls.cache_hits, 0)

    def test_failures_are_not_cached(self):
        self._run(fake_llm.failing())
        second, provider = self._run(fake_llm.always("produces"))
        self.assertGreater(provider.requests_made, 0)
        self.assertEqual(len(second.stamped), 6)


if __name__ == "__main__":
    unittest.main()
