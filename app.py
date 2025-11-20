import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO, BytesIO
import re
from functools import reduce
from fpdf import FPDF
import time
import json
import os
import uuid
from datetime import datetime
import requests
import qrcode
import urllib.parse

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Data AI Hub", page_icon="🎫", layout="wide")

# --- GESTOR DE HISTÓRICO E TOKENS (DB) ---
HISTORY_FILE = "chat_database.json"

class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            init_db = {"users": {}, "guest_tokens": {}}
            with open(HISTORY_FILE, 'w') as f: json.dump(init_db, f)
        with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        if "guest_tokens" not in self.full_db: self.full_db["guest_tokens"] = {}
        if "users" not in self.full_db: self.full_db["users"] = {}
        if self.username not in self.full_db["users"]: self.full_db["users"][self.username] = {}
        self.user_chats = self.full_db["users"][self.username]

    def save_db(self):
        self.full_db["users"][self.username] = self.user_chats
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

    # GESTÃO DE TOKEN ÚNICO
    def create_one_time_token(self):
        # Gera um código curto de 6 letras/números (mais fácil de digitar)
        token = str(uuid.uuid4())[:6].upper()
        self.full_db["guest_tokens"][token] = {
            "created_at": datetime.now().isoformat(),
            "used": False,
            "created_by": self.username
        }
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
        return token

    def validate_and_consume_token(self, token):
        token = token.strip().upper() # Garante que letras minúsculas funcionam
        tokens = self.full_db.get("guest_tokens", {})
        if token in tokens:
            if tokens[token]["used"] == False:
                tokens[token]["used"] = True # QUEIMA O TOKEN
                tokens[token]["used_at"] = datetime.now().isoformat()
                with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
                return True
        return False

    # CHATS
    def create_chat(self, first_message):
        chat_id = str(uuid.uuid4())
        title = first_message[:30] + "..." if len(first_message) > 30 else first_message
        self.user_chats[chat_id] = {"title": title, "created_at": datetime.now().isoformat(), "pinned": False, "messages": []}
        self.save_db(); return chat_id
    def add_message(self, chat_id, role, content):
        if chat_id not in self.user_chats: return
        self.user_chats[chat_id]["messages"].append({"role": role, "content": content})
        self.save_db()
    def get_messages(self, chat_id): return self.user_chats.get(chat_id, {}).get("messages", [])
    def toggle_pin(self, chat_id):
        if chat_id in self.user_chats:
            self.user_chats[chat_id]["pinned"] = not self.user_chats[chat_id].get("pinned", False)
            self.save_db(); st.rerun()
    def delete_chat(self, chat_id):
        if chat_id in self.user_chats: del self.user_chats[chat_id]; self.save_db(); return True
        return False

# --- FUNÇÕES DE DADOS ---
def clean_individual_df(df, filename):
    df.drop_duplicates(inplace=True)
    date_col = None
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]): date_col = col; break
    if not date_col:
        for col in df.columns:
            if df[col].dtype == 'object':
                try: df[col] = pd.to_datetime(df[col]); date_col = col; break
                except: pass
    if date_col:
        df.rename(columns={date_col: 'DATA_FUSAO'}, inplace=True)
        return df, True
    return df, False

def load_from_url(url):
    try:
        if "docs.google.com/spreadsheets" in url:
            url = url.replace("/edit?usp=sharing", "/export?format=csv").replace("/edit", "/export?format=csv")
        response = requests.get(url)
        try: return pd.read_csv(StringIO(response.text)), "Link_CSV"
        except: return pd.read_excel(BytesIO(response.content)), "Link_Excel"
    except: return None, "Erro Link"

def smart_merge(files=None, url_df=None, url_name=None):
    dataframes = []
    file_names = []
    if files:
        for file in files:
            try:
                if file.name.endswith('.csv'): df = pd.read_csv(file)
                else: df = pd.read_excel(file)
                clean_df, tem_data = clean_individual_df(df, file.name)
                if tem_data:
                    prefix = file.name.split('.')[0]
                    clean_df.columns = [f"{prefix}_{c}" if c != 'DATA_FUSAO' else 'DATA_FUSAO' for c in clean_df.columns]
                    dataframes.append(clean_df); file_names.append(file.name)
            except: pass
    if url_df is not None:
        clean_df, tem_data = clean_individual_df(url_df, url_name)
        if tem_data:
            clean_df.columns = [f"CLOUD_{c}" if c != 'DATA_FUSAO' else 'DATA_FUSAO' for c in clean_df.columns]
            dataframes.append(clean_df); file_names.append(url_name)
    if not dataframes: return None, "Sem dados."
    if len(dataframes) == 1: return dataframes[0], file_names
    try:
        df_final = reduce(lambda left, right: pd.merge(left, right, on='DATA_FUSAO', how='outer'), dataframes)
        return df_final.sort_values('DATA_FUSAO').fillna(0), file_names
    except: return None, "Erro fusão."

def ask_gemini(df, query, api_key, context, file_list, persona):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"Persona: {persona}. Contexto: {context}. Files: {file_list}. Dados: {df.dtypes}. Query: {query}. Responda SÓ código Python (```python)."
    response = model.generate_content(prompt)
    match = re.search(r"```python(.*?)```", response.text, re.DOTALL)
    return match.group(1).strip() if match else response.text.replace("```", "").strip()

def execute_code(code, df):
    try:
        import numpy as np
        old_stdout = sys.stdout; redirected_output = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd, 'np': np}
        exec(code, {}, local_vars)
        sys.stdout = old_stdout
        return redirected_output.getvalue(), plt
    except Exception as e: return f"Erro: {e}", None

def create_pdf(chat_history):
    pdf = FPDF(); pdf.add_page(); pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Relatorio AI", ln=1, align='C'); pdf.ln(10)
    for msg in chat_history:
        text = msg["content"].encode('latin-1', 'replace').decode('latin-1')
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{msg['role']}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=text); pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- SISTEMA DE LOGIN ---

def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    buf = BytesIO(); img.save(buf)
    return buf.getvalue()

def login_page():
    st.markdown("<h2 style='text-align: center;'>🔐 Acesso Seguro</h2>", unsafe_allow_html=True)
    
    # Verifica se há token na URL para auto-login (Opcional)
    if "token" in st.query_params:
        token_url = st.query_params["token"]
        db = HistoryManager()
        if db.validate_and_consume_token(token_url):
            st.session_state['authenticated'] = True
            st.session_state['username'] = "Convidado"
            st.session_state['is_guest'] = True
            st.success("🚀 Token Validado! A entrar..."); time.sleep(1); st.rerun()

    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        tab_admin, tab_guest = st.tabs(["🔑 Admin", "🎫 Tenho um Código"])
        
        with tab_admin:
            u = st.text_input("User Admin"); p = st.text_input("Password", type="password")
            if st.button("Entrar como Admin", use_container_width=True):
                ru = st.secrets.get("ADMIN_USER", "admin"); rp = st.secrets.get("ADMIN_PASSWORD", "123")
                if u == ru and p == rp:
                    st.session_state['authenticated'] = True; st.session_state['username'] = u; st.session_state['is_guest'] = False; st.rerun()
                else: st.error("Dados errados.")

        with tab_guest:
            st.write("Insira o código de 6 dígitos que recebeu.")
            token_input = st.text_input("Código do Convite", placeholder="EX: A1B2C3")
            if st.button("Validar Código", use_container_width=True):
                db = HistoryManager()
                if db.validate_and_consume_token(token_input):
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = "Convidado"
                    st.session_state['is_guest'] = True
                    st.success("✅ Código Válido!"); time.sleep(1); st.rerun()
                else:
                    st.error("❌ Código inválido ou já utilizado.")

def generate_whatsapp_link(text):
    encoded = urllib.parse.quote(text)
    return f"https://wa.me/?text={encoded}"

def main_app():
    user = st.session_state.get('username', 'User')
    is_guest = st.session_state.get('is_guest', False)
    db = HistoryManager(user)

    with st.sidebar:
        st.title(f"👤 {user}")
        
        # --- ÁREA ADMIN: GERADOR DE CONVITES ---
        if not is_guest:
            with st.expander("🎫 Criar Convite Novo", expanded=False):
                if st.button("Gerar Código Único", use_container_width=True):
                    new_token = db.create_one_time_token()
                    app_url = st.secrets.get("APP_URL", "https://tua-app.streamlit.app")
                    
                    # 1. Mostrar o Código para Copiar
                    st.success("Código Gerado:")
                    st.code(new_token, language="text")
                    
                    # 2. Link Mágico
                    magic_link = f"{app_url}?token={new_token}"
                    
                    # 3. QR Code (Aponta para o site)
                    # O QR leva ao site. A pessoa depois mete o código manual.
                    qr_bytes = generate_qr_code(app_url) 
                    
                    st.image(qr_bytes, caption="Scan para abrir o site")
                    st.write("Envie o código **" + new_token + "** ao cliente.")
                    
                    # Botão WhatsApp
                    msg_wa = f"Olá! Acede à plataforma aqui: {app_url} \nO teu código de acesso único é: *{new_token}*"
                    wa_url = generate_whatsapp_link(msg_wa)
                    st.link_button("📲 Enviar por WhatsApp", wa_url)

        st.markdown("---")
        if st.button("➕ Nova Análise", use_container_width=True): st.session_state['current_chat_id'] = None; st.rerun()
        
        # Histórico
        chats = db.user_chats
        for cid, d in sorted(chats.items(), key=lambda x:x[1]['created_at'], reverse=True):
            if st.button(f"💬 {d['title']}", key=cid, use_container_width=True):
                st.session_state['current_chat_id']=cid; st.rerun()
        
        st.markdown("---")
        if st.button("🚪 Sair"): st.session_state['authenticated']=False; st.query_params.clear(); st.rerun()

    # APP
    current_id = st.session_state.get('current_chat_id')
    messages = db.get_messages(current_id) if current_id else []

    with st.expander("⚙️ Dados", expanded=not messages):
        if "GEMINI_API_KEY" in st.secrets: api_key = st.secrets["GEMINI_API_KEY"]
        else: api_key = st.text_input("API Key", type="password")
        c1, c2 = st.columns(2)
        persona = c1.selectbox("Persona", ["Data Scientist", "CFO"])
        context = c2.text_area("Contexto", height=40)
        t1, t2 = st.tabs(["Upload", "Link"])
        up_files = t1.file_uploader("Ficheiros", accept_multiple_files=True)
        url_df = None; url_name = None
        if u := t2.text_input("URL"): url_df, url_name = load_from_url(u)

    df_final = None
    if up_files or url_df is not None:
        df_final, f_names = smart_merge(up_files, url_df, url_name)
        if df_final is not None: st.success("Dados OK")

    for msg in messages: st.chat_message(msg["role"]).write(msg["content"])

    if query := st.chat_input("Pergunta..."):
        if not api_key or df_final is None: st.error("Falta dados.")
        else:
            if not current_id: current_id = db.create_chat(query); st.session_state['current_chat_id']=current_id
            st.chat_message("user").write(query); db.add_message(current_id, "user", query)
            with st.spinner("..."):
                code = ask_gemini(df_final, query, api_key, context, f_names, persona)
                text, fig = execute_code(code, df_final)
                st.chat_message("assistant").write(text); db.add_message(current_id, "assistant", text)
                if fig: st.chat_message("assistant").pyplot(fig)

    if messages:
        pdf = create_pdf(messages)
        st.download_button("📄 PDF", pdf, "relatorio.pdf")

if __name__ == "__main__":
    if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
    if st.session_state["authenticated"]: main_app()
    else: login_page()