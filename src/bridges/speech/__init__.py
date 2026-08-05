"""Issue 30：听写与单条回答朗读（固定 ASR/TTS 快照的真实语音链路）。"""

from bridges.speech.service import SpeechService, markdown_to_plain_text

__all__ = ["SpeechService", "markdown_to_plain_text"]
