"""Cloud captions: each finished sentence goes to a speech API instead of the local Whisper model.

Settings → Captions by: "local" (Whisper on this computer, private, the default) or a provider whose
API key is saved in Settings → Assistant:

  gemini      one request hears AND translates the sentence (Gemini understands audio)
  openai      gpt-4o-mini-transcribe hears it; Opus-MT translates it locally
  cloudflare  Workers AI Whisper Large v3 Turbo hears it; Opus-MT translates it locally

Dolmi still finds the pauses locally (Silero VAD), so only finished sentences are sent: one request
per sentence, no live draft line. Requests run on their own thread, in order, so capturing never waits.
"""
import base64
import io
import json
import queue
import threading
import time
import urllib.error
import urllib.request
import wave

import numpy as np

import live_subs
import models

PROVIDERS = {"gemini": "Gemini", "openai": "OpenAI", "cloudflare": "Cloudflare Workers AI"}
OPENAI_MODEL = "gpt-4o-mini-transcribe"
CLOUDFLARE_MODEL = "@cf/openai/whisper-large-v3-turbo"
_ERROR_EVERY = 30.0   # s between repeated error toasts


def wav_bytes(audio, rate=live_subs.SR):
    """16 kHz float32 samples -> a 16-bit mono WAV file in memory."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


def _post_json(url, payload, headers, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}")


class CloudEngine(live_subs.Engine):
    """Same interface as live_subs.Engine (translate, reload_terms, translators, ...), but the
    speech recognition (and for Gemini the translation) happens in the cloud."""
    cloud = True

    def __init__(self, provider, api_key, language="de", model=""):
        if provider not in PROVIDERS:
            raise RuntimeError(f"{provider} can't caption speech; pick Gemini, OpenAI or Cloudflare")
        if not api_key:
            raise RuntimeError(f"add your {PROVIDERS[provider]} API key in Settings → Assistant first")
        self.provider, self.key, self.language = provider, api_key, language
        self.model_name = model or {"openai": OPENAI_MODEL, "cloudflare": CLOUDFLARE_MODEL}.get(provider, "")
        self.device = "cpu"   # for the local translator (OpenAI/Cloudflare); captions come from the cloud
        self.translators, self.context = {}, ""
        self.thinking = True   # Gemini: ask for low thinking (2 s instead of 3) until the model says it can't
        self.reload_terms()
        if provider != "gemini":   # these only transcribe: translate locally, like local captions
            self._translator(models.translator_key(language), download=True)
        self._jobs = queue.Queue()
        self._last_error = 0.0
        threading.Thread(target=self._run, daemon=True).start()
        print(f"☁️ Cloud captions: {PROVIDERS[provider]} · {self.model_name}")

    # ---- worker hands finished sentences here
    def submit(self, audio, start, end, ui_q, transcript):
        self._jobs.put((audio, start, end, ui_q, transcript))

    def _run(self):
        while True:
            audio, start, end, ui_q, tr = self._jobs.get()
            try:
                heard, subtitle = self.recognize(audio)
            except Exception as e:
                if time.time() - self._last_error > _ERROR_EVERY:
                    self._last_error = time.time()
                    ui_q.put(("error", f"{PROVIDERS[self.provider]} captions failed: {e}", ""))
                print(f"cloud captions: {e}")
                continue
            if heard and not live_subs.is_junk(heard):
                ui_q.put(("final", heard, subtitle))
                tr.add(start, end, heard, subtitle)
                self.context = heard

    # ---- one sentence: audio -> (what was said, its translation)
    def recognize(self, audio):
        if self.provider == "gemini":
            return self._gemini(audio)
        heard = self._openai(audio) if self.provider == "openai" else self._cloudflare(audio)
        heard = live_subs.collapse_repeats(heard.strip())
        return heard, (self.translate(heard, self.language) if heard else "")

    def _gemini(self, audio):
        src = models.LANGUAGES[self.language]
        dst = models.LANGUAGES[models.SUBTITLES[self.language]]
        prompt = (f"This is one sentence from a live meeting, spoken in {src}. Transcribe exactly what is "
                  f"said, in {src}, then translate it into natural {dst}. Return JSON with the keys "
                  f'"heard" and "translation". If there is no clear speech (silence, music, noise), '
                  f"return empty strings; never invent words.")
        if self.glossary:
            prompt += f" Names and terms that may come up (spell them like this): {self.glossary}."
        if self.context:
            prompt += f' The previous sentence was: "{self.context[-200:]}".'
        body = {"contents": [{"parts": [{"text": prompt}, {"inline_data": {
                    "mime_type": "audio/wav", "data": base64.b64encode(wav_bytes(audio)).decode()}}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        if self.thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}
        try:
            data = _post_json(url, body, {"x-goog-api-key": self.key})
        except RuntimeError as e:
            if not (self.thinking and "thinking" in str(e).lower()):
                raise
            self.thinking = False   # this model has no thinking levels: ask again without
            del body["generationConfig"]["thinkingConfig"]
            data = _post_json(url, body, {"x-goog-api-key": self.key})
        parts = data["candidates"][0]["content"].get("parts", [])
        out = json.loads("".join(p.get("text", "") for p in parts) or "{}")
        heard = live_subs.collapse_repeats((out.get("heard") or "").strip())
        return heard, self.vocab.finish((out.get("translation") or "").strip())

    def _openai(self, audio):
        import openai
        client = openai.OpenAI(api_key=self.key, timeout=30.0)
        r = client.audio.transcriptions.create(
            model=self.model_name, file=("sentence.wav", wav_bytes(audio), "audio/wav"),
            language=self.language, prompt=self.glossary or openai.NOT_GIVEN)
        return r.text

    def _cloudflare(self, audio):
        account, _, token = self.key.partition(":")
        payload = {"audio": base64.b64encode(wav_bytes(audio)).decode(), "language": self.language}
        if self.glossary:
            payload["initial_prompt"] = self.glossary
        data = _post_json(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{self.model_name}",
                          payload, {"Authorization": f"Bearer {token}"})
        return (data.get("result") or {}).get("text", "")
