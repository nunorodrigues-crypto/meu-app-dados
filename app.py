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
import qrcode # <--- Nova biblioteca

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Data AI Hub", page_icon="⚡", layout="wide")

# --- GESTOR DE HISTÓRICO (MANTIDO) ---
HISTORY_FILE = "chat_database.json"
class HistoryManager:
    def __init__(self, username):
        self.username = username
        self.load_db()
    def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'w') as f: json.dump({}, f)
        with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        if self.username not in self.full_db: self.full_db[self.username] = {}
        self.user_chats = self.full_db[self.username]
    def save_db(self):
        self.full_db[self.username] = self.user_chats
        with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)
    def create_chat(self, first_message):
        chat_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        title = first_message[:30] + "..." if len(first_message) > 30 else first_message
        self.user_chats[chat_id] = {"title": title, "created_at": timestamp, "pinned": False, "messages": []}
        self.save_db()
        return chat_id
    def add_message(self, chat_id, role, content):
        if chat_id not in self.user_chats: return
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
        self.user_chats[chat_id]["messages"].append(msg)
        self.save_db()
    def get_messages(self, chat_id): return self.user_chats.get(chat_id, {}).get("messages", [])
    def toggle_pin(self, chat_id):
        if chat_id in self.user_chats:
            self.user_chats[chat_id]["pinned"] = not self.user_chats[chat_id].get("pinned", False)
            self.save_db(); st.rerun()
    def rename_chat(self, chat_id, new_name):
        if chat_id in self.user_chats:
            self.user_chats[chat_id]["title"] = new_name
            self.save_db(); st.rerun()
    def delete_chat(self, chat_id):
        if chat_id in self.user_chats:
            del self.user_chats[chat_id]; self.save_db(); return True
        return False

# --- FUNÇÕES DE DADOS (MANTIDAS) ---
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
        response.raise_for_status()
        try: return pd.read_csv(StringIO(response.text)), "Link_Google_CSV"
        except: return pd.read_excel(BytesIO(response.content)), "Link_Excel"
    except Exception as e: return None, str(e)

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
                    cols = {c: f"{prefix}_{c}" for c in clean_df.columns if c != 'DATA_FUSAO'}
                    clean_df.rename(columns=cols, inplace=True)
                    dataframes.append(clean_df)
                    file_names.append(file.name)
            except: pass
    if url_df is not None:
        clean_df, tem_data = clean_individual_df(url_df, url_name)
        if tem_data:
            cols = {c: f"CLOUD_{c}" for c in clean_df.columns if c != 'DATA_FUSAO'}
            clean_df.rename(columns=cols, inplace=True)
            dataframes.append(clean_df)
            file_names.append(url_name)
    if not dataframes: return None, "Erro ou sem dados."
    if len(dataframes) == 1: return dataframes[0], file_names
    try:
        df_final = reduce(lambda left, right: pd.merge(left, right, on='DATA_FUSAO', how='outer'), dataframes)
        return df_final.sort_values('DATA_FUSAO').fillna(0), file_names
    except Exception as e: return None, str(e)

def ask_gemini(df, query, api_key, context, file_list, persona):
    genai.configure(api_key=api_key)
    persona_prompt = "Atue como Data Scientist."
    if persona == "CFO": persona_prompt = "Atue como CFO focado em ROI."
    elif persona == "CMO": persona_prompt = "Atue como CMO focado em crescimento."
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = f"""
    {persona_prompt}
    CONTEXTO: {context}
    DADOS ORIGEM: {", ".join(file_list)}
    ESTRUTURA: {df.dtypes.to_string()}
    PERGUNTA: "{query}"
    REGRAS: Responda APENAS com código Python (```python). Use 'df', print(), plt.figure().
    """
    response = model.generate_content(prompt)
    match = re.search(r"```python(.*?)```", response.text, re.DOTALL)
    return match.group(1).strip() if match else response.text.replace("```", "").strip()

def execute_code(code, df):
    try:
        import numpy as np
        old_stdout = sys.stdout
        redirected_output = sys.stdout = StringIO()
        local_vars = {'df': df, 'plt': plt, 'sns': sns, 'pd': pd, 'np': np}
        exec(code, {}, local_vars)
        sys.stdout = old_stdout
        return redirected_output.getvalue(), plt
    except Exception as e: return f"Erro: {e}", None

def create_pdf(chat_history):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Relatorio AI Data Hub", ln=1, align='C')
    pdf.ln(10)
    for msg in chat_history:
        role = "IA" if msg["role"] == "assistant" else "USER"
        text = msg["content"].replace("€", "EUR").encode('latin-1', 'replace').decode('latin-1')
        pdf.set_font("Arial", 'B', 10); pdf.cell(0, 10, txt=f"[{role}]", ln=1)
        pdf.set_font("Arial", size=10); pdf.multi_cell(0, 10, txt=text); pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- FUNÇÕES DE MAGIC LINK E QR ---
def check_magic_link():
    """Verifica se existe um token na URL para login automático."""
    # Obtém parametros da URL
    params = st.query_params
    if "access_token" in params:
        token = params["access_token"]
        correct_token = st.secrets.get("MAGIC_LINK_TOKEN", "token_secreto_123")
        
        if token == correct_token:
            st.session_state['authenticated'] = True
            st.session_state['username'] = "Magic User"
            st.success("⚡ Login Mágico Autorizado!")
            return True
        else:
            st.error("Link Mágico Inválido ou Expirado.")
    return False

def generate_qr_code(link):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill='black', back_color='white')
    # Converter para bytes para mostrar no streamlit
    buf = BytesIO()
    img.save(buf)
    return buf.getvalue()

# --- PÁGINAS ---
def login_page():
    st.markdown("<h2 style='text-align: center;'>🔐 Acesso Enterprise</h2>", unsafe_allow_html=True)
    
    # Primeiro, verifica se veio pelo Magic Link
    if check_magic_link():
        time.sleep(1)
        st.rerun()

    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        tab_pass, tab_magic = st.tabs(["🔑 Password", "⚡ Acesso Rápido (QR)"])
        
        with tab_pass:
            u = st.text_input("User"); p = st.text_input("Pass", type="password")
            if st.button("Entrar", use_container_width=True):
                ru = st.secrets.get("ADMIN_USER", "admin")
                rp = st.secrets.get("ADMIN_PASSWORD", "123")
                if u == ru and p == rp:
                    st.session_state['authenticated'] = True
                    st.session_state['username'] = u
                    st.rerun()
                else: st.error("Erro.")

        with tab_magic:
            st.info("Use este QR Code para login instantâneo no telemóvel ou envie o link por email.")
            
            # Obter URL base (em produção seria o teu link streamlit)
            # Como não sabemos o link exato dinamicamente, usamos o secrets ou pedimos input
            base_url = st.secrets.get("APP_URL", "https://teu-app.streamlit.app")
            token = st.secrets.get("MAGIC_LINK_TOKEN", "token_secreto_123")
            
            full_magic_link = f"{base_url}?access_token={token}"
            
            # Gerar QR
            img_bytes = generate_qr_code(full_magic_link)
            st.image(img_bytes, caption="Scan para entrar", width=200)
            
            st.code(full_magic_link, language="text")
            st.caption("Copie este link e envie ao cliente para acesso sem senha.")

def main_app():
    user = st.session_state.get('username', 'User')
    db = HistoryManager(user)

    with st.sidebar:
        st.title("🗂️ Histórico")
        if st.button("➕ Nova Análise", use_container_width=True):
            st.session_state['current_chat_id'] = None; st.rerun()
        st.markdown("---")
        
        all_chats = db.user_chats
        pinned = {k:v for k,v in all_chats.items() if v.get('pinned')}
        recent = {k:v for k,v in all_chats.items() if not v.get('pinned')}
        
        def draw_list(cdict, lbl):
            if cdict:
                st.caption(lbl)
                for cid, d in sorted(cdict.items(), key=lambda x:x[1]['created_at'], reverse=True):
                    bt = "primary" if st.session_state.get('current_chat_id')==cid else "secondary"
                    if st.button(f"{'📌' if lbl=='Fixados' else '💬'} {d['title']}", key=cid, type=bt, use_container_width=True):
                        st.session_state['current_chat_id']=cid; st.rerun()
        draw_list(pinned, "Fixados")
        draw_list(recent, "Recentes")
        st.markdown("---")
        if st.button("🚪 Logout"): 
            st.session_state['authenticated']=False
            # Limpar query params ao sair para não fazer auto-login imediato
            st.query_params.clear()
            st.rerun()

    # ÁREA PRINCIPAL
    current_id = st.session_state.get('current_chat_id')
    if current_id:
        chat_data = all_chats[current_id]
        c1, c2, c3 = st.columns([1, 1, 4])
        with c1: 
            if st.button(f"📌 {'Unpin' if chat_data.get('pinned') else 'Pin'}"): db.toggle_pin(current_id)
        with c2: 
            if st.button("🗑️"): 
                if db.delete_chat(current_id): st.session_state['current_chat_id']=None; st.rerun()
        messages = db.get_messages(current_id)
    else:
        messages = []

    with st.expander("⚙️ Configuração & Fontes de Dados", expanded=not messages):
        if "GEMINI_API_KEY" in st.secrets: api_key = st.secrets["GEMINI_API_KEY"]
        else: api_key = st.text_input("API Key", type="password")
        
        c_a, c_b = st.columns(2)
        persona = c_a.selectbox("Persona", ["Data Scientist", "CFO", "CMO"])
        context = c_b.text_area("Contexto", height=40)
        
        tab_up, tab_link = st.tabs(["📂 Upload Arquivo", "🔗 Link Cloud (Google Drive/Sheets)"])
        uploaded_files = None; url_df = None; url_name = None

        with tab_up: uploaded_files = st.file_uploader("Arraste ficheiros do PC", accept_multiple_files=True)
        with tab_link:
            url_input = st.text_input("Cole o URL aqui:")
            if url_input:
                with st.spinner("A baixar da nuvem..."):
                    url_df, url_name = load_from_url(url_input)
                    if url_df is not None: st.success("✅ Ficheiro lido!")
                    else: st.error(f"Erro: {url_name}")

    # FUSÃO
    df_final = None
    if uploaded_files or url_df is not None:
        df_final, file_names = smart_merge(uploaded_files, url_df, url_name)
        if df_final is not None: st.success(f"Dados ativos: {', '.join(file_names)}")

    for msg in messages:
        st.chat_message(msg["role"]).write(msg["content"])

    if query := st.chat_input("Pergunta..."):
        if not api_key or df_final is None: st.error("Faltam dados ou chave.")
        else:
            if not current_id: current_id = db.create_chat(query); st.session_state['current_chat_id']=current_id
            st.chat_message("user").write(query); db.add_message(current_id, "user", query)
            with st.spinner("A analisar..."):
                code = ask_gemini(df_final, query, api_key, context, file_names, persona)
                text, fig = execute_code(code, df_final)
                st.chat_message("assistant").write(text); db.add_message(current_id, "assistant", text)
                if fig and fig.get_fignums(): st.chat_message("assistant").pyplot(fig)

    # Se houver histórico, mostrar botão PDF em baixo
    if messages:
        st.markdown("---")
        col_pdf, _ = st.columns([1,4])
        with col_pdf:
            pdf_bytes = create_pdf(messages)
            st.download_button("📄 Download Relatório PDF", pdf_bytes, "relatorio.pdf", "application/pdf")

if __name__ == "__main__":
    if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
    # Se autenticado ou se magic link válido, entra
    if st.session_state["authenticated"]: main_app()
    else: login_page()