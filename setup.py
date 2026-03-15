#!/usr/bin/env python3
"""LittleAnt V14 - Setup Wizard"""
import json, os, sys

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "littleant", "config.json")
PROVIDERS = {
    "1": ("OpenAI (GPT-4o)", "openai", "https://api.openai.com/v1", "gpt-4o"),
    "2": ("DeepSeek", "deepseek", "https://api.deepseek.com", "deepseek-chat"),
    "3": ("Claude", "claude", "https://api.anthropic.com/v1", "claude-sonnet-4-20250514"),
    "4": ("Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    "5": ("Grok", "grok", "https://api.x.ai/v1", "grok-3-latest"),
}

# For reverse lookup by provider id
PROVIDERS_BY_ID = {v[1]: {"name": v[0], "id": v[1], "base_url": v[2], "model": v[3]} for v in PROVIDERS.values()}


def test_api_key(base_url, model, api_key, timeout=15):
    """Test if an API key works by making a minimal request. Returns (ok, error_msg)."""
    import urllib.request, urllib.error
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 5,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            if data.get("choices"):
                return True, ""
            return False, "Unexpected response format"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "Invalid API key (401 Unauthorized)"
        elif e.code == 403:
            return False, "Access denied (403 Forbidden)"
        elif e.code == 429:
            # Rate limited but key is valid
            return True, ""
        else:
            return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)[:200]

def setup():
    print("\n" + "=" * 42)
    print("  LittleAnt V14 - Setup")
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
    while True:
        key = input("\n> ").strip()
        if len(key) < 10:
            print("Too short" if lang == "en" else "太短了")
            continue
        print("  Testing API key..." if lang == "en" else "  正在测试 API Key...")
        ok, err = test_api_key(prov[2], prov[3], key)
        if ok:
            print("  ✅ API key works!" if lang == "en" else "  ✅ API Key 可用！")
            break
        else:
            print(f"  ❌ API key invalid: {err}" if lang == "en" else f"  ❌ API Key 不可用: {err}")
            print("  Try again." if lang == "en" else "  请重新输入。")

    config = {"language": lang, "telegram_token": tg, "ai_provider": prov[1],
              "ai_api_key": key, "ai_base_url": prov[2], "ai_model": prov[3],
              "providers": {prov[1]: {"api_key": key, "base_url": prov[2], "model": prov[3]}}}
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f: json.dump(config, f, indent=2)

    done = "✅ Setup complete! Run: bash start.sh" if lang == "en" else "✅ 安装完成！运行: bash start.sh"
    print(f"\n{done}\n  Provider: {prov[0]}\n  Config: {CONFIG_PATH}\n")


def load_config():
    if not os.path.exists(CONFIG_PATH): return None
    with open(CONFIG_PATH) as f: return json.load(f)


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f: json.dump(config, f, indent=2)

if __name__ == "__main__":
    setup()
