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

# --- GESTOR DE DADOS ---
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
        
        # Garantir estrutura
        if "workspaces" not in self.full_db: self.full_db["workspaces"] = {}
        if "guest_tokens" not in self.full_db: self.full_db["guest_tokens"] = {}
        if self.username not in self.full_db["users"]:
            self.full_db["users"][self.username] = {"chats": {}, "plan": "free", "workspaces": []}
        
        self.user_data = self.full_db["users"][self.username]
        self.user_chats = self.user_data["chats"]

    def save_db(self):
        self.full_db["users"][self.username] = self.user_data
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

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
        # Simplificação para demo: tenta atualizar no user atual, se falhar tenta workspaces
        if chat_id in self.user_chats:
            self.user_chats[chat_id] = chat_data; self.save_db(); return
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"]:
                self.full_db["workspaces"][wid]["chats"][chat_id] = chat_data
                with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str); return
        # Partilhados (apenas owner pode editar mensagens, mas aqui permitimos notas)
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

# --- FUNÇÕES DADOS ---
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

def create_pdf(chat_data):
    pdf = FPDF(); pdf.add_page(); pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt=f"Relatorio: {chat_data.get('title', 'Sem Titulo')}", ln=1, align='C'); pdf.ln(10)
    pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, txt="NOTAS", ln=1)
    pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=chat_data.get("notes", "").encode('latin-1', 'replace').decode('latin-1')); pdf.ln(10)
    pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, txt="CHAT", ln=1)
    pdf.set_font("Arial", size=10)
    for msg in chat_data.get("messages", []):
        text = msg["content"].replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{msg['role']}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=text); pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- SUPORTE LOGIN ---
def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=4); qr.add_data(data); qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white'); buf = BytesIO(); img.save(buf); return buf.getvalue()

def generate_whatsapp_link(text):
    return f"https://wa.me/?text={urllib.parse.quote(text)}"

# --- LOGIN PAGE ---
def login_page():
    if "token" in st.query_params:
        tk_url = st.query_params["token"]
        db = HistoryManager()
        if db.validate_and_consume_token(tk_url):
            st.session_state['authenticated'] = True; st.session_state['username'] = "Convidado"; st.session_state['is_guest'] = True
            st.success("A entrar..."); time.sleep(1); st.rerun()

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<h1 style='text-align: center;'>Data AI Hub</h1>", unsafe_allow_html=True)
        st.write("") 

        with st.form("login_form"):
            u = st.text_input("Utilizador")
            p = st.text_input("Password", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                ru = st.secrets.get("ADMIN_USER", "admin"); rp = st.secrets.get("ADMIN_PASSWORD", "123")
                if u == ru and p == rp:
                    st.session_state['authenticated'] = True; st.session_state['username'] = u; st.session_state['is_guest'] = False; st.rerun()
                else: st.error("Erro.")

        st.markdown("<div style='text-align: center; margin: 10px;'>ou</div>", unsafe_allow_html=True)

        if "GOOGLE_CLIENT_ID" in st.secrets:
            try:
                oauth2 = OAuth2Component(st.secrets["GOOGLE_CLIENT_ID"], st.secrets["GOOGLE_CLIENT_SECRET"], "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token", "https://www.googleapis.com/oauth2/v1/tokeninfo", "https://www.googleapis.com/oauth2/v1/userinfo")
                res = oauth2.authorize_button("Entrar com Google", "https://www.google.com.tw/favicon.ico", st.secrets["GOOGLE_REDIRECT_URI"], "email", key="g_btn")
                if res and "token" in res:
                    st.session_state['authenticated'] = True; st.session_state['username'] = "Google User"; st.session_state['is_guest'] = False; st.rerun()
            except: pass
            
        st.write("")
        with st.expander("🎟️ Tenho um Código"):
            tk = st.text_input("Código")
            if st.button("Validar", key="val_btn", use_container_width=True):
                db = HistoryManager()
                if db.validate_and_consume_token(tk):
                    st.session_state['authenticated'] = True; st.session_state['username'] = "Convidado"; st.session_state['is_guest'] = True
                    st.rerun()
                else: st.error("Inválido.")

# --- APP PRINCIPAL (CORRIGIDA) ---
def main_app():
    user = st.session_state.get('username', 'User')
    is_guest = st.session_state.get('is_guest', False)
    db = HistoryManager(user)

    with st.sidebar:
        st.title(f"👤 {user}")
        
        if not is_guest:
            with st.expander("🎟️ Gerar Convite", expanded=False):
                if st.button("Criar Código", key="gen_btn"):
                    tk = db.create_one_time_token()
                    url = st.secrets.get("APP_URL", "#"); lnk = f"{url}?token={tk}"
                    st.success(f"Código: {tk}")
                    st.image(generate_qr_code(lnk), width=150)
                    st.markdown(f"[WhatsApp]({generate_whatsapp_link(f'Acede: {lnk}')})")

        st.markdown("---")
        # Seletor de Workspace
        context_mode = st.radio("Modo:", ["Pessoal", "Workspaces"], horizontal=True)
        selected_ws_id = None
        if context_mode == "Workspaces":
            if db.user_data["plan"] != "pro":
                if st.button("Upgrade PRO"): db.upgrade_plan(); st.rerun()
            else:
                my_ws = {k:v for k,v in db.full_db["workspaces"].items() if user in v["members"]}
                selected_ws_id = st.selectbox("Workspace", list(my_ws.keys()), format_func=lambda x: my_ws[x]["name"])
                if st.button("Criar Workspace"): db.create_workspace(f"WS de {user}"); st.rerun()

        st.markdown("---")
        if st.button("➕ Nova Análise", use_container_width=True): 
            st.session_state['current_chat_id'] = None; st.rerun()
        
        # Listagem de Chats
        chats_source = db.user_chats
        if context_mode == "Workspaces" and selected_ws_id:
             chats_source = db.full_db["workspaces"][selected_ws_id]["chats"]

        for cid, d in sorted(chats_source.items(), key=lambda x:x[1]['created_at'], reverse=True):
            if st.button(f"💬 {d['title']}", key=cid): st.session_state['current_chat_id'] = cid; st.rerun()

        st.markdown("---")
        if st.button("🚪 Sair"): st.session_state['authenticated'] = False; st.query_params.clear(); st.rerun()

    # --- LÓGICA CENTRAL (CORRIGIDA AQUI) ---
    current_id = st.session_state.get('current_chat_id')
    
    # Variáveis de sessão para o formulário novo
    if 'temp_df' not in st.session_state: st.session_state['temp_df'] = None
    if 'temp_files' not in st.session_state: st.session_state['temp_files'] = []
    
    # CENÁRIO 1: NOVA ANÁLISE (ID é None) - MOSTRAR CONFIGURAÇÃO
    if current_id is None:
        st.title("✨ Nova Análise")
        st.info("Carregue os dados para começar.")
        
        with st.container():
            if "GEMINI_API_KEY" in st.secrets: api_key = st.secrets["GEMINI_API_KEY"]
            else: api_key = st.text_input("API Key", type="password")
            
            c1, c2 = st.columns(2)
            persona = c1.selectbox("Persona", ["Data Scientist", "CFO", "CMO"])
            context = c2.text_area("Contexto", height=40)
            
            t1, t2 = st.tabs(["Upload", "Link"])
            up_files = t1.file_uploader("Ficheiros", accept_multiple_files=True)
            url_df = None; url_name = None
            if u := t2.text_input("URL"): url_df, url_name = load_from_url(u)

            # Processar Upload
            if up_files or url_df is not None:
                df, fn = smart_merge(up_files, url_df, url_name)
                if df is not None:
                    st.success("✅ Dados Carregados!")
                    st.session_state['temp_df'] = df
                    st.session_state['temp_files'] = fn
                    with st.expander("Ver Tabela"): st.dataframe(df.head())
            
            # Chat Input Inicial
            if query := st.chat_input("O que quer analisar?"):
                if not api_key or st.session_state['temp_df'] is None:
                    st.error("Precisa de Chave e Dados.")
                else:
                    # CRIAR CHAT E REDIRECIONAR
                    new_id = db.create_chat(query, workspace_id=selected_ws_id)
                    
                    # Processar resposta inicial
                    with st.spinner("A criar análise..."):
                        code = ask_gemini(st.session_state['temp_df'], query, api_key, context, st.session_state['temp_files'], persona)
                        text, fig = execute_code(code, st.session_state['temp_df'])
                        
                        # Salvar mensagens
                        chat_data = db.get_chat(new_id)
                        chat_data["messages"].append({"role": "user", "content": query})
                        chat_data["messages"].append({"role": "assistant", "content": text})
                        
                        # Se houver imagem, não salvamos na DB JSON (limitação), mostramos só agora
                        # Em produção usaríamos S3 para imagens
                        db.update_chat(new_id, chat_data)
                        
                        st.session_state['current_chat_id'] = new_id
                        st.rerun()

    # CENÁRIO 2: CHAT EXISTENTE (ID Existe) - MOSTRAR CHAT E NOTAS
    else:
        chat_data = db.get_chat(current_id)
        if not chat_data: 
            st.error("Erro ao carregar chat.")
            st.session_state['current_chat_id'] = None
            st.rerun()
            
        # Header e Share
        c1, c2 = st.columns([3, 1])
        with c1: st.subheader(f"📂 {chat_data['title']}")
        with c2:
            with st.popover("📤 Partilhar"):
                em = st.text_input("Email"); 
                if st.button("Enviar"): db.share_chat(current_id, em); st.success("OK")

        # Layout Dividido
        col_chat, col_notes = st.columns([2, 1])
        
        with col_notes:
            st.markdown("### 📝 Notas")
            notes = st.text_area("Notas", value=chat_data.get("notes", ""), height=400, key="notes")
            if notes != chat_data.get("notes", ""):
                chat_data["notes"] = notes; db.update_chat(current_id, chat_data); st.toast("Salvo")
        
        with col_chat:
            for msg in chat_data["messages"]:
                st.chat_message(msg["role"]).write(msg["content"])
            
            if query := st.chat_input("Continuar..."):
                # Recuperar DF da memória (limitado neste demo) ou pedir re-upload se perdido
                df = st.session_state.get('temp_df')
                if df is None: 
                    st.warning("⚠️ Sessão expirou. Por favor carregue os dados novamente numa Nova Análise.")
                else:
                    st.chat_message("user").write(query)
                    chat_data["messages"].append({"role": "user", "content": query})
                    
                    with st.spinner("..."):
                        # Nota: Contexto/Persona aqui usam padrão se não guardados. 
                        # V20 ideal guardaria metadados no chat_data
                        code = ask_gemini(df, query, st.secrets["GEMINI_API_KEY"], "", st.session_state['temp_files'], "Data Scientist")
                        text, fig = execute_code(code, df)
                        
                        st.chat_message("assistant").write(text)
                        if fig: st.chat_message("assistant").pyplot(fig)
                        
                        chat_data["messages"].append({"role": "assistant", "content": text})
                        db.update_chat(current_id, chat_data)
            
            if chat_data["messages"]:
                pdf = create_pdf(chat_data)
                st.download_button("📄 PDF", pdf, "relatorio.pdf")

if __name__ == "__main__":
    if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
    if st.session_state["authenticated"]: main_app()
    else: login_page()