Tem toda a razão! Ao focar tanto nas novas funcionalidades empresariais (Workspaces, Notas, Partilha), acabei por "limpar" demais a interface de login e o gerador de convites da barra lateral. Peço desculpa por isso.

Vamos corrigir já. Esta é a Versão 18.0 (A Versão Definitiva).

Esta versão une tudo:

Funcionalidades Enterprise: (Notas, Partilha, Workspaces, PDF).

Acesso Universal: (Google, Admin Password E o regresso do QR Code/Token).

Substitua TODO o app.py por este código:
Python

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
st.set_page_config(page_title="Data AI Hub", page_icon="🏢", layout="wide")

# --- GESTOR DE DADOS (DATABASE) ---
HISTORY_FILE = "chat_database.json"

class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            init_db = {"users": {}, "guest_tokens": {}, "workspaces": {}}
            with open(HISTORY_FILE, 'w') as f: json.dump(init_db, f)
        
        with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        
        # Garantir estrutura completa
        if "workspaces" not in self.full_db: self.full_db["workspaces"] = {}
        if "guest_tokens" not in self.full_db: self.full_db["guest_tokens"] = {}
        if self.username not in self.full_db["users"]:
            self.full_db["users"][self.username] = {"chats": {}, "plan": "free", "workspaces": []}
        
        self.user_data = self.full_db["users"][self.username]
        self.user_chats = self.user_data["chats"]

    def save_db(self):
        self.full_db["users"][self.username] = self.user_data
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

    # --- GESTÃO DE TOKENS / CONVITES (O QUE FALTOU) ---
    def create_one_time_token(self):
        token = str(uuid.uuid4())[:6].upper()
        self.full_db["guest_tokens"][token] = {
            "created_at": datetime.now().isoformat(),
            "used": False,
            "created_by": self.username
        }
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
        return token
    
    def validate_and_consume_token(self, token):
        token = token.strip().upper()
        tokens = self.full_db.get("guest_tokens", {})
        if token in tokens and not tokens[token]["used"]:
            tokens[token]["used"] = True
            tokens[token]["used_at"] = datetime.now().isoformat()
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
            return True
        return False

    # --- GESTÃO DE CHATS & NOTAS ---
    def create_chat(self, first_message, workspace_id=None):
        chat_id = str(uuid.uuid4())
        title = first_message[:30] + "..." if len(first_message) > 30 else first_message
        chat_obj = {
            "title": title, "created_at": datetime.now().isoformat(), "pinned": False, 
            "messages": [], "notes": "", "owner": self.username, "shared_with": [], 
            "workspace_id": workspace_id
        }
        if workspace_id and workspace_id in self.full_db["workspaces"]:
            self.full_db["workspaces"][workspace_id]["chats"][chat_id] = chat_obj
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
        else:
            self.user_chats[chat_id] = chat_obj
            self.save_db()
        return chat_id

    def get_chat(self, chat_id):
        if chat_id in self.user_chats: return self.user_chats[chat_id]
        for u_email, u_data in self.full_db["users"].items():
            if chat_id in u_data["chats"]:
                chat = u_data["chats"][chat_id]
                if self.username in chat.get("shared_with", []): return chat
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"]:
                if self.username in wdata["members"] or self.username == wdata["owner"]: return wdata["chats"][chat_id]
        return None

    def update_chat(self, chat_id, chat_data):
        if chat_id in self.user_chats:
            self.user_chats[chat_id] = chat_data; self.save_db(); return
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"]:
                self.full_db["workspaces"][wid]["chats"][chat_id] = chat_data
                with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str); return
        for u_email, u_data in self.full_db["users"].items():
             if chat_id in u_data["chats"]:
                 self.full_db["users"][u_email]["chats"][chat_id] = chat_data
                 with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str); return

    def share_chat(self, chat_id, target_email):
        chat = self.get_chat(chat_id)
        if chat and target_email not in chat["shared_with"]:
            chat["shared_with"].append(target_email)
            self.update_chat(chat_id, chat)
            return True
        return False
    
    def delete_chat(self, chat_id):
        if chat_id in self.user_chats: del self.user_chats[chat_id]; self.save_db(); return True
        return False

    # --- GESTÃO DE WORKSPACES ---
    def upgrade_plan(self): self.user_data["plan"] = "pro"; self.save_db()

    def create_workspace(self, name):
        if self.user_data["plan"] != "pro": return False, "Requer Plano PRO"
        ws_id = str(uuid.uuid4())
        self.full_db["workspaces"][ws_id] = {"name": name, "owner": self.username, "members": [self.username], "chats": {}}
        self.user_data["workspaces"].append(ws_id)
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
        return True, "Criado"

    def add_member_to_workspace(self, ws_id, email):
        if ws_id in self.full_db["workspaces"]:
            ws = self.full_db["workspaces"][ws_id]
            if email not in ws["members"]:
                ws["members"].append(email)
                if email in self.full_db["users"]:
                    if ws_id not in self.full_db["users"][email].get("workspaces", []):
                         self.full_db["users"][email].setdefault("workspaces", []).append(ws_id)
                with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
                return True
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
    persona_prompt = "Atue como Data Scientist."
    if persona == "CFO": persona_prompt = "Atue como Diretor Financeiro Rígido."
    elif persona == "CMO": persona_prompt = "Atue como Diretor de Marketing Criativo."

    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""
    {persona_prompt}
    CONTEXTO: {context}
    DADOS: {", ".join(file_list)}
    ESTRUTURA: {df.dtypes.to_string()}
    PERGUNTA: "{query}"
    REGRAS: Responda APENAS com código Python (```python). Use 'df', print(), plt.figure().
    """
    response = model.generate_content(prompt)
    match = re.search(r"```python(.*?)```", response.text, re.DOTALL)
    return match.group(1).strip() if match else response.text.replace("```", "").strip()

def execute_code(code, df):
    try:
        import numpy as np; old = sys.stdout; redir = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd, 'np': np}
        exec(code, {}, local_vars); sys.stdout = old
        return redir.getvalue(), plt
    except Exception as e: return f"Erro: {e}", None

def create_pdf(chat_data):
    pdf = FPDF(); pdf.add_page(); pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Relatorio: {chat_data['title']}", ln=1, align='C'); pdf.ln(10)
    pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, txt="NOTAS", ln=1)
    pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=chat_data.get("notes", "").encode('latin-1', 'replace').decode('latin-1')); pdf.ln(10)
    pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, txt="CHAT", ln=1)
    pdf.set_font("Arial", size=10)
    for msg in chat_data["messages"]:
        text = msg["content"].replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{msg['role']}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=text); pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- SUPORTE LOGIN (QR CODE) ---
def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=4); qr.add_data(data); qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white'); buf = BytesIO(); img.save(buf); return buf.getvalue()

def generate_whatsapp_link(text):
    return f"https://wa.me/?text={urllib.parse.quote(text)}"

# --- PÁGINA DE LOGIN (RESTITUIÇÃO DAS FUNCIONALIDADES) ---
def login_page():
    # 1. Auto-Login via URL (O Código QR aponta para aqui)
    if "token" in st.query_params:
        tk_url = st.query_params["token"]
        db = HistoryManager()
        if db.validate_and_consume_token(tk_url):
            st.session_state['authenticated'] = True
            st.session_state['username'] = "Convidado"
            st.session_state['is_guest'] = True
            st.success("🎟️ Convite Validado! A entrar..."); time.sleep(1); st.rerun()

    # 2. Interface Visual
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<h1 style='text-align: center;'>Data AI Enterprise</h1>", unsafe_allow_html=True)
        st.write("") 

        # A. Login Normal (Admin/User)
        with st.form("login_form"):
            u = st.text_input("Utilizador")
            p = st.text_input("Password", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                ru = st.secrets.get("ADMIN_USER", "admin"); rp = st.secrets.get("ADMIN_PASSWORD", "123")
                if u == ru and p == rp:
                    st.session_state['authenticated'] = True; st.session_state['username'] = u; st.session_state['is_guest'] = False; st.rerun()
                else: st.error("Dados incorretos.")

        st.markdown("<div style='text-align: center; margin: 10px; color: gray;'>ou</div>", unsafe_allow_html=True)

        # B. Login Google
        if "GOOGLE_CLIENT_ID" in st.secrets:
            try:
                oauth2 = OAuth2Component(
                    st.secrets["GOOGLE_CLIENT_ID"], st.secrets["GOOGLE_CLIENT_SECRET"],
                    "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
                    "https://www.googleapis.com/oauth2/v1/tokeninfo", "https://www.googleapis.com/oauth2/v1/userinfo"
                )
                result = oauth2.authorize_button("Entrar com Google", "https://www.google.com.tw/favicon.ico", st.secrets["GOOGLE_REDIRECT_URI"], "email profile", key="g_btn_login")
                if result and "token" in result:
                    st.session_state['authenticated'] = True; st.session_state['username'] = "Google User"; st.session_state['is_guest'] = False; st.rerun()
            except: pass

        st.write("")
        
        # C. Login Convidado (Código Manual) - DE VOLTA!
        with st.expander("🎟️ Tenho um Código de Convite"):
            tk = st.text_input("Insira o código de 6 dígitos")
            if st.button("Validar Código", key="btn_guest_manual", use_container_width=True):
                db = HistoryManager()
                if db.validate_and_consume_token(tk):
                    st.session_state['authenticated'] = True; st.session_state['username'] = "Convidado"; st.session_state['is_guest'] = True
                    st.success("Código Aceite!"); time.sleep(1); st.rerun()
                else: st.error("Código inválido ou expirado.")

# --- APP PRINCIPAL ---
def main_app():
    user = st.session_state.get('username', 'User')
    is_guest = st.session_state.get('is_guest', False)
    db = HistoryManager(user)

    with st.sidebar:
        st.title(f"👤 {user}")
        
        # --- GERADOR DE CONVITES (DE VOLTA NA SIDEBAR) ---
        if not is_guest:
            with st.expander("🎟️ Gerar Convite", expanded=False):
                if st.button("Criar Código Único", key="btn_gen_invite"):
                    new_token = db.create_one_time_token()
                    base = st.secrets.get("APP_URL", "#")
                    link = f"{base}?token={new_token}"
                    st.success(f"CÓDIGO: {new_token}")
                    qr = generate_qr_code(link)
                    st.image(qr, width=150, caption="QR Acesso Direto")
                    wa = generate_whatsapp_link(f"Link: {link} \nCódigo: *{new_token}*")
                    st.markdown(f"[![WhatsApp](https://img.shields.io/badge/WhatsApp-Share-green)]({wa})")

        # WORKSPACES (ENTERPRISE)
        st.markdown("---")
        context_mode = st.radio("Modo:", ["Pessoal", "Workspaces"], horizontal=True)
        if context_mode == "Workspaces":
            if db.user_data["plan"] == "free":
                if st.button("💎 Upgrade PRO"): db.upgrade_plan(); st.rerun()
            else:
                my_ws = db.full_db["workspaces"]
                user_ws = {k:v for k,v in my_ws.items() if user in v["members"]}
                sel_ws = st.selectbox("Workspace", list(user_ws.keys()), format_func=lambda x: user_ws[x]["name"])
                if sel_ws:
                    st.caption(f"Membros: {len(user_ws[sel_ws]['members'])}")
                    if st.button("Convidar Colega"): db.add_member_to_workspace(sel_ws, "colega@email.com"); st.toast("Simulação: Convite enviado")
                
                with st.expander("Novo Workspace"):
                    n = st.text_input("Nome"); 
                    if st.button("Criar"): db.create_workspace(n); st.rerun()

        # LISTA DE CHATS
        st.markdown("---")
        if st.button("➕ Nova Análise", use_container_width=True): st.session_state['current_chat_id'] = None; st.rerun()
        
        chats_show = db.user_chats
        if context_mode == "Workspaces" and db.user_data["plan"]=="pro" and 'sel_ws' in locals() and sel_ws:
             chats_show = db.full_db["workspaces"][sel_ws]["chats"]

        st.caption("MEUS CHATS")
        for cid, d in sorted(chats_show.items(), key=lambda x:x[1]['created_at'], reverse=True):
            c1, c2 = st.columns([1,5])
            with c1: 
                if st.button("🗑️", key=f"d{cid}"): db.delete_chat(cid); st.rerun()
            with c2:
                if st.button(f"💬 {d['title']}", key=cid): st.session_state['current_chat_id']=cid; st.rerun()
        
        # PARTILHADOS COMIGO
        st.caption("PARTILHADOS COMIGO")
        for uid, udata in db.full_db["users"].items():
            if uid == user: continue
            for cid, cdata in udata["chats"].items():
                if user in cdata.get("shared_with", []):
                    if st.button(f"🔗 {cdata['title']} ({uid})", key=f"s{cid}"): st.session_state['current_chat_id']=cid; st.rerun()

        st.markdown("---")
        if st.button("🚪 Logout"): st.session_state['authenticated']=False; st.query_params.clear(); st.rerun()

    # ÁREA CENTRAL
    current_id = st.session_state.get('current_chat_id')
    if current_id:
        chat_data = db.get_chat(current_id)
        if not chat_data: st.error("Erro chat"); return

        # CABEÇALHO E PARTILHA
        c1, c2 = st.columns([3, 1])
        with c1: st.subheader(f"📂 {chat_data['title']}")
        with c2:
            with st.popover("📤 Partilhar"):
                em = st.text_input("Email"); 
                if st.button("Enviar"): 
                    if db.share_chat(current_id, em): st.success("OK")
                    else: st.error("Erro")

        # DIVISÃO: CHAT (Esq) vs NOTAS (Dir)
        col_chat, col_notes = st.columns([2, 1])
        
        with col_notes:
            st.markdown("### 📝 Notas")
            notes = st.text_area("Anotações", value=chat_data.get("notes", ""), height=400, key="n_area")
            if notes != chat_data.get("notes", ""):
                chat_data["notes"] = notes; db.update_chat(current_id, chat_data); st.toast("Salvo")

        with col_chat:
            if not chat_data["messages"]:
                with st.expander("⚙️ Configuração", expanded=True):
                    if "GEMINI_API_KEY" in st.secrets: api_key = st.secrets["GEMINI_API_KEY"]
                    else: api_key = st.text_input("API Key", type="password")
                    c_a, c_b = st.columns(2); persona = c_a.selectbox("Persona", ["Data Scientist", "CFO", "CMO"]); context = c_b.text_area("Contexto")
                    t1, t2 = st.tabs(["Upload", "Link"]); up = t1.file_uploader("Files", accept_multiple_files=True)
                    u_df = None; u_n = None; 
                    if u := t2.text_input("URL"): u_df, u_n = load_from_url(u)
                    
                    if up or u_df is not None:
                        df, fn = smart_merge(up, u_df, u_n)
                        if df is not None: 
                            st.success("Dados OK"); st.session_state['temp_df'] = df; st.session_state['temp_names'] = fn

            for msg in chat_data["messages"]: st.chat_message(msg["role"]).write(msg["content"])

            if query := st.chat_input("Pergunta..."):
                df = st.session_state.get('temp_df'); fn = st.session_state.get('temp_names', [])
                if df is None and not chat_data["messages"]: st.error("Carregue dados.")
                else:
                    if df is None: st.warning("Recarregue dados.")
                    else:
                        chat_data["messages"].append({"role": "user", "content": query})
                        db.update_chat(current_id, chat_data)
                        st.chat_message("user").write(query)
                        with st.spinner("..."):
                            code = ask_gemini(df, query, api_key, context if 'context' in locals() else "", fn, persona if 'persona' in locals() else "DS")
                            text, fig = execute_code(code, df)
                            chat_data["messages"].append({"role": "assistant", "content": text})
                            db.update_chat(current_id, chat_data)
                            st.chat_message("assistant").write(text)
                            if fig: st.chat_message("assistant").pyplot(fig)
    
    else: st.info("Selecione uma análise.")

if __name__ == "__main__":
    if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
    if st.session_state["authenticated"]: main_app()
    else: login_page()