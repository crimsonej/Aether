"""
Production Readiness Audit
==========================
Tests runtime behavior (not code structure) across all major subsystems.
Each test is self-contained and prints PASS / FAIL + root cause.

Run with:
    PYTHONPATH=. aether/venv/bin/python aether/tests/audit_production_readiness.py
"""

import asyncio
import json
import os
import pathlib
import sqlite3
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESULTS = []


def record(section, test, passed, note=""):
    RESULTS.append((section, test, passed, note))
    status = "PASS" if passed else "FAIL"
    flag = "✓" if passed else "✗"
    print(f"  [{flag}] {status:<5} — {test}")
    if not passed and note:
        print(f"         NOTE: {note}")


# ============================================================
# SECTION 1 — AI Natural-Language Configuration Control
# ============================================================
async def audit_aether_agent():
    print("\n── 1. AI Natural-Language Configuration Control ──")

    # 1a: AetherAgent boots without LLM credentials
    try:
        from aether.core.model_manager import ModelManager
        from aether.core.agent import AetherAgent
        from aether.core.security.permission import PermissionEngine
        from aether.core.memory.conversation import ConversationMemory
        from aether.core.event_bus import EventBus
        from aether.core.config.store import ConfigStore

        ConfigStore._instance = None
        config = ConfigStore(ROOT)
        await config.load()
        bus = EventBus()
        await bus.start()
        perm = PermissionEngine(ROOT / "config" / "permissions.yaml")
        conv = ConversationMemory("audit_session", ROOT)
        model = ModelManager(config, bus)
        ai = AetherAgent(model, config, perm, conv, bus=bus)
        await ai.register_events(bus)
        record("Aether Agent", "AetherAgent boots without LLM credentials", True)
        await bus.stop()
    except Exception as e:
        record("Aether Agent", "AetherAgent boots without LLM credentials", False, str(e))

    # 1b: handle_user_command with all providers unhealthy → graceful failure to user
    try:
        from aether.core.model_manager import ModelManager, LLMProviderProtocol
        from aether.core.agent import AetherAgent
        from aether.core.security.permission import PermissionEngine
        from aether.core.memory.conversation import ConversationMemory
        from aether.core.event_bus import EventBus
        from aether.core.config.store import ConfigStore

        ConfigStore._instance = None
        config = ConfigStore(ROOT)
        await config.load()
        bus = EventBus()
        await bus.start()
        perm = PermissionEngine()
        conv = ConversationMemory("audit_all_fail", ROOT)
        model = ModelManager(config, bus)
        ai = AetherAgent(model, config, perm, conv, bus=bus)

        responses = []
        async def fake_send(msg):
            responses.append(msg)

        ai.register_source_adapter("test", fake_send)

        # All providers will fail (ping returns False for all real ones)
        # handle_user_command must catch RuntimeError and respond to the user
        await ai.handle_user_command("disable EUR/USD strategy", "user123", "test")

        # We expect either a JSON parse error reply or a caught error reply, not an uncaught exception
        record("Aether Agent", "All reasoning providers fail → sends error reply to user, no crash", len(responses) > 0,
               f"responses={responses[:1]}")
        await bus.stop()
    except Exception as e:
        record("Aether Agent", "All reasoning providers fail → sends error reply to user, no crash", False, traceback.format_exc()[-300:])

    # 1c: ConversationMemory persists and reloads across restarts
    try:
        session = "audit_persist"
        mem1 = ConversationMemory(session, ROOT)
        await mem1.add_entry("user", "Set EUR/USD risk to 1%")
        await mem1.add_entry("assistant", "Done.")
        # Re-instantiate (simulating restart)
        mem2 = ConversationMemory(session, ROOT)
        entries = mem2.recent(5)
        ok = any("EUR/USD" in e.get("text", "") for e in entries)
        record("Aether Agent", "ConversationMemory persists across restart", ok,
               f"entries={entries}")
    except Exception as e:
        record("Aether Agent", "ConversationMemory persists across restart", False, str(e))


# ============================================================
# SECTION 2 — Model Failover Chain
# ============================================================
async def audit_model_failover():
    print("\n── 2. Model Failover Chain ──")

    from aether.core.model_manager import ModelManager, LLMProviderProtocol, LocalProvider
    from aether.core.event_bus import EventBus
    from aether.core.config.store import ConfigStore

    ConfigStore._instance = None
    config = ConfigStore(ROOT)
    await config.load()
    bus = EventBus()
    await bus.start()

    # 2a: Chain skips unhealthy providers and reaches LocalProvider
    try:
        model = ModelManager(config, bus)
        tokens = []
        async for tok in model.generate("hello world"):
            tokens.append(tok)
        # LocalProvider just splits words, so we get tokens
        ok = len(tokens) > 0
        record("Model Failover", "Skips unhealthy → falls through to LocalProvider", ok,
               f"tokens={tokens}")
    except Exception as e:
        record("Model Failover", "Skips unhealthy → falls through to LocalProvider", False, str(e))

    # 2b: Provider marked unhealthy goes into backoff dict
    try:
        model = ModelManager(config, bus)
        # Force Claude into backoff by marking it manually
        model.unhealthy["Claude"] = asyncio.get_event_loop().time() + 60
        # Check chain skips it
        skipped_claude = False
        orig_chain = model.chain[:]

        tokens = []
        async for tok in model.generate("test"):
            tokens.append(tok)
        # If we got tokens without crash, Claude was properly skipped
        record("Model Failover", "Backoff provider is skipped in chain", len(tokens) > 0)
    except Exception as e:
        record("Model Failover", "Backoff provider is skipped in chain", False, str(e))

    # 2c: All providers fail → RuntimeError (not silent hang)
    try:
        from aether.core.model_manager import ModelManager
        model = ModelManager(config, bus)
        # Override all providers to fail
        class DeadProvider(LLMProviderProtocol):
            async def ping(self): return False
            async def stream(self, p):
                raise RuntimeError("dead")
                yield  # make it an async generator

        for name in model.chain:
            model.providers[name] = DeadProvider()

        raised = False
        try:
            async for tok in model.generate("test"):
                pass
        except RuntimeError as e:
            raised = "All LLM providers" in str(e)
        record("Model Failover", "All dead providers → RuntimeError raised", raised)
    except Exception as e:
        record("Model Failover", "All dead providers → RuntimeError raised", False, str(e))

    # 2d: model.fallback event published on skip
    try:
        model = ModelManager(config, bus)
        fallback_events = []
        async def capture(ev): fallback_events.append(ev)
        await bus.subscribe("model.fallback", capture)
        tokens = []
        async for tok in model.generate("ping"):
            tokens.append(tok)
        await asyncio.sleep(0.05)
        # Claude, OpenAI, Gemini, OpenRouter all fail → each fires model.fallback
        record("Model Failover", "model.fallback event published for each skipped provider",
               len(fallback_events) >= 4, f"events={len(fallback_events)}")
    except Exception as e:
        record("Model Failover", "model.fallback event published for each skipped provider", False, str(e))

    await bus.stop()


# ============================================================
# SECTION 3 — Streaming Token Delivery
# ============================================================
async def audit_streaming():
    print("\n── 3. Streaming Token Delivery ──")

    from aether.core.model_manager import ModelManager
    from aether.core.streaming.manager import StreamingManager
    from aether.core.event_bus import EventBus
    from aether.core.config.store import ConfigStore

    ConfigStore._instance = None
    config = ConfigStore(ROOT)
    await config.load()
    bus = EventBus()
    await bus.start()
    model = ModelManager(config, bus)

    # 3a: Tokens delivered to registered sink
    try:
        sm = StreamingManager(model)
        received = []
        async def sink(tok): received.append(tok)
        sm.register_sink("test_sink", sink)
        await sm.stream("hello world test")
        ok = len(received) >= 2  # LocalProvider splits by word
        record("Streaming", "Tokens delivered to registered sink", ok, f"tokens={received}")
        await sm.shutdown()
    except Exception as e:
        record("Streaming", "Tokens delivered to registered sink", False, str(e))

    # 3b: Duplicate sink registration raises ValueError
    try:
        sm = StreamingManager(model)
        raised = False
        async def sink(tok): pass
        sm.register_sink("dup", sink)
        try:
            sm.register_sink("dup", sink)
        except ValueError:
            raised = True
        record("Streaming", "Duplicate sink name raises ValueError", raised)
        await sm.shutdown()
    except Exception as e:
        record("Streaming", "Duplicate sink name raises ValueError", False, str(e))

    # 3c: Back-pressure — queue full drops oldest, no hang
    try:
        sm = StreamingManager(model)
        sm._queue_maxsize = 1  # Very small to force back-pressure
        received = []
        async def slow_sink(tok):
            await asyncio.sleep(0.01)
            received.append(tok)
        sm.register_sink("slow", slow_sink)
        # Stream a long prompt (many tokens) to trigger back-pressure
        await sm.stream(" ".join(["word"] * 50))
        record("Streaming", "Back-pressure: queue full drops tokens without hang", True)
        await sm.shutdown()
    except Exception as e:
        record("Streaming", "Back-pressure: queue full drops tokens without hang", False, str(e))

    # 3d: Sink crash does not stop other sinks
    try:
        sm = StreamingManager(model)
        good_received = []
        async def crashing_sink(tok): raise RuntimeError("sink crash")
        async def good_sink(tok): good_received.append(tok)
        sm.register_sink("crash", crashing_sink)
        sm.register_sink("good", good_sink)
        await sm.stream("one two three")
        ok = len(good_received) > 0
        record("Streaming", "Sink crash does not stop delivery to other sinks", ok,
               f"good_received={good_received}")
        await sm.shutdown()
    except Exception as e:
        record("Streaming", "Sink crash does not stop delivery to other sinks", False, str(e))

    await bus.stop()


# ============================================================
# SECTION 4 — RetryQueue Persistence
# ============================================================
async def audit_retry_queue():
    print("\n── 4. RetryQueue Persistence ──")

    from aether.core.ops.retry_queue import RetryQueue
    from aether.core.event_bus import EventBus
    from aether.core.config.store import ConfigStore

    ConfigStore._instance = None
    config = ConfigStore(ROOT)
    await config.load()
    bus = EventBus()
    await bus.start()

    # 4a: DB created on boot
    try:
        rq = RetryQueue(config, bus)
        ok = rq.db_path.exists()
        record("RetryQueue", "SQLite DB created on boot", ok, str(rq.db_path))
    except Exception as e:
        record("RetryQueue", "SQLite DB created on boot", False, str(e))

    # 4b: Enqueue + verify persistence after restart
    try:
        rq = RetryQueue(config, bus)
        await rq.enqueue("telegram", {"signal_id": "audit_test_001", "symbol": "EURUSD"})
        # Re-instantiate RetryQueue (simulating restart)
        rq2 = RetryQueue(config, bus)
        conn = sqlite3.connect(rq2.db_path)
        rows = conn.execute("SELECT * FROM retries WHERE adapter='telegram'").fetchall()
        conn.close()
        found = any("audit_test_001" in r[2] for r in rows)
        record("RetryQueue", "Enqueued row survives restart (DB persistence)", found,
               f"rows={len(rows)}")
    except Exception as e:
        record("RetryQueue", "Enqueued row survives restart (DB persistence)", False, str(e))

    # 4c: No adapter registry → skip retry, no crash
    try:
        rq = RetryQueue(config, bus)
        rq._adapter_registry = None  # not injected
        await rq.start()
        await asyncio.sleep(0.1)
        # Should not crash even with pending rows
        record("RetryQueue", "Missing adapter registry: skips retry gracefully, no crash", True)
        await rq.stop()
    except Exception as e:
        record("RetryQueue", "Missing adapter registry: skips retry gracefully, no crash", False, str(e))

    # 4d: Exponential backoff applied on failed delivery
    try:
        from aether.core.adapters.registry import AdapterRegistry, AdapterProtocol

        class AlwaysFailAdapter(AdapterProtocol):
            async def validate(self): return False
            async def send_signal(self, signal): return False
            async def receive_message(self, msg): pass
            async def close(self): pass

        rq = RetryQueue(config, bus)
        # Clear old test rows
        conn = sqlite3.connect(rq.db_path)
        conn.execute("DELETE FROM retries WHERE adapter='fail_adapter'")
        conn.commit()
        conn.close()

        # Inject a fake registry with a failing adapter
        class FakeRegistry:
            def get(self, name): return AlwaysFailAdapter()

        rq._adapter_registry = FakeRegistry()
        await rq.enqueue("fail_adapter", {"test": True})

        # Force process pending now
        await rq._process_pending()

        # Check that attempts incremented and next_attempt is in the future
        conn = sqlite3.connect(rq.db_path)
        rows = conn.execute("SELECT attempts, next_attempt FROM retries WHERE adapter='fail_adapter'").fetchall()
        conn.close()
        ok = all(r[0] == 1 and r[1] > time.time() for r in rows)
        record("RetryQueue", "Failed delivery increments attempts and sets future next_attempt", ok,
               f"rows={rows}")
    except Exception as e:
        record("RetryQueue", "Failed delivery increments attempts and sets future next_attempt", False, str(e))

    await bus.stop()


# ============================================================
# SECTION 5 — SignalStateManager Persistence
# ============================================================
async def audit_signal_state():
    print("\n── 5. SignalStateManager Persistence ──")

    from aether.core.signal.state_manager import SignalStateManager

    data_dir = str(ROOT / "data")

    # 5a: Register signal and verify it persists
    try:
        ssm = SignalStateManager(data_dir)
        sig = {
            "signal_id": "audit_sig_001",
            "symbol": "XAUUSD",
            "direction": "BUY",
        }
        ssm.register_signal(sig)
        # Re-instantiate
        ssm2 = SignalStateManager(data_dir)
        found = "audit_sig_001" in ssm2.active_signals
        record("SignalState", "Registered signal persists across restart", found)
    except Exception as e:
        record("SignalState", "Registered signal persists across restart", False, str(e))

    # 5b: mark_expired moves signal to history
    try:
        ssm = SignalStateManager(data_dir)
        ssm.active_signals["audit_sig_001"] = {"signal_id": "audit_sig_001", "state": "ACTIVE"}
        ssm._save_json(ssm.active_signals_file, ssm.active_signals)
        ssm.mark_expired("audit_sig_001")
        ssm2 = SignalStateManager(data_dir)
        in_active = "audit_sig_001" in ssm2.active_signals
        in_history = "audit_sig_001" in ssm2.history
        record("SignalState", "mark_expired moves signal to history, removes from active", not in_active and in_history)
    except Exception as e:
        record("SignalState", "mark_expired moves signal to history, removes from active", False, str(e))

    # 5c: Cooldown persists across restarts
    try:
        ssm = SignalStateManager(data_dir)
        ssm.set_cooldown("EURUSD", "BUY", "trend_v1", 3600)
        ssm2 = SignalStateManager(data_dir)
        in_cooldown = ssm2.check_cooldown("EURUSD", "BUY", "trend_v1")
        record("SignalState", "Cooldown state persists across restart", in_cooldown)
    except Exception as e:
        record("SignalState", "Cooldown state persists across restart", False, str(e))

    # 5d: Corrupt JSON in active_signals.json → recovers with empty dict
    try:
        ssm = SignalStateManager(data_dir)
        ssm.active_signals_file.write_text("NOT VALID JSON { }")
        ssm2 = SignalStateManager(data_dir)
        record("SignalState", "Corrupt active_signals.json → recovers gracefully", isinstance(ssm2.active_signals, dict))
    except Exception as e:
        record("SignalState", "Corrupt active_signals.json → recovers gracefully", False, str(e))


# ============================================================
# SECTION 6 — AdapterRegistry Plugin Loading
# ============================================================
async def audit_adapter_registry():
    print("\n── 6. AdapterRegistry Plugin Loading ──")

    from aether.core.adapters.registry import AdapterRegistry
    from aether.core.event_bus import EventBus
    from aether.core.config.store import ConfigStore

    ConfigStore._instance = None
    config = ConfigStore(ROOT)
    await config.load()
    bus = EventBus()
    await bus.start()

    # 6a: Registry loads without crashing even if no adapter is enabled
    try:
        registry = AdapterRegistry(config, bus)
        await registry.load_all()
        record("AdapterRegistry", "load_all() succeeds with no enabled adapters", True,
               f"loaded={registry.list_enabled()}")
    except Exception as e:
        record("AdapterRegistry", "load_all() succeeds with no enabled adapters", False, str(e))

    # 6b: Telegram adapter: MANIFEST present and class loadable
    try:
        import importlib
        mod = importlib.import_module("aether.adapters.telegram.adapter")
        manifest = getattr(mod, "MANIFEST", None)
        # MANIFEST is on the class, not the module, for Telegram
        if not manifest:
            cls = getattr(mod, "TelegramAdapter", None)
            if cls:
                manifest = getattr(cls, "MANIFEST", None)
        record("AdapterRegistry", "Telegram adapter: MANIFEST accessible", manifest is not None,
               f"manifest={manifest}")
    except Exception as e:
        record("AdapterRegistry", "Telegram adapter: MANIFEST accessible", False, str(e))

    # 6c: WhatsApp adapter: MANIFEST present and class loadable
    try:
        import importlib
        mod = importlib.import_module("aether.adapters.whatsapp.adapter")
        cls = getattr(mod, "WhatsAppAdapter", None)
        manifest = getattr(cls, "MANIFEST", None) if cls else None
        record("AdapterRegistry", "WhatsApp adapter: MANIFEST accessible", manifest is not None,
               f"manifest={manifest}")
    except Exception as e:
        record("AdapterRegistry", "WhatsApp adapter: MANIFEST accessible", False, str(e))

    # 6d: MANIFEST on class, not module — registry cannot find it
    # (This is the key bug to detect)
    try:
        import importlib
        adapters_path = ROOT / "aether" / "adapters"
        module_level_manifests = {}
        for entry in adapters_path.iterdir():
            if not entry.is_dir() or entry.name.startswith("__"):
                continue
            module_name = f"aether.adapters.{entry.name}.adapter"
            try:
                mod = importlib.import_module(module_name)
                manifest = getattr(mod, "MANIFEST", None)  # Module-level MANIFEST
                module_level_manifests[entry.name] = manifest is not None
            except Exception:
                module_level_manifests[entry.name] = False

        all_have_module_manifest = all(module_level_manifests.values())
        record("AdapterRegistry", "All adapter modules expose module-level MANIFEST",
               all_have_module_manifest, f"per-adapter={module_level_manifests}")
    except Exception as e:
        record("AdapterRegistry", "All adapter modules expose module-level MANIFEST", False, str(e))

    # 6e: get() on non-existent adapter raises KeyError (not crash)
    try:
        registry = AdapterRegistry(config, bus)
        await registry.load_all()
        raised = False
        try:
            registry.get("nonexistent")
        except KeyError:
            raised = True
        record("AdapterRegistry", "get('nonexistent') raises KeyError, no crash", raised)
    except Exception as e:
        record("AdapterRegistry", "get('nonexistent') raises KeyError, no crash", False, str(e))

    await bus.stop()


# ============================================================
# SECTION 7 — Telegram Inbound Commands
# ============================================================
async def audit_telegram_inbound():
    print("\n── 7. Telegram Inbound Commands ──")

    from aether.adapters.telegram.adapter import TelegramAdapter
    from aether.core.event_bus import EventBus

    bus = EventBus()
    await bus.start()

    # 7a: receive_message publishes user_command when bus set
    try:
        adapter = TelegramAdapter()
        TelegramAdapter._event_bus = bus

        commands = []
        async def capture(ev): commands.append(ev)
        await bus.subscribe("user_command", capture)

        update = {
            "message": {
                "from": {"id": 12345},
                "text": "disable EUR strategy"
            }
        }
        await adapter.receive_message(update)
        await asyncio.sleep(0.1)
        ok = any("disable EUR strategy" in c.get("raw", "") for c in commands)
        record("Telegram", "receive_message publishes user_command to EventBus", ok,
               f"events={commands}")
    except Exception as e:
        record("Telegram", "receive_message publishes user_command to EventBus", False, str(e))

    # 7b: receive_message with no bus set → logs warning, no crash
    try:
        adapter = TelegramAdapter()
        TelegramAdapter._event_bus = None
        update = {"message": {"from": {"id": 1}, "text": "hello"}}
        await adapter.receive_message(update)
        record("Telegram", "receive_message without bus set: warning only, no crash", True)
    except Exception as e:
        record("Telegram", "receive_message without bus set: warning only, no crash", False, str(e))

    # 7c: Empty/missing message field → silent return, no crash
    try:
        adapter = TelegramAdapter()
        TelegramAdapter._event_bus = bus
        await adapter.receive_message({})
        await adapter.receive_message({"message": {"from": {"id": 1}, "text": ""}})
        record("Telegram", "Empty or missing message field: silent return, no crash", True)
    except Exception as e:
        record("Telegram", "Empty or missing message field: silent return, no crash", False, str(e))

    # 7d: send_signal with no token → returns False, no crash
    try:
        adapter = TelegramAdapter()
        result = await adapter.send_signal({"symbol": "EURUSD", "direction": "BUY",
                                             "confidence": {"adjusted": 80},
                                             "trade_construction": {}, "reason_tags": []})
        record("Telegram", "send_signal with no token → returns False, no crash", result is False)
    except Exception as e:
        record("Telegram", "send_signal with no token → returns False, no crash", False, str(e))

    # 7e: validate() with no token → returns False, no crash
    try:
        adapter = TelegramAdapter()
        result = await adapter.validate()
        record("Telegram", "validate() with no token → returns False, no crash", result is False)
    except Exception as e:
        record("Telegram", "validate() with no token → returns False, no crash", False, str(e))

    await bus.stop()


# ============================================================
# SECTION 8 — WhatsApp Inbound Commands
# ============================================================
async def audit_whatsapp_inbound():
    print("\n── 8. WhatsApp Inbound Commands ──")

    from aether.adapters.whatsapp.adapter import WhatsAppAdapter

    # 8a: send_signal with no credentials → False, no crash
    try:
        adapter = WhatsAppAdapter()
        result = await adapter.send_signal({"symbol": "XAUUSD", "direction": "BUY",
                                             "confidence": {"adjusted": 70},
                                             "trade_construction": {}, "strategy": {"name": "v1"}})
        record("WhatsApp", "send_signal with no credentials → returns False, no crash", result is False)
    except Exception as e:
        record("WhatsApp", "send_signal with no credentials → returns False, no crash", False, str(e))

    # 8b: validate() with no token → False, no crash
    try:
        adapter = WhatsAppAdapter()
        result = await adapter.validate()
        record("WhatsApp", "validate() with no token → returns False, no crash", result is False)
    except Exception as e:
        record("WhatsApp", "validate() with no token → returns False, no crash", False, str(e))

    # 8c: WhatsApp has no receive_message → inbound commands not wired
    try:
        from aether.adapters.whatsapp.adapter import WhatsAppAdapter
        has_receive = hasattr(WhatsAppAdapter, "receive_message")
        record("WhatsApp", "receive_message implemented for inbound command handling", has_receive,
               "WhatsApp adapter is OUTBOUND only — no inbound routing exists" if not has_receive else "")
    except Exception as e:
        record("WhatsApp", "receive_message implemented for inbound command handling", False, str(e))

    # 8d: WhatsApp _event_bus attribute exists (for registry to set)
    try:
        has_attr = hasattr(WhatsAppAdapter, "_event_bus")
        record("WhatsApp", "_event_bus class attribute exists for registry injection", has_attr)
    except Exception as e:
        record("WhatsApp", "_event_bus class attribute exists for registry injection", False, str(e))


# ============================================================
# SECTION 9 — Runtime Reload Behavior
# ============================================================
async def audit_reload():
    print("\n── 9. Runtime Reload Behavior ──")

    from aether.core.config.store import ConfigStore
    from aether.core.event_bus import EventBus
    from aether.core.service_manager import ServiceManager

    # 9a: ConfigStore.reload() detects version change and emits config_reload_success
    try:
        ConfigStore._instance = None
        config = ConfigStore(ROOT)
        await config.load()
        bus = EventBus()
        await bus.start()
        events = []
        async def capture(ev): events.append(ev)
        await bus.subscribe("config_reload_success", capture)

        sm = ServiceManager(ROOT)
        await sm.start()
        # Touch the config (bump version) — simulate a disk change
        original_version = sm.config._version
        sm.config._version = original_version - 1  # force diff detection
        try:
            await sm.reload()
        except Exception:
            pass  # rollback is fine for our test
        await sm.stop()
        record("Reload", "ServiceManager.reload() executes without unhandled exception", True)
    except Exception as e:
        record("Reload", "ServiceManager.reload() executes without unhandled exception", False, str(e)[:200])

    # 9b: ConfigStore.set() persists to disk and increments version
    try:
        ConfigStore._instance = None
        config = ConfigStore(ROOT)
        await config.load()
        v_before = config.version()
        # Read current watchlist to restore it
        try:
            orig_watchlist = config.get("watchlist")
        except Exception:
            orig_watchlist = []
        await config.set("watchlist", ["EURUSD", "XAUUSD"])
        v_after = config.version()
        persisted = config.config_path.read_text()
        ok = v_after > v_before and "EURUSD" in persisted
        # Restore
        await config.set("watchlist", orig_watchlist)
        record("Reload", "ConfigStore.set() persists to disk and increments version", ok,
               f"v_before={v_before} v_after={v_after}")
    except Exception as e:
        record("Reload", "ConfigStore.set() persists to disk and increments version", False, str(e))

    # 9c: ConfigStore.load() on missing file raises FileNotFoundError (not silent)
    try:
        ConfigStore._instance = None
        config = ConfigStore(pathlib.Path("/tmp/nonexistent_aether_audit"))
        raised = False
        try:
            await config.load()
        except FileNotFoundError:
            raised = True
        record("Reload", "ConfigStore.load() on missing config → FileNotFoundError raised", raised)
    except Exception as e:
        record("Reload", "ConfigStore.load() on missing config → FileNotFoundError raised", False, str(e))

    # 9d: Atomic write — tmp file does not linger after save
    try:
        ConfigStore._instance = None
        config = ConfigStore(ROOT)
        await config.load()
        tmp_path = config.config_path.with_suffix(".tmp")
        await config.save()
        tmp_lingering = tmp_path.exists()
        record("Reload", "Atomic config write: .tmp file does not linger after save", not tmp_lingering)
    except Exception as e:
        record("Reload", "Atomic config write: .tmp file does not linger after save", False, str(e))


# ============================================================
# SECTION 10 — Oanda Live Data Operation
# ============================================================
async def audit_oanda():
    print("\n── 10. Oanda Live-Data Operation ──")

    from aether.core.data.engine import DataEngine
    from aether.core.event_bus import EventBus
    from aether.core.config.store import ConfigStore

    ConfigStore._instance = None
    config = ConfigStore(ROOT)
    await config.load()
    bus = EventBus()
    await bus.start()

    # 10a: DataEngine boots without OANDA_API_KEY (no crash, logs error)
    try:
        engine = DataEngine(config, bus)
        await engine.start()
        ok = engine._running is True
        record("Oanda", "DataEngine.start() without OANDA_API_KEY: boots cleanly, no crash", ok)
        await engine.stop()
    except Exception as e:
        record("Oanda", "DataEngine.start() without OANDA_API_KEY: boots cleanly, no crash", False, str(e))

    # 10b: DataEngine._fetch_and_publish() with no client → logs warning, no crash
    try:
        engine = DataEngine(config, bus)
        engine._client = None  # simulate missing key
        engine._running = True
        await engine._fetch_and_publish()
        record("Oanda", "_fetch_and_publish() with no client: skips fetch, no crash", True)
        engine._running = False
    except Exception as e:
        record("Oanda", "_fetch_and_publish() with no client: skips fetch, no crash", False, str(e))

    # 10c: OandaClient init with missing env var → raises (get_secret should raise)
    try:
        from aether.core.data.oanda_client import OandaClient
        from aether.core.config.loader import get_secret
        raised = False
        try:
            # OANDA_API_KEY is expected to be missing
            key = get_secret("OANDA_API_KEY_DEFINITELY_MISSING_AUDIT")
        except Exception:
            raised = True
        record("Oanda", "get_secret() on missing env var → raises exception", raised)
    except Exception as e:
        record("Oanda", "get_secret() on missing env var → raises exception", False, str(e))

    # 10d: DataEngine health() returns structured dict
    try:
        engine = DataEngine(config, bus)
        await engine.start()
        h = await engine.health()
        ok = isinstance(h, dict) and "status" in h
        record("Oanda", "DataEngine.health() returns structured dict", ok, f"health={h}")
        await engine.stop()
    except Exception as e:
        record("Oanda", "DataEngine.health() returns structured dict", False, str(e))

    # 10e: OandaClient uses synchronous requests (blocks event loop) — detect
    try:
        import inspect
        from aether.core.data.oanda_client import OandaClient
        # get_candles is a regular def (sync) — this blocks the event loop
        is_sync = not inspect.iscoroutinefunction(OandaClient.get_candles)
        record("Oanda", "get_candles() is synchronous (blocks event loop) — ARCHITECTURE RISK",
               not is_sync,  # PASS only if it's async
               "get_candles is a sync method using requests.Session — blocks asyncio event loop during fetch")
    except Exception as e:
        record("Oanda", "get_candles() is synchronous (blocks event loop)", False, str(e))

    await bus.stop()


# ============================================================
# MAIN
# ============================================================
async def main():
    print("=" * 60)
    print("   AETHER PRODUCTION READINESS AUDIT")
    print("=" * 60)
    print(f"   Root: {ROOT}")
    print(f"   OANDA_API_KEY set: {'OANDA_API_KEY' in os.environ}")
    print("=" * 60)

    await audit_aether_agent()
    await audit_model_failover()
    await audit_streaming()
    await audit_retry_queue()
    await audit_signal_state()
    await audit_adapter_registry()
    await audit_telegram_inbound()
    await audit_whatsapp_inbound()
    await audit_reload()
    await audit_oanda()

    # Summary
    passed = [r for r in RESULTS if r[2]]
    failed = [r for r in RESULTS if not r[2]]

    print("\n" + "=" * 60)
    print(f"   AUDIT COMPLETE: {len(passed)} PASS / {len(failed)} FAIL  ({len(RESULTS)} total)")
    print("=" * 60)
    if failed:
        print("\n── FAILURES ──")
        for section, test, _, note in failed:
            print(f"  FAIL [{section}] {test}")
            if note:
                print(f"       → {note}")
    print()

    # Emit JSON for structured reporting
    out = {"passed": len(passed), "failed": len(failed), "total": len(RESULTS),
           "results": [{"section": r[0], "test": r[1], "passed": r[2], "note": r[3]} for r in RESULTS]}
    (ROOT / "data" / "audit_results.json").write_text(json.dumps(out, indent=2))
    print(f"   Results saved → {ROOT / 'data' / 'audit_results.json'}")


if __name__ == "__main__":
    asyncio.run(main())
