"""Localized server-side strings (it / en / es / fr / de / ru, the languages of the site): error messages and safe
fill-in values."""
from __future__ import annotations

from typing import Any, Optional

SUPPORTED_LANGS = ("it", "en", "es", "fr", "de", "ru")
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
        "error_voice_too_long": "Il vocale è troppo lungo: la trascrizione supera {max} caratteri. Prova con un vocale più breve o incolla solo la parte che ti interessa.",
        "trial_ended": "Prova terminata: il tuo codice non ha più analisi disponibili o non è più attivo. Per continuare a usare Before You Send, scrivici a hola@bb1studio.com.",
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
        "error_voice_too_long": "The voice note is too long: its transcript is over {max} characters. Try a shorter one or paste just the part you need.",
        "trial_ended": "Trial ended: your code has no analyses left or is no longer active. To keep using Before You Send, write to us at hola@bb1studio.com.",
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
        "error_voice_too_long": "El audio es demasiado largo: su transcripción supera los {max} caracteres. Prueba con uno más corto o pega solo la parte que te interesa.",
        "trial_ended": "Prueba terminada: tu código ya no tiene análisis disponibles o ya no está activo. Para seguir usando Before You Send, escríbenos a hola@bb1studio.com.",
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
    "fr": {
        "error_empty_text": "Collez un message à analyser.",
        "error_empty_send": "Collez un message à évaluer.",
        "error_audio_missing": "Importez un audio ou collez une transcription.",
        "error_image_missing": "Importez une capture ou collez le texte extrait.",
        "error_text_too_long": "Le texte est trop long : {max} caractères au maximum.",
        "error_invalid_input": "Demande non valide. Vérifiez les données et réessayez.",
        "error_image_too_large": "L’image dépasse {mb} Mo. Essayez avec une capture plus petite.",
        "error_image_type": "Format d’image non pris en charge. Utilisez PNG, JPEG, WebP ou GIF.",
        "error_audio_too_large": "Le fichier audio dépasse {mb} Mo.",
        "error_audio_type": "Importez un fichier audio (par exemple .m4a, .mp3, .ogg ou .wav).",
        "error_voice_unavailable": "La transcription des vocaux n’est pas disponible pour le moment. Collez la transcription.",
        "error_ai_unavailable": "L’analyse n’est pas disponible pour le moment. Réessayez plus tard.",
        "error_refused": "Je ne peux pas analyser ce contenu. Essayez avec un autre message.",
        "error_busy": "Le service est très sollicité en ce moment. Réessayez dans quelques secondes.",
        "error_generic": "Quelque chose s’est mal passé pendant l’analyse. Réessayez dans un instant.",
        "error_transcription": "Je n’ai pas réussi à transcrire le vocal. Réessayez ou collez la transcription.",
        "error_transcription_empty": "Je n’ai trouvé aucun mot dans le vocal. Essayez un autre fichier ou collez la transcription.",
        "error_voice_too_long": "Le vocal est trop long : sa transcription dépasse {max} caractères. Essayez avec un vocal plus court ou collez seulement la partie qui vous intéresse.",
        "trial_ended": "Essai terminé : votre code n’a plus d’analyses disponibles ou n’est plus actif. Pour continuer à utiliser Before You Send, écrivez-nous à hola@bb1studio.com.",
        "verdict_fallback": "Lecture indisponible",
        "meaning_fallback": "Impossible de résumer le message.",
        "issue_fallback": "Relisez-le calmement avant de l’envoyer.",
        "flag_fallbacks": ["Contexte limité", "À vérifier", "Lecture prudente"],
        "guardrail_fallbacks": ["C’est une lecture indicative, pas une certitude.", "En cas de doute, demandez directement."],
        "reply_styles": ["Neutre", "Amicale", "Décontractée"],
        "reply_fallbacks": ["D’accord, merci de me l’avoir dit.", "Merci, j’y réfléchis et je te réponds.", "Bien reçu, je t’écris plus tard."],
        "score_labels": ["Très solide", "Bonne base", "À revoir", "À réécrire"],
    },
    "de": {
        "error_empty_text": "Fügen Sie eine Nachricht zum Analysieren ein.",
        "error_empty_send": "Fügen Sie eine Nachricht zum Bewerten ein.",
        "error_audio_missing": "Laden Sie eine Audiodatei hoch oder fügen Sie eine Abschrift ein.",
        "error_image_missing": "Laden Sie einen Screenshot hoch oder fügen Sie den ausgelesenen Text ein.",
        "error_text_too_long": "Der Text ist zu lang: höchstens {max} Zeichen.",
        "error_invalid_input": "Ungültige Anfrage. Prüfen Sie die Angaben und versuchen Sie es erneut.",
        "error_image_too_large": "Das Bild ist größer als {mb} MB. Versuchen Sie es mit einem kleineren Screenshot.",
        "error_image_type": "Bildformat nicht unterstützt. Verwenden Sie PNG, JPEG, WebP oder GIF.",
        "error_audio_too_large": "Die Audiodatei ist größer als {mb} MB.",
        "error_audio_type": "Laden Sie eine Audiodatei hoch (zum Beispiel .m4a, .mp3, .ogg oder .wav).",
        "error_voice_unavailable": "Die Transkription von Sprachnachrichten ist gerade nicht verfügbar. Fügen Sie die Abschrift ein.",
        "error_ai_unavailable": "Die Analyse ist gerade nicht verfügbar. Versuchen Sie es später erneut.",
        "error_refused": "Diesen Inhalt kann ich nicht analysieren. Versuchen Sie es mit einer anderen Nachricht.",
        "error_busy": "Der Dienst ist gerade stark ausgelastet. Versuchen Sie es in ein paar Sekunden erneut.",
        "error_generic": "Bei der Analyse ist etwas schiefgelaufen. Versuchen Sie es gleich noch einmal.",
        "error_transcription": "Ich konnte die Sprachnachricht nicht transkribieren. Versuchen Sie es erneut oder fügen Sie die Abschrift ein.",
        "error_transcription_empty": "In der Sprachnachricht habe ich keine Wörter gefunden. Versuchen Sie es mit einer anderen Datei oder fügen Sie die Abschrift ein.",
        "error_voice_too_long": "Die Sprachnachricht ist zu lang: Ihre Abschrift hat mehr als {max} Zeichen. Versuchen Sie es mit einer kürzeren oder fügen Sie nur den Teil ein, der Sie interessiert.",
        "trial_ended": "Test beendet: Ihr Code hat keine Analysen mehr oder ist nicht mehr aktiv. Wenn Sie Before You Send weiter nutzen möchten, schreiben Sie uns an hola@bb1studio.com.",
        "verdict_fallback": "Einordnung nicht verfügbar",
        "meaning_fallback": "Die Nachricht konnte nicht zusammengefasst werden.",
        "issue_fallback": "Lesen Sie sie in Ruhe noch einmal, bevor Sie sie senden.",
        "flag_fallbacks": ["Wenig Kontext", "Zu prüfen", "Vorsichtige Einordnung"],
        "guardrail_fallbacks": ["Das ist eine Orientierung, keine Gewissheit.", "Im Zweifel fragen Sie direkt nach."],
        "reply_styles": ["Neutral", "Freundlich", "Locker"],
        "reply_fallbacks": ["Okay, danke, dass du es mir sagst.", "Danke, ich denke darüber nach und melde mich.", "Angekommen, ich schreibe dir später."],
        "score_labels": ["Sehr stark", "Gute Basis", "Überarbeiten", "Neu schreiben"],
    },
    "ru": {
        "error_empty_text": "Вставьте сообщение для анализа.",
        "error_empty_send": "Вставьте сообщение для оценки.",
        "error_audio_missing": "Загрузите аудио или вставьте расшифровку.",
        "error_image_missing": "Загрузите скриншот или вставьте извлечённый текст.",
        "error_text_too_long": "Текст слишком длинный: не больше {max} символов.",
        "error_invalid_input": "Некорректный запрос. Проверьте данные и попробуйте снова.",
        "error_image_too_large": "Изображение больше {mb} МБ. Попробуйте скриншот поменьше.",
        "error_image_type": "Формат изображения не поддерживается. Используйте PNG, JPEG, WebP или GIF.",
        "error_audio_too_large": "Аудиофайл больше {mb} МБ.",
        "error_audio_type": "Загрузите аудиофайл (например, .m4a, .mp3, .ogg или .wav).",
        "error_voice_unavailable": "Расшифровка голосовых сейчас недоступна. Вставьте расшифровку.",
        "error_ai_unavailable": "Анализ сейчас недоступен. Попробуйте позже.",
        "error_refused": "Я не могу проанализировать это содержимое. Попробуйте другое сообщение.",
        "error_busy": "Сервис сейчас сильно загружен. Попробуйте через несколько секунд.",
        "error_generic": "Во время анализа что-то пошло не так. Попробуйте ещё раз чуть позже.",
        "error_transcription": "Не удалось расшифровать голосовое. Попробуйте снова или вставьте расшифровку.",
        "error_transcription_empty": "В голосовом не нашлось слов. Попробуйте другой файл или вставьте расшифровку.",
        "error_voice_too_long": "Голосовое слишком длинное: расшифровка длиннее {max} символов. Попробуйте покороче или вставьте только нужную часть.",
        "trial_ended": "Пробный доступ закончился: в вашем коде не осталось анализов, или он больше не действует. Чтобы продолжить пользоваться Before You Send, напишите нам на hola@bb1studio.com.",
        "verdict_fallback": "Разбор недоступен",
        "meaning_fallback": "Не удалось кратко изложить сообщение.",
        "issue_fallback": "Перечитайте спокойно перед отправкой.",
        "flag_fallbacks": ["Мало контекста", "Стоит проверить", "Осторожный разбор"],
        "guardrail_fallbacks": ["Это ориентировочный разбор, а не точный ответ.", "Если сомневаетесь, спросите напрямую."],
        "reply_styles": ["Нейтрально", "Дружелюбно", "Неформально"],
        "reply_fallbacks": ["Хорошо, спасибо, что сказал(а).", "Спасибо, подумаю и отвечу.", "Принято, напишу позже."],
        "score_labels": ["Очень сильно", "Хорошая основа", "Стоит доработать", "Лучше переписать"],
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
