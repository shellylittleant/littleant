"""LittleAnt V14 - Internationalization"""
import json, os

_lang = "en"
_strings = {}
LANG_DIR = os.path.dirname(__file__)

def load_language(lang_code="en"):
    global _lang, _strings
    _lang = lang_code
    path = os.path.join(LANG_DIR, f"{lang_code}.json")
    if not os.path.exists(path):
        path = os.path.join(LANG_DIR, "en.json")
    with open(path, "r", encoding="utf-8") as f:
        _strings = json.load(f)

def t(key, **kwargs):
    text = _strings.get(key, key)
    if kwargs:
        try: text = text.format(**kwargs)
        except: pass
    return text

def get_lang(): return _lang
