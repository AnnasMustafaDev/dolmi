"""Dolmi Assistant mode: answers spoken English technical questions with a fast cloud model.

Providers: Claude (Anthropic), OpenAI, Gemini (Google) or NVIDIA. Gemini and NVIDIA are reached
through their OpenAI-compatible endpoints, so the openai package talks to all three. API keys are encrypted with a secret kept in the macOS
login Keychain, so only the signed-in Mac user can read them; settings.json never holds a key in
plain text.
"""
import base64, getpass, hashlib, hmac, os, re, secrets

PROVIDERS = {
    "claude": {"label": "Claude (Anthropic)", "model": "claude-haiku-4-5",
               "key_hint": "sk-ant-…", "console": "console.anthropic.com"},
    "openai": {"label": "OpenAI", "model": "gpt-5.4-mini",
               "key_hint": "sk-…", "console": "platform.openai.com"},
    "gemini": {"label": "Gemini (Google)", "model": "gemini-3-flash-preview",
               "key_hint": "AIza…", "console": "aistudio.google.com/apikey",
               "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "nvidia": {"label": "NVIDIA", "model": "nvidia/nemotron-3-super-120b-a12b",
               "key_hint": "nvapi-…", "console": "build.nvidia.com",
               "base_url": "https://integrate.api.nvidia.com/v1"},
}

SYSTEM = """You are the user's live copilot in meetings and interviews. Other people are talking to \
the user; their speech was transcribed, so expect small transcription errors and infer what was meant.

For each question, write what the user can say back: first person, as the user, in natural spoken \
English they can read out loud. Never say you are an AI, a language model or ChatGPT.

- Questions about the user (background, experience, projects, education, strengths and weaknesses, \
motivation, goals, opinions, how they work, "tell me about a time when…"): answer confidently as \
the user, built from the profile below. Never refuse, never say something wasn't shared, never say \
you'd rather not answer. If the profile doesn't cover it, give a natural answer that fits the profile, \
keeping concrete facts (numbers, dates, names) general instead of making them up.
- Technical questions: answer correctly and confidently, the way the user would explain it in an \
interview, and tie it to the user's real experience from the profile wherever it fits ("In my last role I…", \
"When I built … I used …"). Topics include AI and LLMs, agents (LangGraph, MCP, tool use), RAG and \
retrieval, embeddings, evaluation, ML fundamentals, Python and FastAPI, full-stack and web \
development, APIs, SQL and NoSQL databases, Docker and Kubernetes, cloud (Azure, AWS, GCP), DevOps \
and CI/CD, security, system design, and data structures and algorithms.
- Data structures and algorithms or coding questions: say the approach in one or two sentences, \
give time and space complexity (Big-O), mention the key edge case, then a short Python solution. \
If a brute force exists, name it first, then the better approach.

Format, for reading at a glance:
1. First line: the direct answer in one sentence.
2. Then 2-4 short bullet points with key details.
3. Only if code clearly helps (always for coding questions): one short Python code block.
Every message gets an answer. If it isn't clearly a question, treat it as said to the user and \
reply the way the user would.
Earlier questions and answers, and the last lines of the meeting, are included: use them for \
follow-ups like "and how did that scale?"."""

LENGTHS = {
    "short": "Keep the words under 150 (a code block doesn't count).",
    "detailed": "Go deeper when it helps: up to 400 words, still answer-first, with one example.",
}
HISTORY_MESSAGES = 10   # last 5 question/answer pairs travel with each new question

SUMMARY_SYSTEM = """You turn a meeting transcript into notes for someone who attended. The transcript \
comes from speech recognition and machine translation, so expect small errors: infer what was meant, \
but never invent facts, names, numbers or dates.

Write in English, in Markdown, with exactly these four sections:
## Summary
3-6 sentences: what the meeting was about and where it ended.
## Decisions
Bullet points, or "None recorded."
## Action items
One "- [ ] Owner — task (deadline)" per item; leave out owner or deadline if nobody said it. \
"None recorded." if there are none.
## Open questions
Bullet points, or "None."
Be brief and skip small talk."""
MAX_TRANSCRIPT_CHARS = 120_000   # ~2-3 hours of speech; older lines are cut beyond this

# ------------------------------------------------------------------ Pro unlock
# Only a salted PBKDF2 hash of the password is used, and it lives in .env (DOLMI_PRO_SALT /
# DOLMI_PRO_HASH), never in the source. This is a local check: it keeps the feature out of reach
# of normal users, but anyone editing the source can bypass it. Real paid licensing needs
# server-side license keys. See .env.example for how to generate these for your own password.
def _pro_secret(name):
    try:
        return bytes.fromhex(os.environ.get(name, ""))
    except ValueError:
        return b""

def check_password(password):
    salt, expected = _pro_secret("DOLMI_PRO_SALT"), _pro_secret("DOLMI_PRO_HASH")
    if not salt or not expected:
        return False   # Assistant unlock not configured (set DOLMI_PRO_SALT / DOLMI_PRO_HASH in .env)
    attempt = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return hmac.compare_digest(attempt, expected)

def _pro_token():
    return f"dolmi-pro:{getpass.getuser()}"

def pro_unlock_value():
    """Stored in settings; encrypted with this Mac user's Keychain secret, so it can't be copied to another Mac."""
    return encrypt_key(_pro_token())

def is_pro(stored):
    return decrypt_key(stored) == _pro_token()

# Speech recognition often drops the "?", and interviewers ask with commands ("Tell me about…",
# "Walk us through…") or after a filler ("So, …"), so all of those count as questions.
QUESTION_START = re.compile(
    r"^(?:(?:so|okay|ok|and|now|alright|well|then|right|um|uh|yeah)[,.\s]+)*"
    r"(what|why|how|when|where|which|who|whose|can|could|should|would|is|are|do|does|did|will|have|has|"
    r"had|were|was|any|explain|describe|tell|walk|compare|introduce|share|give|talk|list|name|define|"
    r"summari[sz]e|elaborate|imagine|suppose|let's|difference between|"
    # coding-interview tasks are said as commands: "Reverse a linked list", "Implement an LRU cache"
    r"reverse|implement|write|find|design|build|sort|merge|detect|check if|given|count|calculate|solve|"
    r"optimi[sz]e|convert|validate|traverse|search)\b", re.I)
QUESTION_ANYWHERE = re.compile(
    r"\?|\b(tell (me|us)|can you|could you|would you|do you|did you|have you|are you|were you|"
    r"what's your|what is your|how do you|how would you|i'd like to know|i want to know|i'm curious)\b", re.I)

def is_question(text):
    t = text.strip()
    return len(t.split()) >= 2 and bool(QUESTION_START.match(t) or QUESTION_ANYWHERE.search(t))

# ------------------------------------------------------------------ key storage (Keychain)
# A random 32-byte secret lives in the login Keychain (service "Dolmi"). Keys are encrypted with an
# HMAC-SHA256 keystream (CTR) and authenticated with HMAC-SHA256, all from the standard library.
_KEYCHAIN = ("Dolmi", "settings-encryption")
_secret = None

def _master():
    global _secret
    if _secret is None:
        import keyring
        stored = keyring.get_password(*_KEYCHAIN)
        if not stored:
            stored = secrets.token_hex(32)
            keyring.set_password(*_KEYCHAIN, stored)
        _secret = bytes.fromhex(stored)
    return _secret

def _stream(key, nonce, n):
    out = b"".join(hmac.digest(key, nonce + i.to_bytes(4, "big"), "sha256") for i in range((n + 31) // 32))
    return out[:n]

def _seal(protect, data):
    try:
        master = _master()
    except Exception as e:
        raise OSError(f"the Keychain could not encrypt/decrypt the API key ({e})")
    enc, mac = hmac.digest(master, b"enc", "sha256"), hmac.digest(master, b"mac", "sha256")
    if protect:
        nonce = secrets.token_bytes(16)
        body = bytes(a ^ b for a, b in zip(data, _stream(enc, nonce, len(data))))
        return nonce + body + hmac.digest(mac, nonce + body, "sha256")
    nonce, body, tag = data[:16], data[16:-32], data[-32:]
    if len(data) < 48 or not hmac.compare_digest(tag, hmac.digest(mac, nonce + body, "sha256")):
        raise ValueError("not encrypted by this Mac user")
    return bytes(a ^ b for a, b in zip(body, _stream(enc, nonce, len(body))))

def encrypt_key(key):
    return base64.b64encode(_seal(True, key.encode())).decode() if key else ""

def decrypt_key(stored):
    try:
        return _seal(False, base64.b64decode(stored)).decode() if stored else ""
    except (OSError, ValueError):
        return ""

# ------------------------------------------------------------------ answering
class AssistantError(Exception):
    """A problem worth showing the user as-is (bad key, no credit, network)."""

def build_system(context="", length="short"):
    system = f"{SYSTEM}\n{LENGTHS.get(length, LENGTHS['short'])}"
    if context.strip():
        system += f"\n\nThe user's profile and notes (who they are, their work, this meeting):\n{context.strip()}"
    else:
        system += "\n\nThe user has not saved a profile yet."
    return system

def stream_answer(provider, model, api_key, question, context="", history=(), length="short", meeting=()):
    """Yield the answer text piece by piece. `history` is earlier {role, content} messages;
    `meeting` is the last transcript lines before the question, so it knows what's being discussed."""
    if not api_key:
        raise AssistantError("Add an API key in Settings → Assistant first.")
    content = question
    if meeting:
        content = "Said in the meeting just before:\n" + "\n".join(meeting) + f"\n\nQuestion: {question}"
    messages = list(history)[-HISTORY_MESSAGES:] + [{"role": "user", "content": content}]
    system = build_system(context, length)
    yield from _sender(provider)(model, api_key, system, messages)

def stream_summary(provider, model, api_key, transcript, context=""):
    """Yield meeting notes (summary, decisions, action items, open questions) piece by piece."""
    if not api_key:
        raise AssistantError("Add an API key in Settings → Assistant first.")
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = "[…earlier part of the meeting cut for length…]\n" + transcript[-MAX_TRANSCRIPT_CHARS:]
    system = SUMMARY_SYSTEM
    if context.strip():
        system += f"\n\nBackground the user saved about themselves and the project:\n{context.strip()}"
    messages = [{"role": "user", "content": f"Meeting transcript:\n\n{transcript}"}]
    yield from _sender(provider)(model, api_key, system, messages, max_tokens=2048)

def _sender(provider):
    """The streaming call for a provider (looked up at call time, so tests can swap them)."""
    if provider == "claude":
        return _claude
    if provider == "openai":
        return _openai
    return lambda *args, **kwargs: _compatible(provider, *args, **kwargs)

def _claude(model, api_key, system, messages, max_tokens=1024):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    try:
        with client.messages.stream(model=model, max_tokens=max_tokens, system=system,
                                    messages=messages) as stream:
            yield from stream.text_stream
            if stream.get_final_message().stop_reason == "refusal":
                yield "\n\n(The model declined to answer this one.)"
    except anthropic.AuthenticationError:
        raise AssistantError("Claude rejected the API key — check it in Settings → Assistant.")
    except anthropic.PermissionDeniedError:
        raise AssistantError("This Claude API key isn't allowed to use that model.")
    except anthropic.NotFoundError:
        raise AssistantError(f"Claude doesn't know the model '{model}'.")
    except anthropic.RateLimitError:
        raise AssistantError("Claude rate limit or credit limit reached — try again shortly.")
    except anthropic.APIStatusError as e:
        raise AssistantError(f"Claude error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        raise AssistantError("Can't reach Claude — check the internet connection.")

def _openai(model, api_key, system, messages, max_tokens=1024):
    yield from _openai_stream("OpenAI", model, api_key, system, messages, max_completion_tokens=max_tokens)

def _compatible(provider, model, api_key, system, messages, max_tokens=1024):
    """Gemini and NVIDIA through their OpenAI-compatible endpoints. No token cap: their thinking
    models count reasoning against it and would cut answers short; the prompts already set the length."""
    p = PROVIDERS[provider]
    extra = {}
    if provider == "gemini" and model.startswith(("gemini-2.5", "gemini-3")):
        extra["reasoning_effort"] = "low"   # live answers: a little thinking, not seconds of it
    yield from strip_thinking(_openai_stream(p["label"], model, api_key, system, messages,
                                             base_url=p["base_url"], **extra))

def strip_thinking(pieces):
    """Some open models (NVIDIA's catalog) stream their reasoning inline as <think>…</think> before
    the answer. Drop it, even when a tag is split across streamed pieces."""
    buf, thinking, after = "", False, False   # after: just left a think block, skip its blank lines
    for piece in pieces:
        buf += piece
        while buf:
            if after:
                buf = buf.lstrip()
                after = not buf
                if not buf:
                    break
            tag = "</think>" if thinking else "<think>"
            i = buf.find(tag)
            if i >= 0:
                if not thinking and buf[:i]:
                    yield buf[:i]
                buf, thinking, after = buf[i + len(tag):], not thinking, thinking
                continue
            keep = next((n for n in range(len(tag) - 1, 0, -1) if buf.endswith(tag[:n])), 0)
            if not thinking and len(buf) > keep:
                yield buf[:len(buf) - keep]
            buf = buf[len(buf) - keep:] if keep else ""
            break
    if buf and not thinking:
        yield buf

def _openai_stream(label, model, api_key, system, messages, base_url=None, **options):
    import openai
    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
    try:
        stream = client.chat.completions.create(
            model=model, stream=True, messages=[{"role": "system", "content": system}] + messages, **options)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except openai.AuthenticationError:
        raise AssistantError(f"{label} rejected the API key — check it in Settings → Assistant.")
    except openai.PermissionDeniedError:
        raise AssistantError(f"This {label} API key isn't allowed to use that model.")
    except openai.NotFoundError:
        raise AssistantError(f"{label} doesn't know the model '{model}'.")
    except openai.RateLimitError:
        raise AssistantError(f"{label} rate limit or quota reached — check billing or try again shortly.")
    except openai.APIStatusError as e:
        if e.status_code == 400 and "api key" in str(e).lower():   # Gemini answers a bad key with 400
            raise AssistantError(f"{label} rejected the API key — check it in Settings → Assistant.")
        raise AssistantError(f"{label} error {e.status_code}: {e.message}")
    except openai.APIConnectionError:
        raise AssistantError(f"Can't reach {label} — check the internet connection.")
