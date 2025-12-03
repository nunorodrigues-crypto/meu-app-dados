import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import google.generativeai as genai
import sys
from io import StringIO, BytesIO
import re
import json
import os
import uuid
from datetime import datetime
import hashlib
from streamlit_oauth import OAuth2Component
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

# --- 1. CONFIGURAÇÃO GERAL ---
st.set_page_config(page_title="AInsight Pro", page_icon="👁️", layout="wide")
HISTORY_FILE = "chat_database.json"

# --- 2. MOTOR DE DADOS BLINDADO ---
def clean_money_universal(val):
    if pd.isna(val) or val == '': return 0.0
    s = str(val).strip()
    s_clean = re.sub(r'[^\d.,-]', '', s)
    if not s_clean: return 0.0
    try:
        if ',' in s_clean and '.' in s_clean:
            if s_clean.rfind(',') > s_clean.rfind('.'): s_clean = s_clean.replace('.', '').replace(',', '.')
            else: s_clean = s_clean.replace(',', '')
        elif ',' in s_clean: s_clean = s_clean.replace(',', '.')
        return float(s_clean)
    except: return 0.0

def load_universal_file(uploaded_file):
    try:
        if uploaded_file.name.endswith('.xlsx') or uploaded_file.name.endswith('.xls'):
            return pd.read_excel(uploaded_file)
        encodings = ['utf-8', 'latin-1', 'iso-8859-1', 'cp1252']
        for enc in encodings:
            try:
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, sep=None, engine='python', encoding=enc)
            except: continue
        return None
    except: return None

def smart_clean_dataframe(df):
    df.dropna(how='all', inplace=True)
    df.dropna(how='all', axis=1, inplace=True)
    if len(df) > 1 and any("Unnamed" in str(c) for c in df.columns):
        row_0 = df.iloc[0]
        if row_0.notna().sum() > (len(df.columns) / 2):
            df.columns = row_0
            df = df.iloc[1:].reset_index(drop=True)
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                sample = df[col].astype(str).head().tolist()
                if any(re.search(r'\d', x) for x in sample):
                    temp_col = df[col].apply(clean_money_universal)
                    if (temp_col != 0).sum() > 0: df[col] = temp_col
            except: pass
            try: df[col] = pd.to_datetime(df[col]); continue
            except: pass
    return df

# --- 3. BACKEND ---
class HistoryManager:
    def __init__(self, username="system"):
        self.username = username
        self.load_db()

    def load_db(self):
        if not os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'w') as f: json.dump({"users": {}, "guest_tokens": {}}, f)
        try:
            with open(HISTORY_FILE, 'r') as f: self.full_db = json.load(f)
        except: self.full_db = {"users": {}, "guest_tokens": {}}
        
        if "users" not in self.full_db: self.full_db["users"] = {}
        if self.username not in self.full_db["users"]:
            self.user_data = {"chats": {}, "docs": {}, "datasets": {}, "notifications": []}
        else:
            self.user_data = self.full_db["users"][self.username]
            # Garante chaves
            for k in ["notifications", "datasets", "docs"]:
                if k not in self.user_data: self.user_data[k] = [] if k=="notifications" else {}

    def save_db(self):
        if self.username in self.full_db["users"]:
            self.full_db["users"][self.username] = self.user_data
            with open(HISTORY_FILE, 'w') as f: json.dump(self.full_db, f, indent=4, default=str)

    def create_doc(self, title, content=""):
        did = str(uuid.uuid4())
        self.user_data["docs"][did] = {"title": title, "content": content, "updated_at": datetime.now().isoformat()}
        self.save_db()

    def save_dataset_version(self, name, df):
        if "datasets" not in self.user_data: self.user_data["datasets"] = {}
        did = str(uuid.uuid4())
        djson = df.to_json(orient='split', date_format='iso')
        self.user_data["datasets"][did] = {
            "name": name, 
            "current_version": "v1",
            "commits": [{"version": "v1", "msg": "Upload via Análise", "ts": datetime.now().isoformat(), "data": djson}]
        }
        self.save_db()
        return did
    
    def delete_doc(self, did):
        if did in self.user_data["docs"]: del self.user_data["docs"][did]; self.save_db()

# --- 4. FUNÇÕES DE IA ---
def ask_gemini(df, query, api_key, persona):
    genai.configure(api_key=api_key)
    chosen_model = "gemini-pro"
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                if 'flash' in m.name: chosen_model = m.name; break
                elif '1.5-pro' in m.name: chosen_model = m.name
    except: pass

    summary = df.describe(include='all').to_string()
    prompt = f"""
    Atue como {persona}.
    RESUMO DADOS: {summary}
    PERGUNTA: "{query}"
    REGRAS:
    1. Responda SÓ com código Python em blocos ```python```.
    2. O dataframe chama-se 'df'. NÃO uses pd.read_csv.
    3. Importa pandas, matplotlib.pyplot, seaborn.
    4. Gráficos: plt.figure(figsize=(10,6)).
    5. NÃO uses 'datetime_is_numeric'.
    """
    try:
        model = genai.GenerativeModel(chosen_model)
        res = model.generate_content(prompt)
        match = re.search(r"```python(.*?)```", res.text, re.DOTALL)
        return match.group(1).strip() if match else res.text.replace("```", "").strip()
    except Exception as e: return f"print('Erro IA: {e}')"

def execute_code(code, df):
    try:
        code = code.replace(", datetime_is_numeric=True", "")
        old = sys.stdout; sys.stdout = StringIO()
        exec(code, {}, {'df': df, 'pd': pd, 'plt': plt, 'sns': sns, 'np': np})
        out = sys.stdout.getvalue(); sys.stdout = old
        fig = plt.gcf()
        if not plt.gca().has_data(): fig = None
        else: plt.clf()
        return out, fig
    except Exception as e:
        sys.stdout = sys.__stdout__
        return f"Erro Código: {e}", None

# --- 5. INTERFACE ---
def ui_login():
    st.markdown("## 🔐 AInsight Pro")
    u = st.text_input("User"); p = st.text_input("Pass", type="password")
    if st.button("Entrar"):
        db = HistoryManager(); usr = db.full_db["users"].get(u)
        if (u=="admin" and p=="123") or (usr and usr.get("password") == hashlib.sha256(p.encode()).hexdigest()):
            st.session_state.update({'auth': True, 'user': u})
            st.rerun()
        else: st.error("Erro.")

def ui_analysis_unified(db, key):
    st.title("📊 Análise IA")
    
    if 'df' not in st.session_state:
        with st.container(border=True):
            st.subheader("📂 Carregar Ficheiro")
            up = st.file_uploader("Arrasta Excel ou CSV aqui", type=['csv', 'xlsx', 'xls'])
            if up:
                with st.spinner("A processar..."):
                    df = load_universal_file(up)
                    if df is not None:
                        df = smart_clean_dataframe(df)
                        db.save_dataset_version(up.name, df)
                        st.session_state['df'] = df
                        st.session_state['filename'] = up.name
                        st.success("Carregado!"); st.rerun()
                    else: st.error("Erro ao ler.")
        return

    df = st.session_state['df']
    c1, c2 = st.columns([3, 1])
    c1.success(f"✅ {st.session_state.get('filename')} ({len(df)} linhas)")
    if c2.button("❌ Trocar"): del st.session_state['df']; st.rerun()
        
    persona = st.selectbox("Persona", ["Data Scientist", "CFO", "CMO", "COO"])
    
    if st.button(f"🚀 Relatório Automático", type="primary"):
        if not key: st.error("Falta API Key")
        else:
            with st.spinner("A gerar insights..."):
                code = ask_gemini(df, f"Gera relatório completo de {persona}", key, persona)
                txt, fig = execute_code(code, df)
                st.markdown(txt)
                if fig: st.pyplot(fig)
                db.create_doc(f"Relatório {persona}", txt)
                st.success("Guardado em Docs!")

    if q := st.chat_input("Pergunta..."):
        with st.chat_message("user"): st.write(q)
        with st.chat_message("assistant"):
            code = ask_gemini(df, q, key, persona)
            txt, fig = execute_code(code, df)
            st.write(txt)
            if fig: st.pyplot(fig)

def ui_docs(db):
    st.title("🧠 Docs")
    for did, d in db.user_data.get("docs", {}).items():
        with st.expander(f"📄 {d['title']}"):
            st.markdown(d['content'])
            if st.button("Apagar", key=did): db.delete_doc(did); st.rerun()

def main():
    if "auth" not in st.session_state: st.session_state["auth"] = False
    if not st.session_state["auth"]: ui_login()
    else:
        user = st.session_state["user"]
        db = HistoryManager(user)
        key = st.secrets.get("GEMINI_API_KEY", "")
        with st.sidebar:
            st.header("AInsight Pro"); st.caption(f"User: {user}")
            page = st.radio("Menu", ["📊 Análise IA", "🧠 Docs", "Sair"])
            if page == "Sair": st.session_state.clear(); st.rerun()
        
        if page == "📊 Análise IA": ui_analysis_unified(db, key)
        elif page == "🧠 Docs": ui_docs(db)

if __name__ == "__main__":
    main()