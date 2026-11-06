"""Short system text for the voice LLM. No tools, no markdown."""

SYSTEM_PROMPT = (
    "Answer in the user's spoken language and dialect. "
    "Cantonese: colloquial spoken Cantonese, Traditional Chinese. "
    "1-2 short sentences. No markdown."
)

# Spoken when an upstream returns 503 and the body says the token expired.
BUSY_TEXT = "系統忙，等陣再試"

# Spoken for other upstream failures so the device still gets a caption.
FAIL_TEXT = "暫時答唔到，等陣再試"

# Spoken when STT returns no words. Skips LLM and TTS.
EMPTY_TEXT = "聽唔清，請再講一次"
