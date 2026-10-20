"""Localized server-side strings (it / en / es): error messages and safe fill-in values."""
from __future__ import annotations

from typing import Any, Optional

SUPPORTED_LANGS = ("it", "en", "es")
DEFAULT_LANG = "it"


def canonical_lang(lang: Optional[str]) -> str:
    raw = (lang or DEFAULT_LANG).strip().lower()[:2]
    return raw if raw in SUPPORTED_LANGS else DEFAULT_LANG


# Score labels, best to worst, with the minimum score for each (shared by the prompt and the schema enum).
SCORE_THRESHOLDS = (85, 70, 55, 0)

TEXTS: dict[str, dict[str, Any]] = {
    "it": {
        "error_empty_text": "Inserisci un testo da analizzare.",
        "error_empty_send": "Inserisci un messaggio da valutare.",
        "error_audio_missing": "Carica un file audio o incolla una trascrizione.",
        "error_image_missing": "Carica uno screenshot o incolla il testo estratto.",
        "error_text_too_long": "Il testo è troppo lungo: massimo {max} caratteri.",
        "error_invalid_input": "Richiesta non valida. Controlla i dati e riprova.",
        "error_image_too_large": "L'immagine supera {mb} MB. Prova con uno screenshot più piccolo.",
        "error_image_type": "Formato immagine non supportato. Usa PNG, JPEG, WebP o GIF.",
        "error_audio_too_large": "Il file audio supera {mb} MB.",
        "error_audio_type": "Carica un file audio (per esempio .m4a, .mp3, .ogg o .wav).",
        "error_voice_unavailable": "La trascrizione dei vocali non è disponibile al momento. Incolla la trascrizione del vocale.",
        "error_ai_unavailable": "L'analisi non è disponibile al momento. Riprova più tardi.",
        "error_refused": "Non posso analizzare questo contenuto. Prova con un altro messaggio.",
        "error_busy": "Il servizio è molto richiesto in questo momento. Riprova tra qualche secondo.",
        "error_generic": "Qualcosa è andato storto durante l'analisi. Riprova tra poco.",
        "error_transcription": "Non sono riuscito a trascrivere il vocale. Riprova o incolla la trascrizione.",
        "error_transcription_empty": "Non ho trovato parole nel vocale. Prova con un altro file o incolla la trascrizione.",
        "trial_ended": "Prova terminata: il tuo codice non ha più analisi disponibili o non è più attivo. Vedi le opzioni di accesso: {url}",
        "verdict_fallback": "Lettura non disponibile",
        "meaning_fallback": "Non è stato possibile riassumere il messaggio.",
        "issue_fallback": "Rileggilo con calma prima di inviarlo.",
        "flag_fallbacks": ["Contesto limitato", "Da verificare", "Lettura prudente"],
        "guardrail_fallbacks": [
            "È una lettura indicativa, non una certezza.",
            "Se hai dubbi, chiedi direttamente.",
        ],
        "reply_styles": ["Neutra", "Amichevole", "Informale"],
        "reply_fallbacks": [
            "Ok, grazie per avermelo detto.",
            "Grazie, ci penso e ti rispondo.",
            "Ricevuto, ti scrivo più tardi.",
        ],
        "score_labels": ["Molto forte", "Buona base", "Da rivedere", "Da riscrivere"],
    },
    "en": {
        "error_empty_text": "Paste a message to analyze.",
        "error_empty_send": "Paste a message to score.",
        "error_audio_missing": "Upload audio or paste a transcript.",
        "error_image_missing": "Upload a screenshot or paste extracted text.",
        "error_text_too_long": "The text is too long: {max} characters at most.",
        "error_invalid_input": "Invalid request. Check the data and try again.",
        "error_image_too_large": "The image is larger than {mb} MB. Try a smaller screenshot.",
        "error_image_type": "Unsupported image format. Use PNG, JPEG, WebP or GIF.",
        "error_audio_too_large": "The audio file is larger than {mb} MB.",
        "error_audio_type": "Upload an audio file (for example .m4a, .mp3, .ogg or .wav).",
        "error_voice_unavailable": "Voice note transcription is not available right now. Paste the transcript instead.",
        "error_ai_unavailable": "Analysis is not available right now. Please try again later.",
        "error_refused": "I can't analyze this content. Try a different message.",
        "error_busy": "The service is very busy right now. Try again in a few seconds.",
        "error_generic": "Something went wrong during the analysis. Please try again shortly.",
        "error_transcription": "I couldn't transcribe the voice note. Try again or paste the transcript.",
        "error_transcription_empty": "I couldn't find any words in the voice note. Try another file or paste the transcript.",
        "trial_ended": "Trial ended: your code has no analyses left or is no longer active. See the access options: {url}",
        "verdict_fallback": "Read not available",
        "meaning_fallback": "The message could not be summarized.",
        "issue_fallback": "Read it again calmly before you send it.",
        "flag_fallbacks": ["Limited context", "Worth checking", "Cautious read"],
        "guardrail_fallbacks": [
            "This is an indicative read, not a certainty.",
            "If in doubt, ask them directly.",
        ],
        "reply_styles": ["Neutral", "Friendly", "Casual"],
        "reply_fallbacks": [
            "Okay, thanks for letting me know.",
            "Thanks, I'll think about it and get back to you.",
            "Got it, I'll text you later.",
        ],
        "score_labels": ["Very strong", "Good base", "Needs work", "Rewrite it"],
    },
    "es": {
        "error_empty_text": "Pega un mensaje para analizar.",
        "error_empty_send": "Pega un mensaje para evaluar.",
        "error_audio_missing": "Sube un audio o pega una transcripción.",
        "error_image_missing": "Sube una captura o pega el texto extraído.",
        "error_text_too_long": "El texto es demasiado largo: {max} caracteres como máximo.",
        "error_invalid_input": "Solicitud no válida. Revisa los datos y vuelve a intentarlo.",
        "error_image_too_large": "La imagen supera {mb} MB. Prueba con una captura más pequeña.",
        "error_image_type": "Formato de imagen no compatible. Usa PNG, JPEG, WebP o GIF.",
        "error_audio_too_large": "El archivo de audio supera {mb} MB.",
        "error_audio_type": "Sube un archivo de audio (por ejemplo .m4a, .mp3, .ogg o .wav).",
        "error_voice_unavailable": "La transcripción de notas de voz no está disponible ahora mismo. Pega la transcripción.",
        "error_ai_unavailable": "El análisis no está disponible ahora mismo. Vuelve a intentarlo más tarde.",
        "error_refused": "No puedo analizar este contenido. Prueba con otro mensaje.",
        "error_busy": "El servicio está muy solicitado ahora mismo. Vuelve a intentarlo en unos segundos.",
        "error_generic": "Algo ha fallado durante el análisis. Vuelve a intentarlo en breve.",
        "error_transcription": "No he podido transcribir el audio. Vuelve a intentarlo o pega la transcripción.",
        "error_transcription_empty": "No he encontrado palabras en el audio. Prueba con otro archivo o pega la transcripción.",
        "trial_ended": "Prueba terminada: tu código ya no tiene análisis disponibles o ya no está activo. Consulta las opciones de acceso: {url}",
        "verdict_fallback": "Lectura no disponible",
        "meaning_fallback": "No se ha podido resumir el mensaje.",
        "issue_fallback": "Reléelo con calma antes de enviarlo.",
        "flag_fallbacks": ["Contexto limitado", "Por verificar", "Lectura prudente"],
        "guardrail_fallbacks": [
            "Es una lectura orientativa, no una certeza.",
            "Si tienes dudas, pregunta directamente.",
        ],
        "reply_styles": ["Neutra", "Amable", "Informal"],
        "reply_fallbacks": [
            "Vale, gracias por decírmelo.",
            "Gracias, lo pienso y te respondo.",
            "Recibido, te escribo luego.",
        ],
        "score_labels": ["Muy fuerte", "Buena base", "Hay que revisar", "Hay que reescribir"],
    },
}


def text(lang: str, key: str, **values: Any) -> Any:
    entry = TEXTS[canonical_lang(lang)][key]
    return entry.format(**values) if values and isinstance(entry, str) else entry


def label_for_score(score: int, lang: str) -> str:
    labels = TEXTS[canonical_lang(lang)]["score_labels"]
    for label, minimum in zip(labels, SCORE_THRESHOLDS, strict=True):
        if score >= minimum:
            return label
    return labels[-1]
