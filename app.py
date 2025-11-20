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
st.set_page_config(page_title="Data AI Enterprise", page_icon="🏢", layout="wide")

# --- GESTOR DE DADOS (DATABASE AVANÇADA) ---
HISTORY_FILE = "chat_database.json"

class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            # Nova estrutura com Workspaces
            init_db = {"users": {}, "guest_tokens": {}, "workspaces": {}}
            with open(HISTORY_FILE, 'w') as f: json.dump(init_db, f)
        
        with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        
        # Migração segura
        if "workspaces" not in self.full_db: self.full_db["workspaces"] = {}
        
        # Inicializar user
        if self.username not in self.full_db["users"]:
            self.full_db["users"][self.username] = {
                "chats": {}, 
                "plan": "free", # free ou pro
                "workspaces": [] # IDs dos workspaces
            }
        
        self.user_data = self.full_db["users"][self.username]
        self.user_chats = self.user_data["chats"]

    def save_db(self):
        self.full_db["users"][self.username] = self.user_data
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

    # --- TOKEN CONVIDADO ---
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

    # --- GESTÃO DE CHATS & NOTAS ---
    def create_chat(self, first_message, workspace_id=None):
        chat_id = str(uuid.uuid4())
        title = first_message[:30] + "..." if len(first_message) > 30 else first_message
        
        chat_obj = {
            "title": title, 
            "created_at": datetime.now().isoformat(), 
            "pinned": False, 
            "messages": [],
            "notes": "", # <--- NOVO: Bloco de Notas
            "owner": self.username,
            "shared_with": [], # <--- NOVO: Lista de emails
            "workspace_id": workspace_id
        }

        # Se for num workspace, salva lá. Se não, salva no user.
        if workspace_id and workspace_id in self.full_db["workspaces"]:
            self.full_db["workspaces"][workspace_id]["chats"][chat_id] = chat_obj
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
        else:
            self.user_chats[chat_id] = chat_obj
            self.save_db()
            
        return chat_id

    def get_chat(self, chat_id):
        # 1. Procura nos meus chats
        if chat_id in self.user_chats: return self.user_chats[chat_id]
        
        # 2. Procura em chats partilhados comigo (Scan Global - Ineficiente mas funcional para demo)
        for u_email, u_data in self.full_db["users"].items():
            if chat_id in u_data["chats"]:
                chat = u_data["chats"][chat_id]
                if self.username in chat.get("shared_with", []):
                    return chat
        
        # 3. Procura em Workspaces
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"]:
                # Verificar se sou membro
                if self.username in wdata["members"] or self.username == wdata["owner"]:
                    return wdata["chats"][chat_id]
        return None

    def update_chat(self, chat_id, chat_data):
        # Encontra onde o chat está e atualiza
        if chat_id in self.user_chats:
            self.user_chats[chat_id] = chat_data
            self.save_db()
            return
        
        # Check workspaces
        for wid, wdata in self.full_db["workspaces"].items():
            if chat_id in wdata["chats"]:
                self.full_db["workspaces"][wid]["chats"][chat_id] = chat_data
                with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
                return
        
        # Check shared (dono original)
        for u_email, u_data in self.full_db["users"].items():
             if chat_id in u_data["chats"]:
                 self.full_db["users"][u_email]["chats"][chat_id] = chat_data
                 with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
                 return

    def share_chat(self, chat_id, target_email):
        chat = self.get_chat(chat_id)
        if chat and target_email not in chat["shared_with"]:
            chat["shared_with"].append(target_email)
            self.update_chat(chat_id, chat)
            return True
        return False

    # --- GESTÃO DE WORKSPACES (CORPORATIVO) ---
    def upgrade_plan(self):
        self.user_data["plan"] = "pro"
        self.save_db()

    def create_workspace(self, name):
        if self.user_data["plan"] != "pro": return False, "Requer Plano PRO"
        
        ws_id = str(uuid.uuid4())
        self.full_db["workspaces"][ws_id] = {
            "name": name,
            "owner": self.username,
            "members": [self.username],
            "chats": {}
        }
        self.user_data["workspaces"].append(ws_id)
        
        # Save Global
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
        return True, "Criado com sucesso"

    def add_member_to_workspace(self, ws_id, email):
        if ws_id in self.full_db["workspaces"]:
            ws = self.full_db["workspaces"][ws_id]
            if email not in ws["members"]:
                ws["members"].append(email)
                # Adicionar referência no user (se existir)
                if email in self.full_db["users"]:
                    if ws_id not in self.full_db["users"][email].get("workspaces", []):
                         self.full_db["users"][email].setdefault("workspaces", []).append(ws_id)
                
                with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
                return True
        return False

# --- FUNÇÕES DE DADOS (IGUAIS) ---
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
    pdf.cell(200, 10, txt=f"Relatorio: {chat_data['title']}", ln=1, align='C'); pdf.ln(10)
    
    # Adicionar Notas
    pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, txt="NOTAS / RESUMO", ln=1)
    pdf.set_font("Arial", size=10)
    notes = chat_data.get("notes", "").encode('latin-1', 'replace').decode('latin-1')
    pdf.multi_cell(0, 10, txt=notes); pdf.ln(10)

    # Adicionar Chat
    pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, txt="HISTORICO DE CHAT", ln=1)
    pdf.set_font("Arial", size=10)
    for msg in chat_data["messages"]:
        text = msg["content"].replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{msg['role']}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=text); pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- UI LOGIN E SUPORTE ---
def generate_qr_code(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=4); qr.add_data(data); qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white'); buf = BytesIO(); img.save(buf); return buf.getvalue()

def login_page():
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<h1 style='text-align: center;'>Data AI Enterprise</h1>", unsafe_allow_html=True)
        with st.form("login"):
            u = st.text_input("User"); p = st.text_input("Pass", type="password")
            if st.form_submit_button("Entrar", use_container_width=True):
                if u == st.secrets.get("ADMIN_USER", "admin") and p == st.secrets.get("ADMIN_PASSWORD", "123"):
                    st.session_state['authenticated'] = True; st.session_state['username'] = u; st.rerun()
                else: st.error("Erro.")
        
        if "GOOGLE_CLIENT_ID" in st.secrets:
            try:
                oauth2 = OAuth2Component(st.secrets["GOOGLE_CLIENT_ID"], st.secrets["GOOGLE_CLIENT_SECRET"], "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token", "https://www.googleapis.com/oauth2/v1/tokeninfo", "https://www.googleapis.com/oauth2/v1/userinfo")
                res = oauth2.authorize_button("Google Login", "https://www.google.com.tw/favicon.ico", st.secrets["GOOGLE_REDIRECT_URI"], "email", key="g_btn")
                if res and "token" in res:
                    st.session_state['authenticated'] = True; st.session_state['username'] = "Google User"; st.rerun()
            except: pass

# --- UI PRINCIPAL ---
def main_app():
    user = st.session_state.get('username', 'User')
    db = HistoryManager(user)
    
    # --- SIDEBAR AVANÇADA ---
    with st.sidebar:
        st.title(f"👤 {user}")
        
        # SELEÇÃO DE CONTEXTO (Pessoal vs Workspaces)
        context_mode = st.radio("Modo:", ["Pessoal", "Workspaces"], horizontal=True)
        
        if context_mode == "Workspaces":
            if db.user_data["plan"] == "free":
                st.warning("🔒 Recurso Premium")
                if st.button("💎 Upgrade para PRO"):
                    db.upgrade_plan()
                    st.success("Bem-vindo ao PRO!"); time.sleep(1); st.rerun()
            else:
                # Listar Workspaces
                my_ws = db.full_db["workspaces"]
                # Filtrar onde sou membro
                user_ws = {k:v for k,v in my_ws.items() if user in v["members"]}
                
                selected_ws_id = st.selectbox("Escolher Workspace", options=list(user_ws.keys()), format_func=lambda x: user_ws[x]["name"])
                
                if selected_ws_id:
                    st.info(f"Membros: {len(user_ws[selected_ws_id]['members'])}")
                    new_member = st.text_input("Adicionar Colega (Email)")
                    if st.button("Convidar"):
                        if db.add_member_to_workspace(selected_ws_id, new_member): st.success("Adicionado!")
                        else: st.error("Erro.")
                        
                with st.expander("Criar Novo Workspace"):
                    new_ws_name = st.text_input("Nome da Empresa")
                    if st.button("Criar"):
                        ok, msg = db.create_workspace(new_ws_name)
                        if ok: st.rerun()
                        else: st.error(msg)

        st.markdown("---")
        if st.button("➕ Nova Análise", use_container_width=True): st.session_state['current_chat_id'] = None; st.rerun()
        
        # LISTAGEM DE CHATS
        st.caption("MEUS CHATS")
        chats_to_show = db.user_chats
        
        # Se estiver em Workspace, mostra chats do workspace
        if context_mode == "Workspaces" and db.user_data["plan"] == "pro" and 'selected_ws_id' in locals() and selected_ws_id:
             chats_to_show = db.full_db["workspaces"][selected_ws_id]["chats"]

        for cid, d in sorted(chats_to_show.items(), key=lambda x:x[1]['created_at'], reverse=True):
            if st.button(f"💬 {d['title']}", key=cid): st.session_state['current_chat_id']=cid; st.rerun()
            
        # CHATS PARTILHADOS COMIGO
        st.caption("PARTILHADOS COMIGO")
        # Procura global (simples)
        for uid, udata in db.full_db["users"].items():
            if uid == user: continue
            for cid, cdata in udata["chats"].items():
                if user in cdata.get("shared_with", []):
                    if st.button(f"🔗 {cdata['title']} (de {uid})", key=f"shared_{cid}"):
                        st.session_state['current_chat_id']=cid; st.rerun()

        st.markdown("---")
        if st.button("🚪 Sair"): st.session_state['authenticated']=False; st.rerun()

    # --- ÁREA CENTRAL ---
    current_id = st.session_state.get('current_chat_id')
    
    if current_id:
        chat_data = db.get_chat(current_id)
        if not chat_data: st.error("Chat não encontrado"); return

        # HEADER DO CHAT
        c1, c2 = st.columns([3, 1])
        with c1: st.subheader(f"📂 {chat_data['title']}")
        with c2:
            # BOTÃO SHARE
            with st.popover("📤 Partilhar"):
                share_email = st.text_input("Email do colega")
                if st.button("Enviar Acesso"):
                    if db.share_chat(current_id, share_email): st.success("Partilhado!")
                    else: st.error("Erro.")

        # LAYOUT DIVIDIDO: CHAT vs NOTAS
        col_chat, col_notes = st.columns([2, 1])
        
        with col_notes:
            st.markdown("### 📝 Bloco de Notas")
            st.markdown("Anote conclusões aqui. Fica salvo com a análise.")
            # Notas Persistentes
            notes = st.text_area("Escreva aqui...", value=chat_data.get("notes", ""), height=400, key="notes_area")
            
            # Auto-Save das notas (ao clicar fora ou cmd+enter)
            if notes != chat_data.get("notes", ""):
                chat_data["notes"] = notes
                db.update_chat(current_id, chat_data)
                st.toast("Notas guardadas!")

        with col_chat:
            # SETUP (Só aparece se vazio)
            if not chat_data["messages"]:
                with st.expander("⚙️ Configuração", expanded=True):
                    if "GEMINI_API_KEY" in st.secrets: api_key = st.secrets["GEMINI_API_KEY"]
                    else: api_key = st.text_input("API Key", type="password")
                    c_a, c_b = st.columns(2); persona = c_a.selectbox("Persona", ["Data Scientist", "CFO"]); context = c_b.text_area("Contexto")
                    up_files = st.file_uploader("Ficheiros", accept_multiple_files=True)
                    if up_files: 
                        df, f_names = smart_merge(up_files)
                        if df is not None: st.success("Dados OK")
                        st.session_state['temp_df'] = df # Cache simples
                        st.session_state['temp_names'] = f_names

            # HISTÓRICO
            for msg in chat_data["messages"]:
                st.chat_message(msg["role"]).write(msg["content"])

            # INPUT
            if query := st.chat_input("Pergunta..."):
                # Tenta pegar dados da sessão (limitação: ficheiros não persistem no refresh neste demo)
                df = st.session_state.get('temp_df')
                f_names = st.session_state.get('temp_names', [])
                
                # Se não houver dados na RAM, avisa (em produção usaria S3/Database blob)
                if df is None and not chat_data["messages"]: 
                    st.error("Por favor carregue os dados novamente (sessão nova).")
                else:
                    # Se já tiver mensagens, pode continuar conversa (assumindo que a IA lembra do contexto ou recarrega)
                    # Nota: A IA "Gemini" precisa do DF a cada chamada no nosso código atual. 
                    # Fix rápido: Se não tem DF, pede upload.
                    if df is None: st.warning("⚠️ Recarregue o Excel para continuar a análise.")
                    else:
                        # Guardar Msg User
                        chat_data["messages"].append({"role": "user", "content": query})
                        db.update_chat(current_id, chat_data)
                        st.chat_message("user").write(query)
                        
                        with st.spinner("..."):
                            code = ask_gemini(df, query, api_key, context if 'context' in locals() else "", f_names, persona if 'persona' in locals() else "Data Scientist")
                            text, fig = execute_code(code, df)
                            
                            chat_data["messages"].append({"role": "assistant", "content": text})
                            db.update_chat(current_id, chat_data)
                            
                            st.chat_message("assistant").write(text)
                            if fig: st.chat_message("assistant").pyplot(fig)

    else:
        st.info("Selecione ou crie uma análise na barra lateral.")

if __name__ == "__main__":
    if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
    if st.session_state["authenticated"]: main_app()
    else: login_page()