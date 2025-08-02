# app.py

import os
import email
from pyexpat.errors import messages
from imapclient import IMAPClient
from openai import OpenAI
import os
import ssl
import smtplib
from email.message import EmailMessage
from datetime import datetime

from dotenv import load_dotenv

from email.policy import default
import streamlit as st

# ваш модуль с функциями сортировки и отправки
# from mail_tasks import sort_emails, send_replies, get_mail_counts, ping_ai

load_dotenv()

IMAP_HOST = os.getenv('IMAP_HOST')
IMAP_PORT = int(os.getenv('IMAP_PORT', 993))
IMAP_USER = os.getenv('IMAP_USER')
IMAP_PASS = os.getenv('IMAP_PASS')

SMTP_HOST = os.getenv('SMTP_HOST')
SMTP_PORT = int(os.getenv('SMTP_PORT', 465))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASS = os.getenv('SMTP_PASS')

SRC_FOLDER = os.getenv('SRC_FOLDER', 'selected')
SRC_FOLDER_STATUS = os.getenv('SRC_FOLDER_STATUS', 'UNSEEN')
ACTIVATION_REQ_FOLDER = os.getenv('ACTIVATION_REQ_FOLDER', 'selected.activation_request')
OTHER_TOPIC_FOLDER = os.getenv('OTHER_TOPIC_FOLDER', 'selected.other_topic')
ACTIVATION_REQ_ANSWERED_FOLDER = os.getenv('ACTIVATION_REQ_ANSWERED_FOLDER', 'selected.activation_request_answered')

# openai.api_key = os.getenv('OPENAI_API_KEY')
# openai.api_base = os.getenv('OPENAI_API_BASE')
MODEL = os.getenv('OPENAI_MODEL', 'gpt-3.5-turbo')
AI_BASE_URL = os.getenv("OPENAI_API_BASE")
AI_KEY = os.getenv("OPENAI_API_KEY")

TEMPLATE = os.getenv('TEMPLATE', '')


# Критерий: свободный текст-промпт
CLASSIFICATION_PROMPT = os.getenv('CLASSIFICATION_PROMPT')

# Создаём глобальный клиент (чтобы не инициализировать каждый вызов)
ai_client = OpenAI(
    base_url = AI_BASE_URL,
    api_key=  AI_KEY
)

def get_mail_counts():
    with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True) as client:
        client.login(IMAP_USER, IMAP_PASS)
        client.select_folder(SRC_FOLDER)
        # client.select_folder('selected.other_topics')
        

        # Получаем все письма (можно заменить 'ALL' на 'UNSEEN')
        src_count = len(client.search('UNSEEN'))
        client.select_folder(ACTIVATION_REQ_FOLDER)
        activation_req_count = len(client.search('ALL'))
        client.select_folder(OTHER_TOPIC_FOLDER)
        other_topic_count = len(client.search('ALL'))
        client.logout()
    return {
        "to_sort": src_count,
        "activation_req_count": activation_req_count,
        "other_topic_count": other_topic_count
    }


def ping_ai():
    try:
        response = ai_client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Ты тут?"}],
            max_tokens=10
        )
        ai_response = response.choices[0].message.content.strip()
        return True, f"'{MODEL}', ты тут?: {ai_response}"
    except Exception as e:
        return False, f"AI '{MODEL}' недоступен: {str(e)}"

def classify(text: str) -> bool:
    resp = ai_client.chat.completions.create(
        model= MODEL,
        messages=[
            {"role": "system", "content": CLASSIFICATION_PROMPT},
            {"role": "user",   "content": text}
        ],
        temperature=0
    )
    answer = resp.choices[0].message.content.strip().upper()
    return answer.startswith("YES")


def extract_plain_text(msg):
    if not isinstance(msg, email.message.EmailMessage):
        msg = email.message_from_bytes(msg.as_bytes(), policy=default)
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain' and not part.get_content_disposition():
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or 'utf-8'
        return payload.decode(charset, errors='replace')
    return ""
    
    
def sort_emails(logger=None):
    if logger: logger(f"Подключаемся к IMAP серверу {IMAP_HOST}...")
    
    with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True) as client:
        if logger: logger(f"Аутентифицируемся как {IMAP_USER}...")
        client.login(IMAP_USER, IMAP_PASS)
        if logger: logger(f"Выбираем папку {SRC_FOLDER}...")
        client.select_folder(SRC_FOLDER)
        # client.select_folder('selected.other_topics')
        

        # Получаем все письма (можно заменить 'ALL' на 'UNSEEN')
        messages = client.search('UNSEEN')
        if logger: logger(f"Найдено {len(messages)} писем для сортировки.", 0, len(messages))
            
        resp = client.fetch(messages, ['RFC822'])
        for idx, (msgid, data) in enumerate(resp.items()):
            raw = data[b'RFC822']
            msg = email.message_from_bytes(raw, policy=default)
            body = extract_plain_text(msg)
            is_classified = classify(body)
            if is_classified:
                client.move(msgid, ACTIVATION_REQ_FOLDER)
                if logger:
                    logger(f"↩ Перемещаем {msg['Subject']!r} в {ACTIVATION_REQ_FOLDER}", idx, len(messages))

            else:
                client.move(msgid, OTHER_TOPIC_FOLDER)
                if logger:
                    logger(f"✔ Перемещаем {msg['Subject']!r} в {OTHER_TOPIC_FOLDER}", idx, len(messages))
        if logger: logger("Сортировка писем завершена.")            
        client.logout()

def quote_text(orig_body: str) -> str:
    return '\n'.join('> ' + line for line in orig_body.splitlines())


def send_replies(logger=None):

    # 1) Получаем письма для ответа
    if logger: logger(f"Подключаемся к IMAP серверу {IMAP_HOST}...")
    with IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True) as client:
        if logger: logger(f"Аутентифицируемся как {IMAP_USER}...")
        client.login(IMAP_USER, IMAP_PASS)
        if logger: logger(f"Выбираем папку {ACTIVATION_REQ_FOLDER}...")
        client.select_folder(ACTIVATION_REQ_FOLDER)
        
        msgs = client.search('ALL')
        if logger: logger(f"Найдено {len(msgs)} писем для шаблонного ответа.", 0, len(msgs))

        resp = client.fetch(msgs, ['RFC822'])
        to_remove = []

        # 2) Отправляем ответы
        context = ssl.create_default_context()
        if logger: logger(f"Подключаемся к SMTP серверу {SMTP_HOST}...")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()  # Can be omitted
            smtp.starttls(context=context)
            smtp.ehlo()  # Can be omitted
            if logger: logger(f"Аутентифицируемся как {SMTP_USER}...")
            smtp.login(SMTP_USER, SMTP_PASS)

            for idx, (msgid, data) in enumerate(resp.items()):
                raw = data[b'RFC822']
                orig = email.message_from_bytes(raw, policy=default)
                plain = extract_plain_text(orig)

                reply = EmailMessage()
                reply['Subject'] = 'Re: ' + orig.get('Subject', '')
                reply['From'] = SMTP_USER
                reply['To'] = orig.get('From')
                if orig.get('Message-ID'):
                    reply['In-Reply-To'] = orig['Message-ID']

                body = f"{TEMPLATE}\n{quote_text(plain)}"
                reply.set_content(body)

                smtp.send_message(reply)
                # print(f"✉ Отправлено: {orig.get('From')} | {orig.get('Subject')}")
                if logger: logger(f"✉ Отправлено: {orig.get('From')} | {orig.get('Subject')}", idx, len(resp))

                raw = reply.as_bytes()
                # Флаги и время по вашему вкусу:
                FLAGS = [b'\\Seen']
                # Название папки «Sent» может отличаться:
                SENT_FOLDER = 'Sent Messages'  
                client.append(SENT_FOLDER, raw, FLAGS, datetime.now())
                
                
                
                client.move(msgid, ACTIVATION_REQ_ANSWERED_FOLDER)

                # to_remove.append(msgid)

        # 3) Пометим как отвеченные и/или удалим из папки
        #    (если не нужно хранить копию)
        # client.delete_messages(to_remove)
        client.expunge()

        client.logout()
        if logger: logger("Сортировка писем завершена.")




def update_folder_metrics():
    """
    Считает метрики по папкам и выводит их в сайдбар.
    Вызываем эту функцию при старте и после каждой операции.
    """
    try:
        counts = get_mail_counts()
        to_sort_ph.metric(f"Письма для сортировки **'{SRC_FOLDER}'** ({SRC_FOLDER_STATUS}) ", counts["to_sort"])
        activation_ph.metric( label=
            f"Запросов на активацию найдено,\n\n и ожидает отправки ответов\n\n в **'{ACTIVATION_REQ_FOLDER}'**",
            value=counts["activation_req_count"],
            width="content"
        )
        other_topic_ph.metric(
            f"Писем на иные темы найдено,\n\n и перенесено в **'{OTHER_TOPIC_FOLDER}'**",
            counts["other_topic_count"],
            width="content"
        )
    except Exception as e:
        st.sidebar.error(f"Ошибка при получении доступа к почте: {e}")

hide_menu_style = """
        <style>
        .stAppDeployButton {display:none;}
        .stMainBlockContainer {padding-top:1rem;}
        </style>
        """
st.markdown(hide_menu_style, unsafe_allow_html=True)


st.set_page_config(page_title="Mail Automator", layout="wide")

st.title("📬 Mail Automator")
st.text("")
st.text("")
st.text("")

# SIDEBAR: статус папок и AI
st.sidebar.header("Текущее состояние:")
to_sort_ph       = st.sidebar.empty()
activation_ph    = st.sidebar.empty()
other_topic_ph   = st.sidebar.empty()

update_folder_metrics()
    
st.sidebar.markdown("---")

if "ai_status" not in st.session_state:
    ai_ok, ai_msg = ping_ai()
    st.session_state["ai_status"] = (ai_ok, ai_msg)
else:
    ai_ok, ai_msg = st.session_state["ai_status"]    

if ai_ok:
    st.sidebar.success(ai_msg)
else:
    st.sidebar.error(ai_msg)



# Основная панель: две кнопки и лог
# col1, col2 = st.columns(2)

with st.container():
    if st.button("🔀 Отсортировать почту"):
        progress = st.progress(0)
        log_box  = st.empty()
        try:
            # sort_emails принимает callback для логирования
            def logger(step: str, i: int=None, total: int=None):
                log_box.text(step)
                if i is not None and total:
                    progress.progress((i + 1) / total)
            sort_emails(logger=logger)
            update_folder_metrics()
            st.success("Сортировка завершена!")
        except Exception as e:
            st.error(f"Ошибка при сортировке: {e}")
            
st.divider()

with st.container():
    if st.button("✉️ Отправить ответы"):
        progress2 = st.progress(0)
        log_box2  = st.empty()
        try:
            def logger2(step: str, i: int=None, total: int=None):
                log_box2.text(step)
                if i is not None and total:
                    progress2.progress((i + 1) / total)
            send_replies(logger=logger2)
            update_folder_metrics()
            st.success("Отпрвка завершена!")
        except Exception as e:
            st.error(f"Ошибка при отправке: {e}")

st.divider()

st.markdown(f"""
### Текст класификационного запроса
{quote_text(CLASSIFICATION_PROMPT)}

 
### Текст шаблона ответа
{quote_text(TEMPLATE)}

""")
