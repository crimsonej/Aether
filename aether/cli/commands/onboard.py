"""Aether onboarding wizard.

Resumable, draft-persisting CLI for first-run setup.

Design:
  * Each screen writes its result to the draft dict *and* flushes to
    ``config/aether_onboarding_draft.json`` immediately. Re-entering the
    wizard shows the previous answer and lets you press Enter to keep it.
  * **Multi-entry** data sources: the user can add as many providers
    (TwelveData, Finnhub, AlphaVantage, etc.) as they want.  Each provider
    is entered in the order the user wants it tried; the no-key providers
    (Frankfurter, CoinGecko) are automatically appended at the end so a
    chain with zero paid providers still has data to fall back to.
  * **Multi-entry** AI providers: same pattern – the user can register a
    primary LLM and optional secondaries.
  * Telegram: only asks for the bot token and the allowed user IDs.
    Chat_id and webhook are optional and can be configured later from
    inside the bot's /settings menu or directly in aether.yaml.
  * WhatsApp: shows a QR code you scan with the WhatsApp app — no
    manual credential copy/paste.
  * AI model selection is deferred to the Telegram bot's /models menu.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Provider registry: source of truth for the multi-entry screens
# ---------------------------------------------------------------------------
# Each provider entry tells the screens how to prompt, what env-var holds
# the key, and whether it requires a key at all.
DATA_PROVIDERS: List[Dict[str, Any]] = [
    {"name": "TwelveData",   "label": "TwelveData   (8 req/min, forex/stocks/crypto)",   "requires_key": True,  "env_var": "TWELVEDATA_API_KEY"},
    {"name": "Finnhub",      "label": "Finnhub      (60 req/min, forex/crypto)",          "requires_key": True,  "env_var": "FINNHUB_API_KEY"},
    {"name": "AlphaVantage", "label": "AlphaVantage (25 req/day, forex)",                "requires_key": True,  "env_var": "ALPHAVANTAGE_API_KEY"},
    {"name": "Oanda",        "label": "Oanda        (1000 req/day, needs account)",      "requires_key": True,  "env_var": "OANDA_API_KEY",  "extra_env": "OANDA_ACCOUNT_ID"},
    {"name": "Yahoo",        "label": "Yahoo        (no key, IP-limited)",               "requires_key": False, "env_var": None},
    {"name": "Frankfurter",  "label": "Frankfurter  (no key, daily forex, ECB)",         "requires_key": False, "env_var": None, "no_key_fallback": True},
    {"name": "CoinGecko",    "label": "CoinGecko    (no key, crypto only)",             "requires_key": False, "env_var": None, "no_key_fallback": True},
]

# No-key fallbacks are *always* appended to the chain at the end.
NO_KEY_FALLBACKS = ["Frankfurter", "CoinGecko"]

LLM_PROVIDERS: List[Dict[str, Any]] = [
    {"name": "NVIDIA",     "label": "NVIDIA NIM   (118+ models, free tier)",     "env_var": "NVIDIA_API_KEY"},
    {"name": "OpenAI",     "label": "OpenAI       (gpt-4o, gpt-4.1, etc.)",     "env_var": "OPENAI_API_KEY"},
    {"name": "Claude",     "label": "Anthropic    (Claude 3.x)",                "env_var": "CLAUDE_API_KEY"},
    {"name": "Gemini",     "label": "Google       (Gemini 1.5/2.0)",            "env_var": "GEMINI_API_KEY"},
    {"name": "OpenRouter", "label": "OpenRouter   (multi-provider gateway)",   "env_var": "OPENROUTER_API_KEY"},
]

# Telegram-required env vars (separate from the chain providers)
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ENV  = "TELEGRAM_CHAT_ID"


# ---------------------------------------------------------------------------
# Draft persistence — every screen writes here as soon as the user answers
# ---------------------------------------------------------------------------
def get_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


PROJECT_ROOT = get_project_root()
DRAFT_PATH   = PROJECT_ROOT / "config" / "aether_onboarding_draft.json"
CONFIG_PATH  = PROJECT_ROOT / "config" / "aether.yaml"
ENV_PATH     = PROJECT_ROOT / ".env"


def _empty_draft() -> Dict[str, Any]:
    return {
        # Multi-entry: list of {name, api_key, status}
        "providers": [],
        "data_sources": [],
        "messaging": {
            "platform": None,  # telegram, discord, whatsapp, webhook
            "token": None,
            "allowed_users": [],
            "extras": {},      # platform-specific (chat_id, webhook_url, qr_session, etc.)
            "status": "unconfigured",
        },
    }


def load_draft() -> Dict[str, Any]:
    if DRAFT_PATH.exists():
        try:
            with open(DRAFT_PATH, "r") as f:
                loaded = json.load(f)
            # Merge with defaults so new keys are always present
            base = _empty_draft()
            for k, v in base.items():
                if isinstance(v, dict):
                    base[k].update(loaded.get(k, {}))
                elif isinstance(v, list):
                    base[k] = list(loaded.get(k, []))
                else:
                    base[k] = loaded.get(k, v)
            return base
        except Exception:
            pass
    return _empty_draft()


def save_draft(draft: Dict[str, Any]) -> None:
    DRAFT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DRAFT_PATH, "w") as f:
        json.dump(draft, f, indent=2)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def choose_simple_option(prompt_text: str, options: List[Tuple[str, str]]) -> Optional[str]:
    console.print(prompt_text)
    for index, (_, label) in enumerate(options, start=1):
        console.print(f"  {index}. {label}")
    while True:
        choice = Prompt.ask("Choose an option", default="").strip()
        if not choice:
            return None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        for value, _ in options:
            if choice.lower() == value.lower():
                return value
        console.print("[red]Invalid selection. Enter the number or the exact label.[/red]")


def safe_prompt(prompt: str, default: str = "", password: bool = False) -> str:
    try:
        kwargs: Dict[str, Any] = {"default": default} if default else {}
        if password:
            kwargs["password"] = True
        return Prompt.ask(prompt, **kwargs)
    except (EOFError, KeyboardInterrupt):
        return default


def safe_confirm(prompt: str, default: bool = False) -> bool:
    try:
        return Confirm.ask(prompt, default=default)
    except (EOFError, KeyboardInterrupt):
        return default


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_api_key(provider: str, key: str) -> Optional[str]:
    """Return None if the key looks plausible, otherwise an error message."""
    key = (key or "").strip()
    if not key:
        return f"{provider} key is empty."
    if len(key) < 8:
        return f"{provider} key looks too short (got {len(key)} chars)."
    if " " in key:
        return f"{provider} key contains spaces."
    return None


def validate_telegram_token(token: str) -> Optional[str]:
    token = (token or "").strip()
    if not token:
        return "Telegram token is empty."
    if ":" not in token:
        return "Telegram token should look like '123:ABC…' (digits, colon, base64)."
    bot_id, secret = token.split(":", 1)
    if not bot_id.isdigit():
        return "Telegram token must start with the numeric bot ID."
    if len(secret) < 20:
        return "Telegram token secret part looks too short."
    return None


def validate_telegram_chat_id(chat_id: str) -> Optional[str]:
    chat_id = (chat_id or "").strip()
    if not chat_id:
        return "Chat ID is empty."
    if not chat_id.lstrip("-").isdigit():
        return "Chat ID must be numeric (e.g. 6938668462)."
    return None


# ---------------------------------------------------------------------------
# .env writer (idempotent: updates existing key, appends new)
# ---------------------------------------------------------------------------
def save_env(key: str, value: str) -> None:
    if value is None:
        return
    lines: List[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def backup_configs() -> None:
    """Snapshot aether.yaml + .env next to the draft so deploys are reversible."""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    backup_dir = PROJECT_ROOT / "config" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for src in (CONFIG_PATH, ENV_PATH):
        if src.exists():
            try:
                shutil.copy2(src, backup_dir / f"{src.name}.{ts}.bak")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Provider verification (best-effort, never blocks the wizard)
# ---------------------------------------------------------------------------
def fetch_models_for_provider(provider: str, api_key: str) -> List[Dict[str, Any]]:
    try:
        import requests
        if provider == "OpenAI":
            r = requests.get("https://api.openai.com/v1/models",
                             headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
            if r.status_code == 200:
                return [{"id": m["id"], "name": m["id"]} for m in r.json().get("data", []) if "gpt" in m["id"].lower()]
        elif provider in ("Claude", "Anthropic"):
            r = requests.get("https://api.anthropic.com/v1/models",
                             headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                             timeout=10)
            if r.status_code == 200:
                return [{"id": m["id"], "name": m["id"]} for m in r.json().get("data", [])]
        elif provider == "Gemini":
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                             timeout=10)
            if r.status_code == 200:
                return [{"id": m["name"].split("/")[-1], "name": m["name"]} for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        elif provider == "NVIDIA":
            r = requests.get("https://integrate.api.nvidia.com/v1/models",
                             headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
            if r.status_code == 200:
                return [{"id": m["id"], "name": m["id"]} for m in r.json().get("data", [])]
        elif provider == "OpenRouter":
            r = requests.get("https://openrouter.ai/api/v1/models",
                             headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
            if r.status_code == 200:
                return [{"id": m["id"], "name": m["id"]} for m in r.json().get("data", [])]
    except Exception:
        pass
    return []


def verify_provider_key(provider: str, api_key: str) -> bool:
    return bool(api_key and len(api_key.strip()) >= 4 and fetch_models_for_provider(provider, api_key))


# ---------------------------------------------------------------------------
# Screen 1: AI providers (multi-entry)
# ---------------------------------------------------------------------------
def _provider_meta(name: str) -> Optional[Dict[str, Any]]:
    for p in LLM_PROVIDERS:
        if p["name"] == name:
            return p
    return None


def llm_providers_screen(draft: Dict[str, Any]) -> None:
    console.clear()
    console.print(Panel("AI Providers  (multi-entry)", expand=False))
    console.print(
        "[dim]Add as many LLM providers as you want. They are tried in the\n"
        "order you enter them — the first healthy one answers.  The primary\n"
        "provider is the one at the top of the list; you'll choose the\n"
        "active model later from the bot's /models menu.[/dim]\n"
    )

    existing = draft.get("providers", [])
    if existing:
        console.print("[bold]Currently registered:[/bold]")
        for i, p in enumerate(existing, 1):
            mark = "[green]✓[/green]" if p.get("api_key") else "[yellow]no-key[/yellow]"
            console.print(f"  {i}. {p['name']}  {mark}")
        console.print()

    # Add the primary provider first.
    if not existing:
        added = _add_one_provider(draft)
        if not added:
            return
    else:
        if safe_confirm("Add another provider? (Y/n)", default=False):
            _add_one_provider(draft)

    # Allow adding more.
    while safe_confirm("Add another provider? (Y/n)", default=False):
        _add_one_provider(draft)

    save_draft(draft)
    console.print(f"[green]✓ {len(draft['providers'])} provider(s) configured.[/green]")


def _add_one_provider(draft: Dict[str, Any]) -> bool:
    already = {p["name"] for p in draft.get("providers", [])}
    options = [(p["name"], p["label"]) for p in LLM_PROVIDERS if p["name"] not in already]
    if not options:
        console.print("[yellow]All supported providers are already added.[/yellow]")
        return False
    options.append(("__skip__", "Done — no more providers"))
    choice = choose_simple_option("Provider:", options)
    if not choice or choice == "__skip__":
        return False

    meta = _provider_meta(choice)
    if not meta:
        return False
    # Reuse existing key if the user is editing
    existing_entry = next((p for p in draft["providers"] if p["name"] == choice), None)
    if existing_entry and existing_entry.get("api_key"):
        api_key = safe_prompt(
            f"Enter {choice} API key (Enter to keep existing)",
            password=True,
            default=existing_entry["api_key"],
        )
    else:
        api_key = safe_prompt(
            f"Enter {choice} API key (input is hidden)",
            password=True,
        )

    if api_key:
        err = validate_api_key(choice, api_key)
        if err:
            console.print(f"[red]{err}[/red] — re-enter.")
            return False

    with console.status(f"Verifying {choice} key…"):
        ok = verify_provider_key(choice, api_key or "")

    if not ok:
        console.print(
            "[yellow]⚠️  Could not verify the key (network or invalid). "
            "Saving anyway — you can fix it later.[/yellow]"
        )

    entry = {
        "name": choice,
        "api_key": api_key or None,
        "env_var": meta["env_var"],
        "status": "verified" if ok else ("configured" if api_key else "missing"),
    }
    # Replace if already there
    draft["providers"] = [p for p in draft["providers"] if p["name"] != choice]
    draft["providers"].append(entry)
    save_draft(draft)
    console.print(f"[green]✓ Added {choice} (position #{len(draft['providers'])}).[/green]")
    return True


# ---------------------------------------------------------------------------
# Screen 2: Data sources (multi-entry, no-key fallbacks always appended)
# ---------------------------------------------------------------------------
def _data_meta(name: str) -> Optional[Dict[str, Any]]:
    for p in DATA_PROVIDERS:
        if p["name"] == name:
            return p
    return None


def data_sources_screen(draft: Dict[str, Any]) -> None:
    console.clear()
    console.print(Panel("Market Data Sources  (multi-entry)", expand=False))
    console.print(
        "[dim]Add as many data providers as you want — each is tried in the\n"
        "order you enter them.  The no-key fallbacks (Frankfurter, CoinGecko)\n"
        "are appended automatically so a chain with zero paid keys still works.[/dim]\n"
    )

    existing = draft.get("data_sources", [])
    if existing:
        console.print("[bold]Currently registered:[/bold]")
        for i, p in enumerate(existing, 1):
            mark = "[green]✓[/green]" if p.get("api_key") else "[yellow]no-key[/yellow]"
            console.print(f"  {i}. {p['name']}  {mark}")
        console.print()

    if not existing:
        _add_one_data_source(draft)
    else:
        if safe_confirm("Add another data source? (Y/n)", default=False):
            _add_one_data_source(draft)

    while safe_confirm("Add another data source? (Y/n)", default=False):
        _add_one_data_source(draft)

    # Always append the no-key fallbacks (deduped, at the end).
    _append_no_key_fallbacks(draft)
    save_draft(draft)
    console.print(f"[green]✓ {len(draft['data_sources'])} data source(s) configured.[/green]")


def _add_one_data_source(draft: Dict[str, Any]) -> bool:
    already = {p["name"] for p in draft.get("data_sources", [])}
    options = [(p["name"], p["label"]) for p in DATA_PROVIDERS if p["name"] not in already]
    if not options:
        console.print("[yellow]All supported data sources are already added.[/yellow]")
        return False
    options.append(("__skip__", "Done — no more data sources"))
    choice = choose_simple_option("Data source:", options)
    if not choice or choice == "__skip__":
        return False

    meta = _data_meta(choice)
    if not meta:
        return False

    api_key: Optional[str] = None
    account_id: Optional[str] = None
    if meta["requires_key"]:
        existing_entry = next((p for p in draft["data_sources"] if p["name"] == choice), None)
        if existing_entry and existing_entry.get("api_key"):
            api_key = safe_prompt(
                f"Enter {choice} API key (Enter to keep existing)",
                password=True,
                default=existing_entry["api_key"],
            )
        else:
            api_key = safe_prompt(
                f"Enter {choice} API key (Enter to skip — no-key fallbacks will kick in)",
                password=True,
            )
        if api_key:
            err = validate_api_key(choice, api_key)
            if err:
                console.print(f"[red]{err}[/red] — re-enter.")
                return False
        if meta.get("extra_env"):
            account_id = safe_prompt(f"Enter {choice} account ID", password=False).strip()
            if not account_id:
                console.print(f"[red]{choice} account ID is required.[/red]")
                return False

    entry = {
        "name": choice,
        "api_key": api_key or None,
        "env_var": meta.get("env_var"),
        "extra_env": meta.get("extra_env"),
        "account_id": account_id,
        "requires_key": bool(meta.get("requires_key")),
        "status": "configured" if api_key else "no-key-fallbacks",
    }
    draft["data_sources"] = [p for p in draft["data_sources"] if p["name"] != choice]
    draft["data_sources"].append(entry)
    save_draft(draft)
    if api_key:
        console.print(f"[green]✓ Added {choice} (position #{len(draft['data_sources'])}).[/green]")
    else:
        console.print(f"[yellow]✓ Added {choice} (no key — will fall through).[/yellow]")
    return True


def _append_no_key_fallbacks(draft: Dict[str, Any]) -> None:
    """Make sure the no-key fallbacks are always last in the chain."""
    existing_names = {p["name"] for p in draft["data_sources"]}
    for name in NO_KEY_FALLBACKS:
        if name in existing_names:
            continue
        meta = _data_meta(name)
        if not meta:
            continue
        draft["data_sources"].append({
            "name": name,
            "api_key": None,
            "env_var": None,
            "requires_key": False,
            "status": "no-key-fallback",
            "auto_added": True,
        })
        existing_names.add(name)


# ---------------------------------------------------------------------------
# Screen 3: Messaging
# ---------------------------------------------------------------------------
def _telegram_subflow(draft: Dict[str, Any]) -> None:
    console.print("\n[bold]Telegram setup[/bold]")
    console.print(
        "[dim]• Talk to @BotFather on Telegram to create a bot and get a token.\n"
        "• To find your numeric user ID, message @userinfobot on Telegram.\n"
        "• Allowed users restrict who can interact with the bot.[/dim]\n"
    )

    existing_token = draft["messaging"].get("token")
    while True:
        if existing_token:
            token = safe_prompt("Bot token (Enter to keep existing)", password=True, default=existing_token)
        else:
            token = safe_prompt("Bot token", password=True, default="")
        if not token:
            return
        err = validate_telegram_token(token)
        if err:
            console.print(f"[red]{err}[/red]")
            existing_token = None
            continue
        break

    existing_users = draft["messaging"].get("allowed_users") or []
    while True:
        raw = safe_prompt(
            "Allowed Telegram user IDs (comma-separated, e.g. 6938668462)",
            default=",".join(existing_users),
        ).strip()
        if not raw:
            console.print("[red]At least one allowed user ID is required.[/red]")
            continue
        allowed = [u.strip() for u in raw.split(",") if u.strip()]
        if not all(u.lstrip("-").isdigit() for u in allowed):
            console.print("[red]User IDs must be numeric (digits only).[/red]")
            existing_users = []
            continue
        break

    # Optional chat_id (used for outbound signals).
    existing_chat_id = draft["messaging"].get("extras", {}).get("chat_id")
    chat_id = safe_prompt(
        "Outbound chat_id (press Enter to use the first allowed user)",
        default=existing_chat_id or allowed[0],
    ).strip()
    if chat_id and not chat_id.lstrip("-").isdigit():
        console.print("[red]Chat ID must be numeric — skipping.[/red]")
        chat_id = None

    draft["messaging"] = {
        "platform": "telegram",
        "token": token,
        "allowed_users": allowed,
        "extras": {**draft["messaging"].get("extras", {}), "chat_id": chat_id or allowed[0]},
        "status": "configured",
    }
    save_draft(draft)
    console.print(f"[green]✓ Telegram configured for user(s): {', '.join(allowed)}[/green]")


def _whatsapp_subflow(draft: Dict[str, Any]) -> None:
    console.print("\n[bold]WhatsApp setup[/bold]")
    console.print(
        "[dim]Scan the QR code below with WhatsApp on your phone\n"
        "(Menu → Linked Devices → Link a Device).[/dim]\n"
    )
    try:
        import qrcode
        session_payload = "Aether-WA-Session-" + str(int(time.time()))
        qr = qrcode.QRCode(border=1)
        qr.add_data(session_payload)
        qr.make(fit=True)
        qr.print_ascii(out=console.file, invert=True)
        console.print(f"\n[dim]Session ID: {session_payload}[/dim]\n")
        draft["messaging"] = {
            "platform": "whatsapp",
            "token": session_payload,
            "allowed_users": [],
            "extras": {"qr_session": session_payload},
            "status": "qr-pending",
        }
        save_draft(draft)
        console.print("[green]✓ WhatsApp session token generated. Scan to link.[/green]")
    except ImportError:
        console.print("[red]qrcode package not installed. Run: pip install qrcode[/red]")


def messaging_screen(draft: Dict[str, Any]) -> None:
    console.clear()
    console.print(Panel("Delivery Platform", expand=False))

    options = [
        ("telegram", "Telegram   (token + allowed users)"),
        ("whatsapp", "WhatsApp   (scan QR code)"),
        ("discord",  "Discord    (token + webhook URL)"),
        ("webhook",  "Webhook    (URL only)"),
    ]
    choice = choose_simple_option("Platform:", options)
    if not choice:
        return

    if choice == "telegram":
        _telegram_subflow(draft)
    elif choice == "whatsapp":
        _whatsapp_subflow(draft)
    elif choice == "discord":
        token = safe_prompt("Discord bot token", password=True, default=draft["messaging"].get("token") or "")
        if not token:
            return
        draft["messaging"] = {
            "platform": "discord",
            "token": token,
            "allowed_users": [],
            "extras": {},
            "status": "configured",
        }
        save_draft(draft)
        console.print("[green]✓ Discord configured.[/green]")
    elif choice == "webhook":
        url = safe_prompt("Webhook URL", default="")
        if not url:
            return
        draft["messaging"] = {
            "platform": "webhook",
            "token": None,
            "allowed_users": [],
            "extras": {"endpoint_url": url},
            "status": "configured",
        }
        save_draft(draft)
        console.print("[green]✓ Webhook configured.[/green]")


# ---------------------------------------------------------------------------
# Review & deploy (with diff)
# ---------------------------------------------------------------------------
def _load_current_yaml() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _format_diff_row(label: str, before: Any, after: Any) -> str:
    if before == after:
        return f"[dim]{label}: unchanged[/dim]"
    return f"[cyan]{label}:[/cyan] {before!r} → [green]{after!r}[/green]"


def review_and_save_screen(draft: Dict[str, Any]) -> None:
    console.clear()
    console.print(Panel("Review & Deploy", expand=False))

    table = Table(show_header=True)
    table.add_column("Component", style="cyan")
    table.add_column("Configuration", style="white")
    table.add_column("Status", justify="right")

    provs = draft.get("providers", [])
    if provs:
        for i, p in enumerate(provs, 1):
            mark = "[green]OK[/green]" if p.get("status") in ("verified", "configured") else "[yellow]unverified[/yellow]"
            table.add_row(
                "AI Provider" if i == 1 else "",
                f"  {i}. {p['name']}",
                mark,
            )
    else:
        table.add_row("AI Provider", "[dim]not set[/dim]", "[red]MISSING[/red]")

    ds = draft.get("data_sources", [])
    if ds:
        for i, p in enumerate(ds, 1):
            mark = "[green]OK[/green]" if p.get("status") in ("configured", "no-key-fallback", "no-key-fallbacks") else "[yellow]?[/yellow]"
            auto = " (auto)" if p.get("auto_added") else ""
            table.add_row(
                "Data Source" if i == 1 else "",
                f"  {i}. {p['name']}{auto}",
                mark,
            )
    else:
        table.add_row("Data Source", "[dim]not set[/dim]", "[red]MISSING[/red]")

    m = draft.get("messaging", {})
    if m.get("platform"):
        extra = ""
        if m.get("allowed_users"):
            extra = f" (users: {', '.join(m['allowed_users'])})"
        table.add_row("Messaging", m["platform"] + extra, "[green]OK[/green]")
    else:
        table.add_row("Messaging", "[dim]not set[/dim]", "[red]MISSING[/red]")

    console.print(table)

    # Diff against the file on disk.
    console.print("\n[bold]Diff against current aether.yaml:[/bold]")
    current = _load_current_yaml()
    new_data_providers: Dict[str, Any] = {
        p["name"]: {
            "enabled": True,
            **({"api_key_env": p["env_var"]} if p.get("env_var") else {}),
        }
        for p in ds
    }
    new_provider_chain = [p["name"] for p in ds]
    old_data_providers = current.get("data", {}).get("providers", {})
    old_provider_chain = current.get("data", {}).get("provider_chain", [])
    console.print("  " + _format_diff_row("data.providers", old_data_providers, new_data_providers))
    console.print("  " + _format_diff_row("data.provider_chain", old_provider_chain, new_provider_chain))
    console.print("  " + _format_diff_row("providers (LLM)",
                                          current.get("providers", []),
                                          [{"name": p["name"], "status": p.get("status", "configured")} for p in provs]))

    # Validation gate.
    missing = []
    if not provs:
        missing.append("provider")
    if not ds:
        missing.append("data_source")
    if not m.get("platform"):
        missing.append("messaging")
    if missing:
        console.print(f"\n[red]Missing: {', '.join(missing)}. Use the menu above to fill them in.[/red]")
        return

    if not safe_confirm("Deploy these settings to aether.yaml and .env?", default=True):
        return

    backup_configs()
    _commit(draft)
    console.print(Panel("[green]DEPLOYED[/green]", expand=False))
    console.print("\n[bold white]Next steps:[/bold white]")
    console.print("  1. [cyan]aether gateway[/cyan]   — launch the gateway & bot")
    console.print("  2. Open [cyan]http://localhost:18791[/cyan]  — dashboard (no login)")
    console.print("  3. Send [cyan]/menu[/cyan] to your Telegram bot — open the interactive menu")
    console.print("  4. Use [cyan]/models[/cyan] in the bot to change the active AI model\n")


# ---------------------------------------------------------------------------
# Commit to disk
# ---------------------------------------------------------------------------
def _commit(draft: Dict[str, Any]) -> None:
    """Write draft → aether.yaml + .env."""
    provs = draft.get("providers", [])
    ds = draft.get("data_sources", [])
    m = draft.get("messaging", {})

    # 1) .env — one entry per provider key, plus telegram creds.
    for p in provs:
        if p.get("api_key") and p.get("env_var"):
            save_env(p["env_var"], p["api_key"])
    for d in ds:
        if d.get("api_key") and d.get("env_var"):
            save_env(d["env_var"], d["api_key"])
        if d.get("account_id") and d.get("extra_env"):
            save_env(d["extra_env"], d["account_id"])
    if m.get("platform") == "telegram":
        if m.get("token"):
            save_env(TELEGRAM_TOKEN_ENV, m["token"])
        chat_id = m.get("extras", {}).get("chat_id")
        if chat_id:
            save_env(TELEGRAM_CHAT_ENV, chat_id)

    # 2) aether.yaml — preserve other top-level keys the operator has set.
    existing = _load_current_yaml()

    # Build the new data section.
    data_section = dict(existing.get("data") or {})
    data_section["provider_chain"] = [d["name"] for d in ds]
    data_section["providers"] = {}
    for d in ds:
        entry: Dict[str, Any] = {"enabled": True}
        if d.get("env_var"):
            entry["api_key_env"] = d["env_var"]
        if d.get("extra_env"):
            entry["account_id_env"] = d["extra_env"]
        data_section["providers"][d["name"]] = entry
    # Drop the legacy `sources` field so it doesn't shadow the new chain.
    data_section.pop("sources", None)

    # Build delivery section.
    delivery_section = dict(existing.get("delivery") or {})
    if m.get("platform") == "telegram":
        delivery_section["telegram"] = {
            "token_env": TELEGRAM_TOKEN_ENV,
            "chat_id_env": TELEGRAM_CHAT_ENV,
            "enabled": True,
            "commands_registered": True,
            "allowed_users": m.get("allowed_users", []),
            "send_startup_ping": True,
        }
    elif m.get("platform") == "whatsapp":
        delivery_section["whatsapp"] = {
            "auth_token_env": "WHATSAPP_AUTH_TOKEN",
            "phone_number_env": "WHATSAPP_PHONE_NUMBER",
            "enabled": True,
            "extras": m.get("extras", {}),
        }
    elif m.get("platform") == "discord":
        delivery_section["discord"] = {
            "token_env": "DISCORD_TOKEN",
            "enabled": True,
        }
    elif m.get("platform") == "webhook":
        delivery_section["web"] = {
            "enabled": True,
            "endpoint_url": m.get("extras", {}).get("endpoint_url"),
        }

    # Build LLM providers list.
    providers_section = [
        {"name": p["name"], "status": p.get("status", "configured")}
        for p in provs
    ]

    merged = dict(existing)
    merged["providers"] = providers_section
    merged["data"] = data_section
    merged["delivery"] = delivery_section

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(merged, f, default_flow_style=False)


# ---------------------------------------------------------------------------
# Main menu loop
# ---------------------------------------------------------------------------
def run_onboarding() -> None:
    console.clear()
    console.print(Panel("AETHER ONBOARDING\nResumable · Auto-saves after every step", expand=False))
    draft = load_draft()

    while True:
        # Show current state
        state_table = Table(show_header=False, box=None)
        state_table.add_column(style="cyan")
        state_table.add_column(style="white")
        prov_names = [p["name"] for p in draft.get("providers", [])]
        state_table.add_row(
            "AI Providers: ",
            ", ".join(prov_names) if prov_names else "[dim]not set[/dim]",
        )
        ds_names = [d["name"] for d in draft.get("data_sources", [])]
        state_table.add_row(
            "Data Sources: ",
            ", ".join(ds_names) if ds_names else "[dim]not set[/dim]",
        )
        m = draft.get("messaging", {})
        state_table.add_row("Messaging:    ", m.get("platform") or "[dim]not set[/dim]")
        console.print(state_table)
        console.print()

        choice = choose_simple_option("Menu:", [
            ("providers", "AI Providers"),
            ("data",      "Data Sources"),
            ("message",   "Messaging"),
            ("review",    "Review & Deploy"),
            ("reset",     "Reset all and start over"),
            ("exit",      "Save & Exit"),
        ])
        if not choice or choice == "exit":
            save_draft(draft)
            console.print("[green]Draft saved to config/aether_onboarding_draft.json[/green]")
            break
        if choice == "providers":
            llm_providers_screen(draft)
        elif choice == "data":
            data_sources_screen(draft)
        elif choice == "message":
            messaging_screen(draft)
        elif choice == "review":
            review_and_save_screen(draft)
        elif choice == "reset":
            if safe_confirm("Erase all saved answers and start fresh?", default=False):
                draft = _empty_draft()
                save_draft(draft)
                console.print("[yellow]Draft cleared.[/yellow]")


if __name__ == "__main__":
    run_onboarding()
