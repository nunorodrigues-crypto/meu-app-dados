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
from streamlit_oauth import OAuth2Component

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Data AI Hub", page_icon="💎", layout="wide")

# --- GESTOR DE BASE DE DADOS ---
HISTORY_FILE = "chat_database.json"
class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()
    def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'w') as f: json.dump({"users": {}, "guest_tokens": {}}, f)
        with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        if "guest_tokens" not in self.full_db: self.full_db["guest_tokens"] = {}
        if "users" not in self.full_db: self.full_db["users"] = {}
        if self.username not in self.full_db["users"]: self.full_db["users"][self.username] = {}
        self.user_chats = self.full_db["users"][self.username]
    def save_db(self):
        self.full_db["users"][self.username] = self.user_chats
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
    def create_one_time_token(self):
        token = str(uuid.uuid4())[:6].upper()
        self.full_db["guest_tokens"][token] = {"created_at": datetime.now().isoformat(), "used": False, "created_by": self.username}
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
        return token
    def validate_and_consume_token(self, token):
        token = token.strip().upper()
        tokens = self.full_db.get("guest_tokens", {})
        if token in tokens and not tokens[token]["used"]:
            tokens[token]["used"] = True; tokens[token]["used_at"] = datetime.now().isoformat()
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
            return True
        return False
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
        if chat_id in self.user_chats: self.user_chats[chat_id]["pinned"] = not self.user_chats[chat_id].get("pinned", False); self.save_db(); st.rerun()
    def rename_chat(self, chat_id, new_name):
        self.user_chats[chat_id]["title"] = new_name; self.save_db(); st.rerun()
    def delete_chat(self, chat_id):
        if chat_id in self.user_chats: del self.user_chats[chat_id]; self.save_db(); return True
        return False

# --- FUNÇÕES DE DADOS ---
def clean_individual_df(df, filename):
    df.drop_duplicates(inplace=True); date_col = None
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]): date_col = col; break
    if not date_col:
        for col in df.columns:
            if df[col].dtype == 'object':
                try: df[col] = pd.to_datetime(df[col]); date_col = col; break
                except: pass
    if date_col: df.rename(columns={date_col: 'DATA_FUSAO'}, inplace=True); return df, True
    return df, False

def load_from_url(url):
    try:
        if "docs.google.com" in url: url = url.replace("/edit?usp=sharing", "/export?format=csv").replace("/edit", "/export?format=csv")
        r = requests.get(url); r.raise_for_status()
        try: return pd.read_csv(StringIO(r.text)), "Link_CSV"
        except: return pd.read_excel(BytesIO(r.content)), "Link_Excel"
    except: return None, "Erro Link"

def smart_merge(files=None, url_df=None, url_name=None):
    dataframes = []; file_names = []
    if files:
        for f in files:
            try:
                if f.name.endswith('.csv'): df = pd.read_csv(f)
                else: df = pd.read_excel(f)
                cdf, ok = clean_individual_df(df, f.name)
                if ok: 
                    cdf.columns = [f"{f.name.split('.')[0]}_{c}" if c!='DATA_FUSAO' else c for c in cdf.columns]
                    dataframes.append(cdf); file_names.append(f.name)
            except: pass
    if url_df is not None:
        cdf, ok = clean_individual_df(url_df, url_name)
        if ok:
            cdf.columns = [f"CLOUD_{c}" if c!='DATA_FUSAO' else c for c in cdf.columns]
            dataframes.append(cdf); file_names.append(url_name)
    if not dataframes: return None, "Sem dados."
    try:
        final = reduce(lambda l,r: pd.merge(l, r, on='DATA_FUSAO', how='outer'), dataframes)
        return final.sort_values('DATA_FUSAO').fillna(0), file_names
    except: return None, "Erro fusão."

def ask_gemini(df, query, api_key, context, file_list, persona):
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"Persona: {persona}. Contexto: {context}. Files: {file_list}. Dados: {df.dtypes}. Query: {query}. Responda SÓ código Python (```python)."
    res = model.generate_content(prompt)
    match = re.search(r"```python(.*?)```", res.text, re.DOTALL)
    return match.group(1).strip() if match else res.text.replace("```", "").strip()

def execute_code(code, df):
    try:
        import numpy as np; old = sys.stdout; redir = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd, 'np': np}
        exec(code, {}, local_vars); sys.stdout = old
        return redir.getvalue(), plt
    except Exception as e: return f"Erro: {e}", None

def create_pdf(chat_history):
    pdf = FPDF(); pdf.add_page(); pdf.set_font("Arial", size=12); pdf.cell(200, 10, txt="Relatorio AI", ln=1, align='C'); pdf.ln(10)
    for msg in chat_history:
        text = msg["content"].replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{msg['role']}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=text); pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- FUNÇÕES DE SUPORTE LOGIN ---
def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=4); qr.add_data(data); qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white'); buf = BytesIO(); img.save(buf); return buf.getvalue()

def generate_whatsapp_link(text):
    return f"https://wa.me/?text={urllib.parse.quote(text)}"

# --- PÁGINA DE LOGIN (DESIGN VERTICAL & CORRIGIDO) ---
def login_page():
    # Centralização
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("<h1 style='text-align: center;'>Data AI Hub</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; color: gray;'>Faça login para continuar</p>", unsafe_allow_html=True)
        st.write("") 

        # 1. USER / PASS (ADMIN)
        with st.form("login_form"):
            u = st.text_input("Utilizador")
            p = st.text_input("Password", type="password")
            # KEY ÚNICA PARA EVITAR ERRO
            submitted = st.form_submit_button("Entrar no Sistema", use_container_width=True)
            
            if submitted:
                ru = st.secrets.get("ADMIN_USER", "admin"); rp = st.secrets.get("ADMIN_PASSWORD", "123")
                if u == ru and p == rp:
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = u
                    st.session_state['is_guest'] = False
                    st.rerun()
                else: st.error("Dados incorretos.")

        st.markdown("<div style='text-align: center; margin: 15px; color: gray;'>ou</div>", unsafe_allow_html=True)

        # 2. GOOGLE OAUTH (CORRIGIDO COM KEY ÚNICA)
        if "GOOGLE_CLIENT_ID" in st.secrets:
            try:
                oauth2 = OAuth2Component(
                    st.secrets["GOOGLE_CLIENT_ID"], st.secrets["GOOGLE_CLIENT_SECRET"],
                    "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
                    "https://www.googleapis.com/oauth2/v1/tokeninfo", "https://www.googleapis.com/oauth2/v1/userinfo"
                )
                # CORREÇÃO DO LINK E KEY
                result = oauth2.authorize_button(
                    name="Entrar com Google",
                    icon="https://www.google.com.tw/favicon.ico",
                    redirect_uri=st.secrets["GOOGLE_REDIRECT_URI"],
                    scope="email profile",
                    key="google_oauth_btn_vertical" # <--- KEY ÚNICA CRUCIAL
                )
                
                if result and "token" in result:
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = "Google User"
                    st.session_state['is_guest'] = False
                    st.success("Sucesso!"); time.sleep(1); st.rerun()
            except Exception as e:
                st.warning(f"Configuração Google pendente: {e}")

        st.write("") 
        
        # 3. ACESSO CONVIDADO (EXPANDER DISCRETO)
        with st.expander("🎟️ Tenho um Código de Convite"):
            tk = st.text_input("Código de 6 dígitos")
            # KEY ÚNICA PARA EVITAR ERRO 'DuplicateElementId'
            if st.button("Validar Código", key="btn_validate_guest_code", use_container_width=True):
                db = HistoryManager()
                if db.validate_and_consume_token(tk):
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = "Convidado"
                    st.session_state['is_guest'] = True
                    st.success("Código Aceite!"); time.sleep(1); st.rerun()
                else: st.error("Inválido.")

    # Auto-Login por URL (Executa sempre)
    if "token" in st.query_params:
        tk_url = st.query_params["token"]
        db = HistoryManager()
        if db.validate_and_consume_token(tk_url):
            st.session_state['authenticated'] = True
            st.session_state['username'] = "Convidado"
            st.session_state['is_guest'] = True
            st.rerun()

# --- APP PRINCIPAL ---
def main_app():
    user = st.session_state.get('username', 'User')
    is_guest = st.session_state.get('is_guest', False)
    db = HistoryManager(user)

    with st.sidebar:
        st.title(f"👤 {user}")
        
        if not is_guest:
            with st.expander("🎟️ Gerar Convites"):
                if st.button("Criar Código Único", key="btn_create_invite"):
                    new_token = db.create_one_time_token()
                    base = st.secrets.get("APP_URL", "#")
                    link = f"{base}?token={new_token}"
                    st.success("CÓDIGO:"); st.title(new_token)
                    qr = generate_qr_code(link)
                    st.image(qr, width=150)
                    wa = generate_whatsapp_link(f"Acede aqui: {link} \nCódigo: *{new_token}*")
                    st.markdown(f"[![WhatsApp](https://img.shields.io/badge/WhatsApp-Share-green)]({wa})")
        
        st.markdown("---")
        if st.button("➕ Nova Análise", key="btn_new_chat", use_container_width=True): 
            st.session_state['current_chat_id'] = None; st.rerun()
        
        for cid, d in sorted(db.user_chats.items(), key=lambda x:x[1]['created_at'], reverse=True):
            c1, c2 = st.columns([1, 5])
            with c1: 
                 if st.button("🗑️", key=f"del_{cid}"): db.delete_chat(cid); st.rerun()
            with c2:
                 if st.button(f"💬 {d['title']}", key=f"chat_{cid}"):
                    st.session_state['current_chat_id']=cid; st.rerun()
        
        st.markdown("---")
        if st.button("🚪 Sair", key="btn_logout"): 
            st.session_state['authenticated']=False; st.query_params.clear(); st.rerun()

    current_id = st.session_state.get('current_chat_id')
    messages = db.get_messages(current_id) if current_id else []

    with st.expander("⚙️ Dados", expanded=not messages):
        if "GEMINI_API_KEY" in st.secrets: api_key = st.secrets["GEMINI_API_KEY"]
        else: api_key = st.text_input("API Key", type="password")
        c1, c2 = st.columns(2); persona = c1.selectbox("Persona", ["Data Scientist", "CFO"]); context = c2.text_area("Contexto", height=40)
        t1, t2 = st.tabs(["Upload", "Link"]); up_files = t1.file_uploader("Ficheiros", accept_multiple_files=True)
        url_df = None; url_name = None
        if u := t2.text_input("URL"): url_df, url_name = load_from_url(u)

    df_final = None
    if up_files or url_df is not None:
        df_final, f_names = smart_merge(up_files, url_df, url_name)
        if df_final is not None: st.