#!/usr/bin/env python3
"""LittleAnt V12.1 - Setup Wizard"""
import json, os, sys

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "littleant", "config.json")
PROVIDERS = {
    "1": ("OpenAI (GPT-4o)", "openai", "https://api.openai.com/v1", "gpt-4o"),
    "2": ("Claude", "claude", "https://api.anthropic.com/v1", "claude-sonnet-4-20250514"),
    "3": ("Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    "4": ("Grok", "grok", "https://api.x.ai/v1", "grok-3-latest"),
}

def setup():
    print("\n" + "=" * 42)
    print("  LittleAnt V12.1 - Setup")
    print("=" * 42 + "\n")

    print("Select language / 选择语言:")
    print("  1) English")
    print("  2) 中文")
    lang = None
    while not lang:
        c = input("\n> ").strip()
        if c == "1": lang = "en"
        elif c == "2": lang = "zh"
        else: print("Enter 1 or 2")

    print("\n" + ("Enter Telegram Bot Token:" if lang == "en" else "请输入 Telegram Bot Token:"))
    print("  (Get from @BotFather on Telegram)")
    tg = ""
    while not (tg and ":" in tg):
        tg = input("\n> ").strip()
        if not (tg and ":" in tg): print("Invalid format. Example: 123456:ABC-DEF...")

    print("\n" + ("Select AI Provider:" if lang == "en" else "选择 AI 服务商:"))
    for k, v in PROVIDERS.items(): print(f"  {k}) {v[0]}")
    prov = None
    while not prov:
        c = input("\n> ").strip()
        if c in PROVIDERS: prov = PROVIDERS[c]
        else: print("Invalid" if lang == "en" else "无效选择")

    print("\n" + (f"Enter {prov[0]} API Key:" if lang == "en" else f"请输入 {prov[0]} API Key:"))
    key = ""
    while len(key) < 10:
        key = input("\n> ").strip()
        if len(key) < 10: print("Too short" if lang == "en" else "太短了")

    config = {"language": lang, "telegram_token": tg, "ai_provider": prov[1],
              "ai_api_key": key, "ai_base_url": prov[2], "ai_model": prov[3]}
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f: json.dump(config, f, indent=2)

    done = "✅ Setup complete! Run: bash start.sh" if lang == "en" else "✅ 安装完成！运行: bash start.sh"
    print(f"\n{done}\n  Provider: {prov[0]}\n  Config: {CONFIG_PATH}\n")

if __name__ == "__main__":
    setup()
