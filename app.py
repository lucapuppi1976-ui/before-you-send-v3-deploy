
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT
ENV_FILE = ROOT / '.env'


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env(ENV_FILE)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
TEXT_MODEL = os.getenv('BYS_TEXT_MODEL', 'gpt-4o-mini')
VISION_MODEL = os.getenv('BYS_VISION_MODEL', TEXT_MODEL)
TRANSCRIBE_MODEL = os.getenv('BYS_TRANSCRIBE_MODEL', 'gpt-4o-mini-transcribe')
TIMEOUT_SECONDS = int(os.getenv('BYS_TIMEOUT_SECONDS', '120'))
DEBUG = os.getenv('BYS_DEBUG', 'false').lower() == 'true'

BYS_ACCESS_CODE = os.getenv('BYS_ACCESS_CODE', '').strip()
BYS_ACCESS_COOKIE_NAME = os.getenv('BYS_ACCESS_COOKIE_NAME', 'bys_access')
BYS_ACCESS_COOKIE_DAYS = max(1, int(os.getenv('BYS_ACCESS_COOKIE_DAYS', '30')))
BYS_ACCESS_PAGE_URL = os.getenv('BYS_ACCESS_PAGE_URL', 'https://bb1studio.com/before-you-send/access/').strip() or 'https://bb1studio.com/before-you-send/access/'
_gate_flag = os.getenv('BYS_GATE_ENABLED', '').strip().lower()
if _gate_flag in {'1', 'true', 'yes', 'on'}:
    BYS_GATE_ENABLED = True
elif _gate_flag in {'0', 'false', 'no', 'off'}:
    BYS_GATE_ENABLED = False
else:
    BYS_GATE_ENABLED = bool(BYS_ACCESS_CODE)
BYS_GATE_SECRET = (os.getenv('BYS_GATE_SECRET', '').strip() or OPENAI_API_KEY or 'bys-gate-dev-secret')

SUPPORTED_LANGS = {'it', 'en', 'es'}

app = FastAPI(title='Before You Send')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


class TextPayload(BaseModel):
    text: str
    lang: Optional[str] = 'it'


class AccessPayload(BaseModel):
    code: str


class OpenAIError(RuntimeError):
    pass


def canonical_lang(lang: Optional[str]) -> str:
    raw = (lang or 'it').strip().lower()[:2]
    return raw if raw in SUPPORTED_LANGS else 'it'


def require_api_key() -> None:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail='OPENAI_API_KEY missing. Copy .env.example to .env and add your key.')


def gate_enabled() -> bool:
    return BYS_GATE_ENABLED and bool(BYS_ACCESS_CODE)


def normalize_access_code(raw: Optional[str]) -> str:
    return re.sub(r'\s+', '', (raw or '').strip()).upper()


def gate_signature(payload: str) -> str:
    material = f'{payload}|{normalize_access_code(BYS_ACCESS_CODE)}'
    return hmac.new(BYS_GATE_SECRET.encode('utf-8'), material.encode('utf-8'), hashlib.sha256).hexdigest()


def issue_gate_token() -> str:
    expires_at = int(time.time()) + BYS_ACCESS_COOKIE_DAYS * 86400
    payload = f'access:{expires_at}'
    signed = f'{payload}:{gate_signature(payload)}'
    return base64.urlsafe_b64encode(signed.encode('utf-8')).decode('utf-8')


def verify_gate_token(token: str) -> bool:
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode('utf-8')).decode('utf-8')
        payload, signature = raw.rsplit(':', 1)
        if not hmac.compare_digest(signature, gate_signature(payload)):
            return False
        kind, expires_raw = payload.split(':', 1)
        if kind != 'access':
            return False
        return int(expires_raw) >= int(time.time())
    except Exception:
        return False


def request_has_gate_access(request: Request) -> bool:
    if not gate_enabled():
        return True
    return verify_gate_token(request.cookies.get(BYS_ACCESS_COOKIE_NAME, ''))


def require_gate_access(request: Request) -> None:
    if not request_has_gate_access(request):
        raise HTTPException(status_code=401, detail='Access locked. Enter your access code to continue.')


def secure_cookie_for_request(request: Request) -> bool:
    proto = (request.headers.get('x-forwarded-proto') or request.url.scheme or '').lower()
    return proto == 'https'


def set_gate_cookie(response: Response, request: Request) -> None:
    max_age = BYS_ACCESS_COOKIE_DAYS * 86400
    response.set_cookie(
        key=BYS_ACCESS_COOKIE_NAME,
        value=issue_gate_token(),
        max_age=max_age,
        httponly=True,
        secure=secure_cookie_for_request(request),
        samesite='lax',
        path='/',
    )


def clear_gate_cookie(response: Response) -> None:
    response.delete_cookie(key=BYS_ACCESS_COOKIE_NAME, path='/', samesite='lax')


def auth_headers() -> Dict[str, str]:
    return {'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'}


def int_schema(minimum: int = 0, maximum: int = 100) -> Dict[str, Any]:
    return {'type': 'integer', 'minimum': minimum, 'maximum': maximum}


def decode_schema(include_extracted: bool = False) -> Dict[str, Any]:
    props: Dict[str, Any] = {
        'verdict': {'type': 'string'},
        'meaning': {'type': 'string'},
        'flags': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 3, 'maxItems': 4},
        'guardrails': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 2, 'maxItems': 3},
        'tones': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['warmth', 'clarity', 'interest', 'respect', 'urgency'],
            'properties': {
                'warmth': int_schema(),
                'clarity': int_schema(),
                'interest': int_schema(),
                'respect': int_schema(),
                'urgency': int_schema(),
            },
        },
        'replies': {
            'type': 'array',
            'minItems': 3,
            'maxItems': 3,
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'required': ['style', 'text'],
                'properties': {
                    'style': {'type': 'string'},
                    'text': {'type': 'string'},
                },
            },
        },
    }
    required = ['verdict', 'meaning', 'flags', 'guardrails', 'tones', 'replies']
    if include_extracted:
        props['extracted_text'] = {'type': 'string'}
        props['extraction_confidence'] = int_schema(0, 100)
        required = ['extracted_text', 'extraction_confidence'] + required
    return {'name': 'decode_result', 'strict': True, 'schema': {'type': 'object', 'additionalProperties': False, 'required': required, 'properties': props}}


def score_schema() -> Dict[str, Any]:
    return {
        'name': 'send_score_result',
        'strict': True,
        'schema': {
            'type': 'object',
            'additionalProperties': False,
            'required': ['score', 'label', 'issue', 'breakdown', 'rewrites'],
            'properties': {
                'score': int_schema(0, 100),
                'label': {'type': 'string'},
                'issue': {'type': 'string'},
                'breakdown': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['frustration', 'clarity', 'warmth', 'pressure'],
                    'properties': {
                        'frustration': int_schema(),
                        'clarity': int_schema(),
                        'warmth': int_schema(),
                        'pressure': int_schema(),
                    },
                },
                'rewrites': {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['clear', 'warm', 'firm', 'short'],
                    'properties': {
                        'clear': {'type': 'string'},
                        'warm': {'type': 'string'},
                        'firm': {'type': 'string'},
                        'short': {'type': 'string'},
                    },
                },
            },
        },
    }


def parse_chat_json(resp_json: Dict[str, Any]) -> Dict[str, Any]:
    try:
        content = resp_json['choices'][0]['message']['content']
        return json.loads(content)
    except Exception as exc:
        if DEBUG:
            raise
        raise OpenAIError('Model response could not be parsed as structured JSON.') from exc


def post_chat(messages: list[dict[str, Any]], schema: Dict[str, Any], model: str) -> Dict[str, Any]:
    require_api_key()
    payload = {'model': model, 'messages': messages, 'temperature': 0.15, 'response_format': {'type': 'json_schema', 'json_schema': schema}}
    try:
        res = requests.post(f'{OPENAI_BASE_URL}/chat/completions', headers=auth_headers(), json=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise OpenAIError(f'Network error talking to OpenAI: {exc}') from exc
    if res.status_code >= 400:
        raise OpenAIError(f'OpenAI error {res.status_code}: {res.text[:500]}')
    return parse_chat_json(res.json())


def post_transcription(file_bytes: bytes, filename: str, content_type: Optional[str]) -> str:
    require_api_key()
    files = {'file': (filename or 'audio.m4a', file_bytes, content_type or 'application/octet-stream')}
    data = {'model': TRANSCRIBE_MODEL}
    headers = {'Authorization': f'Bearer {OPENAI_API_KEY}'}
    try:
        res = requests.post(f'{OPENAI_BASE_URL}/audio/transcriptions', headers=headers, files=files, data=data, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise OpenAIError(f'Network error during transcription: {exc}') from exc
    if res.status_code >= 400:
        raise OpenAIError(f'Transcription error {res.status_code}: {res.text[:500]}')
    data = res.json()
    text = data.get('text') or data.get('transcript') or ''
    if not text:
        raise OpenAIError('Empty transcription.')
    return text


def to_data_url(file_bytes: bytes, filename: str, content_type: Optional[str]) -> str:
    mime = content_type or mimetypes.guess_type(filename)[0] or 'image/png'
    encoded = base64.b64encode(file_bytes).decode('utf-8')
    return f'data:{mime};base64,{encoded}'

LANG_COPY = {
    'it': {
        'ui_name': 'Before You Send',
        'error_text': 'Inserisci un testo da analizzare.',
        'error_send': 'Inserisci un messaggio da valutare.',
        'error_audio': 'Carica un file audio o incolla una trascrizione.',
        'error_image': 'Carica uno screenshot o incolla il testo estratto.',
        'decode_system': 'Sei Before You Send, un communication copilot. Analizza un messaggio ricevuto in modo prudente, senza diagnosi, previsioni o consigli diretti. Rispondi in italiano semplice e mobile-friendly. Il verdict deve essere molto breve, descrittivo e mai oracolare: 2-6 parole. La meaning deve essere una sola frase breve, chiara e concreta. Dai 3 flag molto corte e utili, non generiche. Se il messaggio rassicura ma rimanda, rendilo esplicito. Evita parole vaghe come positivo o informale se non aggiungono valore. Includi 3 risposte pratiche, brevi e naturali. Le 3 risposte devono essere pronte da inviare, scritte come risposta diretta al mittente, mai riassunti o analisi del messaggio. I guardrail devono essere nella stessa lingua dell’output. I valori tones devono essere interi 0-100.',
        'vision_system': 'Sei Before You Send. Guarda questo screenshot di chat. Estrai il blocco di messaggio in arrivo più rilevante per il dubbio dell’utente. Se c’è un unico messaggio lungo e sostanzioso, trascrivilo per intero come blocco e non estrarre solo una frase isolata. Se il testo è poco leggibile, restituisci comunque il miglior tentativo in extracted_text e abbassa extraction_confidence. Poi analizza il tono in italiano in modo prudente, senza diagnosi né certezze assolute. Il verdict deve essere breve, descrittivo e mai oracolare. I flag devono essere corti e utili. Le 3 risposte devono essere pronte da inviare, non riassunti. I guardrail devono essere nella stessa lingua dell’output. I valori tones devono essere interi 0-100.',
        'score_system': 'Sei Before You Send. Valuta un messaggio che l’utente sta per inviare. Rispondi in italiano semplice, naturale e commerciale. Dai uno score 0-100: alto se il messaggio è chiaro, caldo, composto e con bassa pressione. La issue deve essere una frase breve e utile, non tecnica. Le 4 rewrite devono essere naturali, colloquiali, brevi e mai burocratiche. Evita formule come discutere la questione, provvedere, risolvere insieme, in merito. I valori di breakdown devono essere interi 0-100.',
        'verdict_reassure_defer': 'Ti rassicura, ma rimanda.',
        'meaning_reassure_defer': 'Il tono è gentile, ma rimanda senza chiarire bene quando.',
        'verdict_defer': 'Rimanda senza chiarire.',
        'meaning_defer': 'Il tono non è duro, ma sposta la questione più avanti senza dare un piano chiaro.',
        'verdict_offer': 'Condivide una proposta concreta.',
        'meaning_offer': 'Dà dettagli concreti per capire l’interesse e aprire una possibile trattativa.',
        'flags_map': {'ambiguity':'Ambiguità','ambiguous':'Ambiguità','unclear':'Poco chiaro','low effort':'Basso investimento','low_effort':'Basso investimento','weak close':'Chiusura debole','weak_close':'Chiusura debole','mixed signals':'Segnali misti','mixed_signals':'Segnali misti','future ambiguity':'Ambiguità sul futuro','future_ambiguity':'Ambiguità sul futuro','delay':'Rimanda','postponing':'Rimanda','postpone':'Rimanda','reassuring tone':'Tono rassicurante','reassuring':'Tono rassicurante','friendly tone':'Tono amichevole','friendly':'Tono amichevole','low urgency':'Bassa urgenza','not urgent':'Bassa urgenza','positive':'Tono positivo','informal':'Tono informale','pending_response':'In attesa di risposta','pending response':'In attesa di risposta','urgency':'Urgenza','professionalism':'Professionalità'},
        'generic_flags': {'Tono positivo','Tono informale'},
        'desired_flags_reassure': ['Rimanda','Tono rassicurante','Bassa urgenza'],
        'desired_flags_defer': ['Rimanda','Ambiguità sul futuro','Bassa urgenza'],
        'desired_flags_offer': ['Proposta commerciale','Dettagli concreti','Interesse ad avanzare'],
        'desired_flags_generic': ['Tono diretto','Messaggio concreto','Spazio di risposta'],
        'guardrail_fallback': ['Non trattarlo come una promessa chiara.','Non leggere il rinvio come rifiuto definitivo.'],
        'style_map': {'soft':'Più morbida','confident':'Più chiara','detached':'Più distaccata','clear':'Più chiara','warm':'Più calda','firm':'Più ferma','short':'Più breve','formal':'Neutra','concerned':'Amichevole','neutral':'Neutra','friendly':'Amichevole','casual':'Informale','supportive':'Di supporto','natural':'Naturale'},
        'reply_style_defaults': ['Neutra','Amichevole','Informale'],
        'reply_fallbacks_defer': [('Neutra','Va bene, quando hai un attimo scrivimi tu e vediamo.'),('Amichevole','Ok, quando hai più chiarezza scrivimi e vediamo.'),('Informale','Capito, sentiamoci più avanti quando è più chiaro.')],
        'reply_fallbacks_offer': [('Neutra','Grazie per i dettagli. Li guardo e ti dico.'),('Amichevole','Grazie, così è tutto più chiaro. Do un’occhiata e poi ci sentiamo.'),('Informale','Perfetto, grazie per avermelo mandato. Lo guardo e ti faccio sapere.')],
        'reply_fallbacks_generic': [('Neutra','Grazie, lo leggo con calma e ti rispondo.'),('Amichevole','Grazie per averlo spiegato. Lo guardo e ti dico qualcosa.'),('Informale','Perfetto, lo vedo e poi ti scrivo.')],
        'label_very_strong': 'Molto forte','label_good_base':'Buona base','label_review':'Da rivedere','label_rewrite':'Da riscrivere',
        'issue_default': 'Chiaro, ma si può rendere più naturale.','issue_rigid':'Chiaro, ma un po’ rigido.',
        'outgoing_rewrites_case': {'clear':'Ciao, ci sentiamo tra poco così chiariamo?','warm':'Ciao, se ti va ci sentiamo tra poco così chiariamo?','firm':'Ciao, tra poco ci sentiamo e chiarisco con te.','short':'Ci sentiamo tra poco per chiarire?'}
    },
    'en': {
        'ui_name': 'Before You Send',
        'error_text': 'Paste a message to analyze.',
        'error_send': 'Paste a message to score.',
        'error_audio': 'Upload audio or paste a transcript.',
        'error_image': 'Upload a screenshot or paste extracted text.',
        'decode_system': 'You are Before You Send, a communication copilot. Analyze a received message cautiously, with no diagnosis, prediction, or direct advice. Reply in simple mobile-friendly English. The verdict must be very short, descriptive, and never oracular: 2-6 words. The meaning must be one short, clear sentence. Give 3 very short, useful flags, not generic ones. If the message reassures but postpones, make that explicit. Avoid vague words like positive or informal unless they add value. Include 3 practical, natural replies. The 3 replies must be ready to send, written as direct replies to the sender, never summaries or analysis of the received message. Guardrails must stay in the same language as the rest of the output. Tone values must be integers 0-100.',
        'vision_system': 'You are Before You Send. Look at this chat screenshot. Extract the incoming message block most relevant to the user’s doubt. If there is one substantial long message, transcribe that whole block instead of isolating a single sentence. If the text is hard to read, still return your best attempt in extracted_text and lower extraction_confidence. Then analyze the tone in simple English, cautiously, with no diagnosis or certainty. The verdict must be short, descriptive, and never oracular. Flags must be short and useful. The 3 replies must be ready to send, not summaries. Guardrails must stay in the same language as the output. Tone values must be integers 0-100.',
        'score_system': 'You are Before You Send. Score a message the user is about to send. Reply in simple, natural, product-friendly English. Give a 0-100 score: high if the message is clear, warm, composed, and low-pressure. The issue must be one short helpful sentence, not technical. The 4 rewrites must be natural, conversational, short, and never bureaucratic. Avoid phrases like discuss the matter, provide, resolve together, regarding. Breakdown values must be integers 0-100.',
        'verdict_reassure_defer': 'Reassuring, but putting it off.',
        'meaning_reassure_defer': 'The tone is gentle, but it postpones things without making the timing clear.',
        'verdict_defer': 'Postpones without clarity.',
        'meaning_defer': 'The tone is not harsh, but it pushes the issue later without a clear plan.',
        'verdict_offer': 'Shares a concrete proposal.',
        'meaning_offer': 'It gives concrete details to test interest and open a possible negotiation.',
        'flags_map': {'ambiguity':'Ambiguity','ambiguous':'Ambiguity','unclear':'Unclear','low effort':'Low effort','low_effort':'Low effort','weak close':'Weak close','weak_close':'Weak close','mixed signals':'Mixed signals','mixed_signals':'Mixed signals','future ambiguity':'Future ambiguity','future_ambiguity':'Future ambiguity','delay':'Postpones','postponing':'Postpones','postpone':'Postpones','reassuring tone':'Reassuring tone','reassuring':'Reassuring tone','friendly tone':'Friendly tone','friendly':'Friendly tone','low urgency':'Low urgency','not urgent':'Low urgency','positive':'Positive tone','informal':'Informal tone','pending_response':'Pending response','pending response':'Pending response','urgency':'Urgency','professionalism':'Professionalism'},
        'generic_flags': {'Positive tone','Informal tone'},
        'desired_flags_reassure': ['Postpones','Reassuring tone','Low urgency'],
        'desired_flags_defer': ['Postpones','Future ambiguity','Low urgency'],
        'desired_flags_offer': ['Commercial proposal','Concrete details','Interest in moving forward'],
        'desired_flags_generic': ['Direct tone','Concrete message','Room to respond'],
        'guardrail_fallback': ['Do not read this as a clear promise.','Do not read the postponement as a final rejection.'],
        'style_map': {'soft':'Softer','confident':'Clearer','detached':'More detached','clear':'Clearer','warm':'Warmer','firm':'Firmer','short':'Shorter','formal':'Neutral','concerned':'Friendly','neutral':'Neutral','friendly':'Friendly','casual':'Casual','supportive':'Supportive','natural':'Natural'},
        'reply_style_defaults': ['Neutral','Friendly','Casual'],
        'reply_fallbacks_defer': [('Neutral','That works — message me when you have a clearer time.'),('Friendly','Okay, write me when you know better and we can sort it out.'),('Casual','Got it, let’s talk later when it is clearer.')],
        'reply_fallbacks_offer': [('Neutral','Thanks for the details. I’ll review them and get back to you.'),('Friendly','Thanks, this helps a lot. I’ll take a look and we can continue.'),('Casual','Perfect, thanks for sending it. I’ll check it and let you know.')],
        'reply_fallbacks_generic': [('Neutral','Thanks, I’ll read it properly and reply soon.'),('Friendly','Thanks for explaining it. I’ll take a look and get back to you.'),('Casual','Perfect, I’ll check it and message you back.')],
        'label_very_strong': 'Very strong','label_good_base':'Good base','label_review':'Needs work','label_rewrite':'Rewrite it',
        'issue_default': 'Clear, but it could sound more natural.','issue_rigid':'Clear, but a bit stiff.',
        'outgoing_rewrites_case': {'clear':'Hey, can we talk soon so we can clear this up?','warm':'Hey, if you want, can we talk soon and clear this up?','firm':'Hey, let’s talk soon and sort this out.','short':'Can we talk soon to clear this up?'}
    },
    'es': {
        'ui_name': 'Before You Send',
        'error_text': 'Pega un mensaje para analizar.',
        'error_send': 'Pega un mensaje para evaluar.',
        'error_audio': 'Sube un audio o pega una transcripción.',
        'error_image': 'Sube una captura o pega el texto extraído.',
        'decode_system': 'Eres Before You Send, un copiloto de comunicación. Analiza un mensaje recibido con prudencia, sin diagnósticos, predicciones ni consejos directos. Responde en español simple y móvil. El veredicto debe ser muy corto, descriptivo y nunca oracular: 2-6 palabras. El significado debe ser una sola frase breve y clara. Da 3 señales muy cortas y útiles, no genéricas. Si el mensaje tranquiliza pero aplaza, hazlo explícito. Evita palabras vagas como positivo o informal si no aportan valor. Incluye 3 respuestas prácticas y naturales. Las 3 respuestas deben estar listas para enviar, escritas como respuesta directa al remitente, nunca como resumen o análisis del mensaje. Los guardrails deben quedar en el mismo idioma del resto de la respuesta. Los valores tones deben ser enteros 0-100.',
        'vision_system': 'Eres Before You Send. Mira esta captura de chat. Extrae el bloque de mensaje entrante más relevante para la duda del usuario. Si hay un único mensaje largo y sustancioso, transcríbelo entero como bloque y no saques solo una frase aislada. Si el texto es difícil de leer, devuelve igualmente tu mejor intento en extracted_text y baja extraction_confidence. Luego analiza el tono en español simple, con prudencia, sin diagnósticos ni certezas absolutas. El veredicto debe ser corto, descriptivo y nunca oracular. Las señales deben ser cortas y útiles. Las 3 respuestas deben estar listas para enviar, no ser resúmenes. Los guardrails deben quedar en el mismo idioma del output. Los valores tones deben ser enteros 0-100.',
        'score_system': 'Eres Before You Send. Evalúa un mensaje que el usuario está a punto de enviar. Responde en español simple, natural y orientado a producto. Da una puntuación de 0-100: alta si el mensaje es claro, cálido, sereno y con poca presión. La issue debe ser una frase breve y útil, nada técnica. Las 4 rewrite deben ser naturales, coloquiales, breves y nunca burocráticas. Evita fórmulas como tratar el asunto, proceder, resolver juntos, con respecto a. Los valores breakdown deben ser enteros 0-100.',
        'verdict_reassure_defer': 'Te tranquiliza, pero lo deja para más tarde.',
        'meaning_reassure_defer': 'El tono es amable, pero lo deja para más tarde sin dejar claro cuándo.',
        'verdict_defer': 'Aplaza sin aclarar.',
        'meaning_defer': 'El tono no es duro, pero empuja el tema hacia más adelante sin un plan claro.',
        'verdict_offer': 'Comparte una propuesta concreta.',
        'meaning_offer': 'Da detalles concretos para comprobar interés y abrir una posible negociación.',
        'flags_map': {'ambiguity':'Ambigüedad','ambiguous':'Ambigüedad','unclear':'Poco claro','low effort':'Poco esfuerzo','low_effort':'Poco esfuerzo','weak close':'Cierre débil','weak_close':'Cierre débil','mixed signals':'Señales mixtas','mixed_signals':'Señales mixtas','future ambiguity':'Ambigüedad futura','future_ambiguity':'Ambigüedad futura','delay':'Aplaza','postponing':'Aplaza','postpone':'Aplaza','reassuring tone':'Tono tranquilizador','reassuring':'Tono tranquilizador','friendly tone':'Tono amable','friendly':'Tono amable','low urgency':'Baja urgencia','not urgent':'Baja urgencia','positive':'Tono positivo','informal':'Tono informal','pending_response':'Respuesta pendiente','pending response':'Respuesta pendiente','urgency':'Urgencia','professionalism':'Profesionalidad'},
        'generic_flags': {'Tono positivo','Tono informal'},
        'desired_flags_reassure': ['Aplaza','Tono tranquilizador','Baja urgencia'],
        'desired_flags_defer': ['Aplaza','Ambigüedad futura','Baja urgencia'],
        'desired_flags_offer': ['Propuesta comercial','Detalles concretos','Interés por avanzar'],
        'desired_flags_generic': ['Tono directo','Mensaje concreto','Espacio para responder'],
        'guardrail_fallback': ['No lo tomes como una promesa clara.','No leas el aplazamiento como un rechazo definitivo.'],
        'style_map': {'soft':'Más suave','confident':'Más clara','detached':'Más distante','clear':'Más clara','warm':'Más cálida','firm':'Más firme','short':'Más breve','formal':'Neutra','concerned':'Amable','neutral':'Neutra','friendly':'Amable','casual':'Informal','supportive':'De apoyo','natural':'Natural'},
        'reply_style_defaults': ['Neutra','Amable','Informal'],
        'reply_fallbacks_defer': [('Neutra','Vale, escríbeme cuando tengas algo más claro y lo vemos.'),('Amable','Perfecto, cuando lo tengas más claro me dices y lo vemos.'),('Informal','Entendido, lo vemos más adelante cuando te encaje.')],
        'reply_fallbacks_offer': [('Neutra','Gracias por los detalles. Lo reviso y te digo algo.'),('Amable','Gracias, me ayuda verlo así de claro. Lo miro y seguimos.'),('Informal','Perfecto, gracias por pasarlo. Le echo un vistazo y te digo.')],
        'reply_fallbacks_generic': [('Neutra','Gracias, lo leo con calma y te respondo.'),('Amable','Gracias por explicarlo. Lo miro y te digo algo.'),('Informal','Perfecto, lo veo y te contesto.')],
        'label_very_strong': 'Muy fuerte','label_good_base':'Buena base','label_review':'Hay que revisar','label_rewrite':'Hay que reescribir',
        'issue_default': 'Es claro, pero podría sonar más natural.','issue_rigid':'Es claro, pero un poco rígido.',
        'outgoing_rewrites_case': {'clear':'Hola, ¿hablamos en un rato y lo aclaramos?','warm':'Hola, si te va bien, ¿hablamos en un rato y lo aclaramos?','firm':'Hola, hablamos en un rato y lo aclaramos.','short':'¿Hablamos en un rato para aclararlo?'}
    }
}

GUARDRAIL_TRANSLATIONS = {
    'it': {
        'no personal information': 'Nessun dato personale.',
        'no personal data': 'Nessun dato personale.',
        'no sensitive data': 'Nessun dato sensibile.',
        'no sensitive content': 'Nessun contenuto sensibile.',
        'do not read this as a clear promise': 'Non trattarlo come una promessa chiara.',
        'do not read the postponement as a final rejection': 'Non leggere il rinvio come un rifiuto definitivo.',
        'not a clear promise': 'Non trattarlo come una promessa chiara.',
        'not a final rejection': 'Non leggerlo come un rifiuto definitivo.',
    },
    'en': {
        'no personal information': 'No personal information.',
        'no personal data': 'No personal data.',
        'no sensitive data': 'No sensitive data.',
        'no sensitive content': 'No sensitive content.',
        'do not read this as a clear promise': 'Do not read this as a clear promise.',
        'do not read the postponement as a final rejection': 'Do not read the postponement as a final rejection.',
        'not a clear promise': 'Not a clear promise.',
        'not a final rejection': 'Not a final rejection.',
    },
    'es': {
        'no personal information': 'No hay datos personales.',
        'no personal data': 'No hay datos personales.',
        'no sensitive data': 'No hay datos sensibles.',
        'no sensitive content': 'No hay contenido sensible.',
        'do not read this as a clear promise': 'No lo tomes como una promesa clara.',
        'do not read the postponement as a final rejection': 'No leas el aplazamiento como un rechazo definitivo.',
        'not a clear promise': 'No lo tomes como una promesa clara.',
        'not a final rejection': 'No lo leas como un rechazo definitivo.',
    },
}

ENGLISH_GUARDRAIL_HINTS = (
    'message', 'tone', 'personal information', 'personal data', 'sensitive data', 'sensitive content',
    'clear promise', 'final rejection', 'not a promise', 'not final',
)



def copy(lang: str, key: str):
    return LANG_COPY[canonical_lang(lang)][key]


def clean_line(text: str, max_len: int = 120) -> str:
    text = re.sub(r'\s+', ' ', (text or '')).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip(' ,.;:') + '…'


def de_bureaucratize(text: str) -> str:
    if not text:
        return text
    replacements = {
        'discutere la questione': 'parlarne','la questione': 'la cosa','risolvere la situazione': 'chiarire','risolvere insieme': 'chiarire','provvedere': 'fare','incontrarci per risolvere': 'sentirci per chiarire','ci vediamo tra poco per risolvere': 'ci sentiamo tra poco così chiariamo','ci vediamo tra poco per discutere': 'ci sentiamo tra poco per parlarne',
        'discuss the matter':'talk about it','the matter':'this','resolve together':'clear this up','regarding':'about','we can meet soon to resolve':'we can talk soon to clear this up',
        'tratar el asunto':'hablar de esto','resolver juntos':'aclararlo','con respecto a':'sobre'
    }
    out = text
    for k, v in replacements.items():
        out = re.sub(re.escape(k), v, out, flags=re.IGNORECASE)
    out = re.sub(r'\s+', ' ', out).strip()
    return out


def map_decode_flag(flag: str, lang: str) -> str:
    raw = (flag or '').strip().lower()
    mapping = copy(lang, 'flags_map')
    return mapping.get(raw, clean_line(flag, 28).capitalize())


def count_words(text: str) -> int:
    return len(re.findall(r'\w+', text or '', flags=re.UNICODE))


def detect_offer_details(text: str) -> bool:
    t = (text or '').lower()
    if not t:
        return False
    longish = count_words(t) >= 28 or len(t) >= 180
    keyword_patterns = [
        r'licen', r'license', r'licenza', r'proyect', r'project', r'progetto', r'negociaci', r'negozia',
        r'precio', r'price', r'prezzo', r'coste', r'costo', r'cost', r'venta', r'sale', r'vendita',
        r'instalaci', r'installation', r'impiant', r'documentaci', r'documentation', r'documentaz',
        r'certific', r'bolet', r'oca\b', r'daikin', r'climatiz', r'climate', r'aria',
    ]
    hits = sum(1 for pattern in keyword_patterns if re.search(pattern, t))
    if '€' in t:
        hits += 1
    if re.search(r'\b\d{2,3}(?:[., ]\d{3})+(?:\s*€)?\b', t):
        hits += 1
    return longish and hits >= 2


def result_looks_defer(verdict: str, meaning: str, flags: list[str]) -> bool:
    joined = ' '.join([verdict or '', meaning or '', *[str(f) for f in flags]]).lower()
    patterns = [
        'postpon', 'later', 'delay', 'defer', 'future ambiguity', 'low urgency',
        'rimand', 'più avanti', 'piu avanti', 'bassa urgenza',
        'aplaz', 'más adelante', 'mas adelante', 'ambigüedad futura', 'baja urgencia',
    ]
    return any(token in joined for token in patterns)


def normalize_guardrail_line(text: str, lang: str) -> str:
    line = clean_line(text, 90)
    if not line:
        return ''
    normalized = re.sub(r'\s+', ' ', line).strip().lower().rstrip('.!')
    translated = GUARDRAIL_TRANSLATIONS.get(lang, {}).get(normalized)
    if translated:
        return translated
    if lang != 'en' and any(token in normalized for token in ENGLISH_GUARDRAIL_HINTS):
        return ''
    return line


def looks_like_analysis_reply(text: str) -> bool:
    t = (text or '').strip().lower()
    if not t:
        return True
    if re.search(r'^\s*(the|il|el)\s+(message|messaggio|mensaje)\b', t):
        return True
    if re.search(r'^\s*(the|il|el)\s+(tone|tono)\b', t):
        return True
    if re.search(r'^\s*(it|esto|este|questo|se)\s+(seems|looks|appears|parece|sembra|presenta|presentan|expresa|esprime)\b', t):
        return True
    meta_tokens = [
        'message', 'messaggio', 'mensaje', 'tone', 'tono', 'proposal', 'proposta', 'propuesta',
        'project', 'progetto', 'proyecto', 'license', 'licenza', 'licencia', 'cost', 'costo', 'precio', 'price',
    ]
    direct_tokens = [
        'thanks', 'thank you', 'okay', 'ok', 'got it', "i'll", 'i will', "let's", 'can we',
        'grazie', 'va bene', 'capito', 'ti dico', 'ci sentiamo',
        'gracias', 'vale', 'entiendo', 'perfecto', 'lo reviso', 'lo miro', 'te digo', 'te respondo', 'hablamos',
    ]
    meta_count = sum(1 for token in meta_tokens if token in t)
    direct = any(token in t for token in direct_tokens)
    if meta_count >= 2 and not direct:
        return True
    if count_words(t) > 18 and not direct and any(token in t for token in ('parece', 'seems', 'appears', 'sembra', 'expresa', 'presenta', 'presentan', 'tone', 'tono', 'message', 'mensaje', 'messaggio')):
        return True
    return False


def reply_fallbacks_for_context(lang: str, defer: bool, offer: bool) -> list[tuple[str, str]]:
    if offer:
        return copy(lang, 'reply_fallbacks_offer')
    if defer:
        return copy(lang, 'reply_fallbacks_defer')
    return copy(lang, 'reply_fallbacks_generic')


def detect_defer(text: str) -> bool:
    t = (text or '').lower()
    if detect_offer_details(t):
        return False
    explicit_patterns = [r'più avanti', r'piu avanti', r'più tardi', r'piu tardi', r'più in là', r'piu in la', r'non serve adesso', r'non ora', r'ora no', r'rimand', r'poi vediamo', r'un altro giorno', r'later', r'not now', r'not urgent', r'we can see later', r'another day', r'después', r'más adelante', r'mas adelante', r'más tarde', r'mas tarde', r'otro día', r'luego vemos', r'hablamos luego', r'ahora no', r'aplaz']
    if any(re.search(p, t) for p in explicit_patterns):
        return True
    soft_patterns = [r'vediamo', r'luego']
    return count_words(t) <= 18 and any(re.search(p, t) for p in soft_patterns)


def detect_reassure(text: str) -> bool:
    t = (text or '').lower()
    patterns = [r'tranquill', r'non serve', r'non preoccup', r'nessun problema', r'tutto bene', r'calma', r'no worries', r'no problem', r'don\'t worry', r'it\'s fine', r'todo bien', r'sin problema', r'no pasa nada', r'tranqui']
    return any(re.search(p, t) for p in patterns)


def normalize_style_label(label: str, lang: str) -> str:
    raw = (label or '').strip().lower()
    mapping = copy(lang, 'style_map')
    return mapping.get(raw, label or 'Reply')


def normalize_decode_result(result: Dict[str, Any], source_text: str, lang: str) -> Dict[str, Any]:
    text = source_text or result.get('input') or result.get('extracted_text') or ''
    offer = detect_offer_details(text)
    defer = detect_defer(text)
    reassure = detect_reassure(text)

    verdict = clean_line(result.get('verdict', ''), 48)
    meaning = clean_line(result.get('meaning', ''), 140)
    raw_flags = [str(f) for f in result.get('flags', []) if f]
    defer_like_result = result_looks_defer(verdict, meaning, raw_flags)

    if offer and defer_like_result:
        verdict = copy(lang, 'verdict_offer')
        meaning = copy(lang, 'meaning_offer')
        defer = False
        reassure = False
    elif defer and reassure:
        verdict = copy(lang, 'verdict_reassure_defer')
        meaning = copy(lang, 'meaning_reassure_defer')
    elif defer and ('postpon' not in verdict.lower() and 'postpon' not in meaning.lower() and 'rimand' not in verdict.lower() and 'aplaz' not in verdict.lower()):
        verdict = copy(lang, 'verdict_defer')
        meaning = copy(lang, 'meaning_defer')

    flags = [map_decode_flag(f, lang) for f in raw_flags]
    generic = copy(lang, 'generic_flags')
    if offer and defer_like_result:
        desired = copy(lang, 'desired_flags_offer')
        flags = desired + [f for f in flags if f not in desired and f not in generic]
    elif defer:
        desired = copy(lang, 'desired_flags_reassure') if reassure else copy(lang, 'desired_flags_defer')
        flags = desired + [f for f in flags if f not in desired and f not in generic]
    else:
        desired = copy(lang, 'desired_flags_generic')
        flags = [f for f in flags if f not in generic] + [f for f in flags if f in generic]

    dedup = []
    for f in flags:
        if f and f not in dedup:
            dedup.append(f)
    flags = dedup[:3] if len(dedup) >= 3 else (dedup + [f for f in desired if f not in dedup])[:3]

    guardrails = []
    for item in result.get('guardrails', []):
        normalized = normalize_guardrail_line(item, lang)
        if normalized and normalized not in guardrails:
            guardrails.append(normalized)
    fallback_guardrails = copy(lang, 'guardrail_fallback')
    for item in fallback_guardrails:
        if len(guardrails) >= 2:
            break
        if item not in guardrails:
            guardrails.append(item)
    guardrails = guardrails[:2]

    fallback_replies = reply_fallbacks_for_context(lang, defer=defer, offer=offer and (defer_like_result or not defer))
    replies = result.get('replies', [])[:3]
    cleaned_replies = []
    seen_reply_texts = set()
    default_styles = copy(lang, 'reply_style_defaults')

    for idx in range(3):
        raw_reply = replies[idx] if idx < len(replies) else {}
        txt = clean_line(de_bureaucratize(raw_reply.get('text', '')), 120)
        if looks_like_analysis_reply(txt) or txt.lower() in seen_reply_texts:
            txt = fallback_replies[idx][1]
        seen_reply_texts.add(txt.lower())
        cleaned_replies.append({'style': default_styles[idx], 'text': txt})

    result['verdict'] = verdict
    result['meaning'] = meaning
    result['flags'] = flags
    result['guardrails'] = guardrails
    result['replies'] = cleaned_replies
    return result


def normalize_score_result(result: Dict[str, Any], source_text: str, lang: str) -> Dict[str, Any]:
    text = (source_text or '').strip()
    score = int(result.get('score', 0))
    if score >= 85:
        label = copy(lang, 'label_very_strong')
    elif score >= 70:
        label = copy(lang, 'label_good_base')
    elif score >= 55:
        label = copy(lang, 'label_review')
    else:
        label = copy(lang, 'label_rewrite')

    issue = clean_line(result.get('issue', ''), 100)
    if ('natural' in issue.lower() or 'natural' in de_bureaucratize(issue).lower()) and 'rigid' not in issue.lower() and 'rígido' not in issue.lower() and 'rigido' not in issue.lower():
        issue = copy(lang, 'issue_rigid')
    if not issue:
        issue = copy(lang, 'issue_default')

    rewrites = result.get('rewrites', {})
    cleaned = {k: clean_line(de_bureaucratize(rewrites.get(k, '')), 120 if k != 'short' else 90) for k in ['clear','warm','firm','short']}

    low = text.lower()
    if ('ci vediamo' in low or 'ci sentiamo' in low or 'see you' in low or 'talk soon' in low or 'nos vemos' in low or 'hablamos' in low) and ('risolver' in low or 'clear' in low or 'aclar' in low):
        cleaned = copy(lang, 'outgoing_rewrites_case')
        if score < 75:
            label = copy(lang, 'label_good_base')
        issue = copy(lang, 'issue_rigid')

    result['label'] = label
    result['issue'] = issue
    result['rewrites'] = cleaned
    return result


def decode_from_text(text: str, lang: str) -> Dict[str, Any]:
    clean_text = text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail=copy(lang, 'error_text'))
    messages = [{'role':'developer','content': copy(lang, 'decode_system')}, {'role':'user','content': f"Received message or transcript:\n\n{clean_text}"}]
    result = post_chat(messages, decode_schema(), TEXT_MODEL)
    result = normalize_decode_result(result, clean_text, lang)
    result['source'] = 'text'
    result['input'] = clean_text
    result['lang'] = lang
    return result


def decode_from_image(file_bytes: bytes, filename: str, content_type: Optional[str], lang: str) -> Dict[str, Any]:
    data_url = to_data_url(file_bytes, filename, content_type)
    messages = [
        {'role':'developer','content': copy(lang, 'vision_system')},
        {'role':'user','content':[{'type':'text','text':'Analyze this chat screenshot and return structured JSON.'},{'type':'image_url','image_url':{'url':data_url,'detail':'low'}}]}
    ]
    result = post_chat(messages, decode_schema(include_extracted=True), VISION_MODEL)
    result = normalize_decode_result(result, result.get('extracted_text', ''), lang)
    result['source'] = 'image'; result['input'] = result.get('extracted_text', ''); result['lang'] = lang
    return result


def score_outgoing_text(text: str, lang: str) -> Dict[str, Any]:
    clean_text = text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail=copy(lang, 'error_send'))
    messages = [{'role':'developer','content': copy(lang, 'score_system')}, {'role':'user','content': f"Message to send:\n\n{clean_text}"}]
    result = post_chat(messages, score_schema(), TEXT_MODEL)
    result = normalize_score_result(result, clean_text, lang)
    result['input'] = clean_text
    result['lang'] = lang
    return result


@app.get('/api/health')
def api_health() -> Dict[str, Any]:
    return {
        'ok': True,
        'apiConfigured': bool(OPENAI_API_KEY),
        'gateEnabled': gate_enabled(),
        'models': {'text': TEXT_MODEL, 'vision': VISION_MODEL, 'transcribe': TRANSCRIBE_MODEL},
        'supportedLanguages': sorted(SUPPORTED_LANGS),
    }


@app.get('/api/access/status')
def api_access_status(request: Request) -> Dict[str, Any]:
    return {
        'enabled': gate_enabled(),
        'unlocked': request_has_gate_access(request),
        'accessPage': BYS_ACCESS_PAGE_URL,
    }


@app.post('/api/access/unlock')
def api_access_unlock(payload: AccessPayload, request: Request) -> JSONResponse:
    if not gate_enabled():
        return JSONResponse({'ok': True, 'enabled': False, 'unlocked': True, 'accessPage': BYS_ACCESS_PAGE_URL})
    if normalize_access_code(payload.code) != normalize_access_code(BYS_ACCESS_CODE):
        raise HTTPException(status_code=403, detail='Invalid access code.')
    response = JSONResponse({'ok': True, 'enabled': True, 'unlocked': True, 'accessPage': BYS_ACCESS_PAGE_URL})
    set_gate_cookie(response, request)
    return response


@app.post('/api/access/logout')
def api_access_logout() -> JSONResponse:
    response = JSONResponse({'ok': True})
    clear_gate_cookie(response)
    return response


@app.post('/api/decode/text')
def api_decode_text(request: Request, payload: TextPayload) -> Dict[str, Any]:
    require_gate_access(request)
    lang = canonical_lang(payload.lang)
    try:
        return decode_from_text(payload.text, lang)
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/score/text')
def api_score_text(request: Request, payload: TextPayload) -> Dict[str, Any]:
    require_gate_access(request)
    lang = canonical_lang(payload.lang)
    try:
        return score_outgoing_text(payload.text, lang)
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/decode/audio')
async def api_decode_audio(request: Request, file: Optional[UploadFile] = File(default=None), transcript: Optional[str] = Form(default=None), lang: Optional[str] = Form(default='it')) -> Dict[str, Any]:
    require_gate_access(request)
    lang = canonical_lang(lang)
    try:
        if transcript and transcript.strip():
            base = decode_from_text(transcript, lang)
            base['source'] = 'voice'; base['transcript'] = transcript.strip(); return base
        if not file:
            raise HTTPException(status_code=400, detail=copy(lang, 'error_audio'))
        file_bytes = await file.read()
        text = post_transcription(file_bytes, file.filename or 'audio.m4a', file.content_type)
        base = decode_from_text(text, lang)
        base['source'] = 'voice'; base['transcript'] = text; return base
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post('/api/decode/image')
async def api_decode_image(request: Request, file: Optional[UploadFile] = File(default=None), extracted_text: Optional[str] = Form(default=None), lang: Optional[str] = Form(default='it')) -> Dict[str, Any]:
    require_gate_access(request)
    lang = canonical_lang(lang)
    try:
        if extracted_text and extracted_text.strip() and not file:
            base = decode_from_text(extracted_text, lang)
            base['source'] = 'image'; base['extracted_text'] = extracted_text.strip(); base['extraction_confidence'] = 100; return base
        if not file:
            raise HTTPException(status_code=400, detail=copy(lang, 'error_image'))
        file_bytes = await file.read()
        return decode_from_image(file_bytes, file.filename or 'screenshot.png', file.content_type, lang)
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get('/api/config')
def api_config(request: Request) -> Dict[str, Any]:
    require_gate_access(request)
    return {'demoSamples': True, 'supports': {'text': True, 'voice': True, 'image': True, 'shareCard': True}, 'supportedLanguages': sorted(SUPPORTED_LANGS)}


@app.get('/favicon.ico', include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / 'assets' / 'icon-192.png')


app.mount('/', StaticFiles(directory=str(STATIC_DIR), html=True), name='static')
